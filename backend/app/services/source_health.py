"""数据源健康中心。

统一记录「行情 / 公告 / 财务 / 新闻」上游数据源的连通性、数据时间与
连续失败状态, 供设置页展示和主备路由判定。

设计边界:
- 探测是只读旁路: 只取最小样本, 不写业务表、不进入情/管道热路径。
- 状态落 ``<DATA_DIR>/user_data/source_health.json``, 原子写, 不含密钥。
- 单源失败只影响自己的状态, 不阻断其他源的探测。
- 主备切换是「路由级」的: 只在一个轮询周期结束后整体换源, 不在单次请求内混源。
"""
from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from datetime import time as dt_time
from pathlib import Path
from typing import Any

import polars as pl

from app.market_time import CN_TZ, cn_now, in_continuous_session
from app.services.fs_utils import atomic_write_text

logger = logging.getLogger(__name__)

STATE_VERSION = 1
HISTORY_LIMIT = 20
_LOCK = threading.RLock()

CAP_LABELS = {
    "realtime": "实时行情",
    "daily": "日K",
    "minute": "分钟K",
    "financial": "财务数据",
    "announcements": "公告",
    "news": "新闻",
}
CAP_FIELDS = {
    "realtime": "realtime_data_provider",
    "daily": "daily_data_provider",
    "minute": "minute_data_provider",
    "financial": "financial_data_provider",
}
# 仅这些能力参与自动主备切换; 日K/分钟涉及落盘口径, 先只告警。
FALLBACK_CAPS = ("realtime", "financial")

# 探测阈值: 超过即 degraded。交易/非交易时段由调度层门控, 这里只判数据时间。
MAX_LAG = {
    "realtime": 120.0,
    "minute": 600.0,
    "daily": 36 * 3600.0,
    "financial": 120 * 24 * 3600.0,
    "announcements": 36 * 3600.0,
    "news": 7 * 24 * 3600.0,
}
_FAIL_DOWN = 3
_FAIL_DEGRADED = 1
_COOLDOWN_SECONDS = 300

_PROBE_SYMBOL = "600519.SH"
_INFO_SYMBOL = "600519"
_INFO_NAME = "贵州茅台"

_STATE_FILE = Path("user_data") / "source_health.json"


# ---------------------------------------------------------------------------
# 状态存取
# ---------------------------------------------------------------------------

def _state_path(data_dir: str | Path) -> Path:
    return Path(data_dir) / _STATE_FILE


def _empty_state() -> dict[str, Any]:
    return {"version": STATE_VERSION, "updated_at": None, "targets": {}, "switches": []}


def load_state(data_dir: str | Path) -> dict[str, Any]:
    path = _state_path(data_dir)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _empty_state()
    except (OSError, ValueError, TypeError) as exc:
        logger.warning("source health state unreadable: %s", exc)
        return _empty_state()
    if not isinstance(raw, dict) or raw.get("version") != STATE_VERSION:
        return _empty_state()
    raw.setdefault("targets", {})
    raw.setdefault("switches", [])
    return raw


def _save_state(data_dir: str | Path, state: dict[str, Any]) -> None:
    path = _state_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(state, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------------------
# 目标注册
# ---------------------------------------------------------------------------

def _market_targets(data_dir: str | Path) -> list[dict[str, Any]]:
    """按能力矩阵生成「能力 × 已声明源」探测目标。

    只纳入 available 的源; pending (依赖/Key 未就绪) 不探测, 避免每次
    都对着不可用源打请求。
    """
    from app.data_providers.capabilities import build_capability_matrix
    from app.services import preferences
    from app.tickflow import policy

    current = {
        "realtime_data_provider": preferences.get_realtime_data_provider(),
        "daily_data_provider": preferences.get_daily_data_provider(),
        "minute_data_provider": preferences.get_minute_data_provider(),
        "financial_data_provider": preferences.get_financial_provider(),
    }
    matrix = build_capability_matrix(current, tickflow_tier=policy.base_tier_name())
    targets: list[dict[str, Any]] = []
    for cap in matrix.get("capabilities", []):
        cap_id = cap.get("id")
        if cap_id not in CAP_FIELDS:
            continue
        for source in cap.get("candidates") or []:
            if not source.get("available", True):
                continue
            targets.append({
                "id": f"{cap_id}.{source['name']}",
                "category": cap_id,
                "provider": source["name"],
                "display": source.get("display") or source["name"],
                "kind": "market",
                "dataset": cap_id,
            })
    return targets


def _info_targets() -> list[dict[str, Any]]:
    return [
        {"id": "announcements.cninfo", "category": "announcements", "provider": "cninfo",
         "display": "巨潮资讯", "kind": "info", "dataset": "announcements"},
        {"id": "announcements.em", "category": "announcements", "provider": "em",
         "display": "东方财富", "kind": "info", "dataset": "announcements"},
        {"id": "news.ths", "category": "news", "provider": "ths",
         "display": "同花顺", "kind": "info", "dataset": "news"},
        {"id": "news.em", "category": "news", "provider": "em",
         "display": "东方财富", "kind": "info", "dataset": "news"},
    ]


def targets(data_dir: str | Path) -> list[dict[str, Any]]:
    return _market_targets(data_dir) + _info_targets()


# ---------------------------------------------------------------------------
# 数据时间解析
# ---------------------------------------------------------------------------

def _as_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=CN_TZ)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=CN_TZ)
    if isinstance(value, (int, float)):
        value = float(value)
        if value > 10_000_000_000:
            value /= 1000.0
        try:
            return datetime.fromtimestamp(value, CN_TZ)
        except (OSError, OverflowError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y%m%d"):
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        else:
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=CN_TZ)


def _max_datetime(values: list[Any]) -> datetime | None:
    parsed = [v for v in (_as_datetime(x) for x in values) if v is not None]
    return max(parsed) if parsed else None


def _frame_time(df: pl.DataFrame, columns: tuple[str, ...]) -> datetime | None:
    if df.is_empty():
        return None
    for column in columns:
        if column in df.columns:
            value = _max_datetime(df[column].to_list())
            if value is not None:
                return value
    return None


# ---------------------------------------------------------------------------
# 探测实现
# ---------------------------------------------------------------------------

@dataclass
class ProbeOutcome:
    ok: bool
    data_time: datetime | None = None
    rows: int = 0
    detail: str = ""
    error: str = ""
    latency_ms: int = 0
    extra: dict[str, Any] = field(default_factory=dict)


def _call_with_deadline(fn: Callable[[], Any], timeout_s: float = 12.0) -> Any:
    from app.tickflow.request_guard import _sdk_call_with_deadline

    return _sdk_call_with_deadline(fn, timeout_s=timeout_s)


def _provider(name: str):
    if name == "tickflow":
        from app.data_providers.tickflow_provider import TickFlowProvider

        return TickFlowProvider()
    from app.data_providers import custom as custom_sources

    return custom_sources.get_provider(name)


def _probe_market(target: dict[str, Any]) -> ProbeOutcome:
    cap = target["category"]
    provider_name = target["provider"]
    now = cn_now()
    started = time.perf_counter()
    provider = None
    try:
        provider = _provider(provider_name)
        if cap == "realtime":
            # TickFlow 支持按标的拉取; 自定义源契约是整表快照, 不接受 symbols。
            if provider_name == "tickflow":
                data = _call_with_deadline(lambda: provider.get_realtime(symbols=[_PROBE_SYMBOL]))
            else:
                data = _call_with_deadline(provider.get_realtime)
            if isinstance(data, pl.DataFrame):
                rows = len(data)
                data_time = _frame_time(data, ("quote_ts", "timestamp", "datetime"))
            else:
                records = list(data or [])
                rows = len(records)
                data_time = _max_datetime([
                    item.get("quote_ts") or item.get("timestamp") or item.get("datetime") or item.get("time")
                    for item in records if isinstance(item, dict)
                ])
            return ProbeOutcome(
                ok=True,
                rows=rows,
                data_time=data_time,
                detail="实时快照",
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
        if cap == "daily":
            start = now - timedelta(days=10)
            df = _call_with_deadline(
                lambda: provider.get_daily([_PROBE_SYMBOL], start, now, "stock")
            )
            return ProbeOutcome(
                ok=True,
                rows=len(df) if isinstance(df, pl.DataFrame) else 0,
                data_time=_frame_time(df, ("date", "trade_date")),
                detail="最近 10 个自然日",
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
        if cap == "minute":
            start = now - timedelta(minutes=30)
            df = _call_with_deadline(
                lambda: provider.get_minute(
                    [_PROBE_SYMBOL], start, now, asset_type="stock", freq="1m",
                )
            )
            return ProbeOutcome(
                ok=True,
                rows=len(df) if isinstance(df, pl.DataFrame) else 0,
                data_time=_frame_time(df, ("datetime", "timestamp")),
                detail="最近 30 分钟",
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
        if cap == "financial":
            if provider_name == "tickflow":
                from app.tickflow.client import get_client

                data = _call_with_deadline(
                    lambda: get_client().financials.metrics(
                        [_PROBE_SYMBOL], latest=True, as_dataframe=False,
                    )
                )
                rows = data.get(_PROBE_SYMBOL) if isinstance(data, dict) else data
                if isinstance(rows, pl.DataFrame):
                    data_time = _frame_time(rows, ("period_end", "announce_date"))
                    count = len(rows)
                else:
                    data_time = _max_datetime([
                        item.get("period_end") or item.get("end_date") or item.get("report_date")
                        for item in (rows or [])
                        if isinstance(item, dict)
                    ])
                    count = len(rows or []) if isinstance(rows, (list, tuple)) else 0
            else:
                df = _call_with_deadline(
                    lambda: provider.get_financials("metrics", [_PROBE_SYMBOL], latest_only=True)
                )
                data_time = _frame_time(df, ("period_end", "announce_date"))
                count = len(df) if isinstance(df, pl.DataFrame) else 0
            return ProbeOutcome(
                ok=True,
                rows=count,
                data_time=data_time,
                detail="最新一期指标",
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
        return ProbeOutcome(ok=False, error=f"未知能力: {cap}")
    except Exception as exc:  # noqa: BLE001 - 探测失败要转成状态而不是抛给调度器
        return ProbeOutcome(
            ok=False,
            error=f"{type(exc).__name__}: {exc}"[:300],
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
    finally:
        # 自定义源是进程内共享的 ManagedProvider, close 会把它标记为停止;
        # 只关闭本轮新建的 TickFlow provider。
        if provider_name == "tickflow":
            close = getattr(provider, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:  # noqa: BLE001
                    logger.debug("close probe provider failed", exc_info=True)


def _probe_cninfo(data_dir: str | Path) -> ProbeOutcome:
    from app.custom.info_hub.sources import fetch_cninfo_day

    started = time.perf_counter()
    today = cn_now().date().isoformat()
    rows = _call_with_deadline(lambda: fetch_cninfo_day(today, max_pages=1, page_size=10))
    data_time = _max_datetime([
        f"{row.get('ann_date') or ''} {row.get('ann_time') or '00:00'}"
        for row in rows or []
        if row.get("ann_date")
    ])
    return ProbeOutcome(
        ok=True,
        rows=len(rows or []),
        data_time=data_time,
        detail="当日公告第一页",
        latency_ms=int((time.perf_counter() - started) * 1000),
    )


def _probe_em_announcements() -> ProbeOutcome:
    from app.custom.info_hub.sources import fetch_em_day

    started = time.perf_counter()
    today = cn_now().date().isoformat()
    rows = _call_with_deadline(lambda: fetch_em_day(today, max_pages=1, page_size=10))
    data_time = _max_datetime([row.get("notice_date") for row in rows or []])
    return ProbeOutcome(
        ok=True,
        rows=len(rows or []),
        data_time=data_time,
        detail="当日公告第一页",
        latency_ms=int((time.perf_counter() - started) * 1000),
    )


def _probe_news(source: str, data_dir: str | Path) -> ProbeOutcome:
    from app.custom.info_hub import config
    from app.custom.info_hub.sources import _fetch_em_news, _fetch_ths_news

    started = time.perf_counter()
    cfg = dict(config.load_config(data_dir)["sources"]["news"])
    cfg["max_pages"] = 1
    cfg["page_size"] = 10
    since = (cn_now().date() - timedelta(days=7)).isoformat()
    if source == "ths":
        rows = _call_with_deadline(
            lambda: _fetch_ths_news(_INFO_SYMBOL, _INFO_NAME, [], since, cfg)
        )
    else:
        rows = _call_with_deadline(
            lambda: _fetch_em_news(_INFO_SYMBOL, _INFO_NAME, [], since, cfg)
        )
    data_time = _max_datetime([row.get("publish_time") for row in rows or []])
    return ProbeOutcome(
        ok=True,
        rows=len(rows or []),
        data_time=data_time,
        detail="贵州茅台近 7 日第一页",
        latency_ms=int((time.perf_counter() - started) * 1000),
    )


def _run_probe(target: dict[str, Any], data_dir: str | Path) -> ProbeOutcome:
    if target["kind"] == "market":
        return _probe_market(target)
    provider = target["provider"]
    if provider == "cninfo":
        return _probe_cninfo(data_dir)
    if provider == "em" and target["category"] == "announcements":
        return _probe_em_announcements()
    if provider in {"ths", "em"} and target["category"] == "news":
        return _probe_news(provider, data_dir)
    return ProbeOutcome(ok=False, error=f"未知探测目标: {target['id']}")


# ---------------------------------------------------------------------------
# 状态归并
# ---------------------------------------------------------------------------

def _apply_outcome(
    previous: dict[str, Any] | None,
    target: dict[str, Any],
    outcome: ProbeOutcome,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or cn_now()
    previous = previous or {}
    checked_at = now.replace(microsecond=0).isoformat()
    fail = int(previous.get("consecutive_fail") or 0)
    if outcome.ok:
        fail = 0
        cooldown_until = ""
    else:
        fail += 1
        cooldown_until = (
            (now + timedelta(seconds=_COOLDOWN_SECONDS)).replace(microsecond=0).isoformat()
            if fail >= _FAIL_DOWN else ""
        )

    data_time = outcome.data_time
    lag_seconds = None
    state = "unknown"
    message = outcome.detail
    if not outcome.ok:
        state = "down" if fail >= _FAIL_DOWN else "degraded"
        message = outcome.error or "探测失败"
    elif data_time is None:
        state = "unknown"
        message = f"{outcome.detail or '探测成功'}，未返回数据时间"
    else:
        lag_seconds = max(0.0, (now.astimezone(CN_TZ) - data_time.astimezone(CN_TZ)).total_seconds())
        if lag_seconds > MAX_LAG.get(target["category"], 36 * 3600.0):
            state = "degraded"
            message = f"{outcome.detail or '探测成功'}，数据时间偏旧"
        else:
            state = "ok"
            message = outcome.detail or "探测成功"

    result = {
        **target,
        "state": state,
        "message": message,
        "checked_at": checked_at,
        "latency_ms": outcome.latency_ms,
        "rows": outcome.rows,
        "data_time": data_time.isoformat() if data_time else None,
        "lag_seconds": round(lag_seconds, 1) if lag_seconds is not None else None,
        "error": outcome.error,
        "consecutive_fail": fail,
        "cooldown_until": cooldown_until,
        "success_count": int(previous.get("success_count") or 0) + (1 if outcome.ok else 0),
    }
    history = list(previous.get("history") or [])
    history.append({
        "checked_at": checked_at,
        "state": state,
        "latency_ms": outcome.latency_ms,
        "data_time": data_time.isoformat() if data_time else None,
        "error": outcome.error,
    })
    result["history"] = history[-HISTORY_LIMIT:]
    return result


def _emit_transition(
    previous_state: str,
    entry: dict[str, Any],
    data_dir: str | Path,
) -> None:
    """状态跃迁报警。只在这里做 IO, 不进入 _LOCK。"""
    new_state = str(entry.get("state") or "unknown")
    if new_state == previous_state:
        return
    try:
        from app.services.system_alert import emit
    except Exception:  # noqa: BLE001 — 告警模块异常不得影响探测主流程
        return
    target_id = str(entry.get("id") or entry.get("provider") or "unknown")
    provider = str(entry.get("provider") or target_id)
    category = str(entry.get("category") or "")
    label = f"{category}/{provider}"
    dedupe_key = f"source:{target_id}"
    data = {
        "target": target_id,
        "provider": provider,
        "category": category,
        "previous_state": previous_state,
        "state": new_state,
        "consecutive_fail": entry.get("consecutive_fail"),
        "error": (entry.get("error") or "")[:300],
    }
    if new_state == "down":
        emit(
            "source_down", "critical", f"数据源不可用: {label}",
            f"{label} 连续失败 {entry.get('consecutive_fail')} 次: "
            f"{entry.get('error') or entry.get('message') or '探测失败'}",
            dedupe_key=dedupe_key, data=data, data_dir=data_dir,
        )
    elif new_state == "degraded":
        emit(
            "source_degraded", "warn", f"数据源降级: {label}",
            f"{label} 探测异常: {entry.get('error') or entry.get('message') or '未知原因'}",
            dedupe_key=dedupe_key, data=data, data_dir=data_dir,
        )
    elif previous_state in {"down", "degraded"} and new_state in {"ok", "unknown"}:
        emit(
            "source_recovered", "info", f"数据源恢复: {label}",
            f"{label} 已从 {previous_state} 恢复为 {new_state}",
            dedupe_key=dedupe_key, data=data, data_dir=data_dir,
        )


def probe_targets(
    data_dir: str | Path,
    selected: list[dict[str, Any]] | None = None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """探测给定目标并合并写入状态文件; 返回本次探测后的快照。"""
    selected = selected or targets(data_dir)
    with _LOCK:
        state = load_state(data_dir)
    results: dict[str, Any] = {}
    transitions: list[tuple[str, dict[str, Any]]] = []
    for target in selected:
        outcome = ProbeOutcome(ok=False, error="未执行")
        # 失败后同轮再试一次, 区分瞬时抖动与持续不可用; 重试仍失败才累加连续失败。
        for attempt in range(2):
            try:
                outcome = _run_probe(target, data_dir)
            except Exception as exc:  # noqa: BLE001 - 单源异常只标记自己, 不中断整轮探测
                logger.warning("source health probe failed %s: %s", target.get("id"), exc)
                outcome = ProbeOutcome(ok=False, error=f"{type(exc).__name__}: {exc}"[:300])
            if outcome.ok or attempt == 1:
                break
            time.sleep(1.0)
        with _LOCK:
            previous = (state.get("targets") or {}).get(target["id"])
            previous_state = str((previous or {}).get("state") or "")
            entry = _apply_outcome(previous, target, outcome, now=now)
            state.setdefault("targets", {})[target["id"]] = entry
            results[target["id"]] = entry
            state["updated_at"] = cn_now().replace(microsecond=0).isoformat()
            if previous_state and previous_state != entry["state"]:
                transitions.append((previous_state, entry))
        logger.info(
            "source health %s: %s (%s ms)", target["id"], entry["state"], entry["latency_ms"]
        )
    with _LOCK:
        # 探测期间可能有路由切换写入 active/switches, 落盘前合并, 避免覆盖审计。
        latest = load_state(data_dir)
        latest.setdefault("targets", {}).update(state.get("targets") or {})
        latest["updated_at"] = state.get("updated_at")
        if not latest.get("active"):
            latest["active"] = state.get("active") or {}
        if not latest.get("switches"):
            latest["switches"] = state.get("switches") or []
        _save_state(data_dir, latest)
    for previous_state, entry in transitions:
        _emit_transition(previous_state, entry, data_dir)
    return snapshot(data_dir)


# ---------------------------------------------------------------------------
# 查询与主备路由
# ---------------------------------------------------------------------------

def _summarize_target(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        key: entry.get(key)
        for key in (
            "id", "category", "provider", "display", "state", "message",
            "checked_at", "latency_ms", "rows", "data_time", "lag_seconds",
            "error", "consecutive_fail", "cooldown_until", "success_count",
        )
    }


def snapshot(data_dir: str | Path) -> dict[str, Any]:
    state = load_state(data_dir)
    entries = [
        _summarize_target(entry)
        for entry in (state.get("targets") or {}).values()
        if isinstance(entry, dict)
    ]
    categories = []
    for category, label in CAP_LABELS.items():
        rows = [entry for entry in entries if entry.get("category") == category]
        if rows:
            categories.append({
                "id": category,
                "label": label,
                "state": _worst_state([row.get("state") for row in rows]),
                "sources": rows,
            })
    return {
        "version": STATE_VERSION,
        "updated_at": state.get("updated_at"),
        "categories": categories,
        "targets": entries,
        "switches": (state.get("switches") or [])[-20:],
        "active": state.get("active") or {},
    }


def _worst_state(states: list[str | None]) -> str:
    order = {"ok": 0, "unknown": 1, "degraded": 2, "down": 3}
    return max((state for state in states if state), key=lambda s: order.get(s, 1), default="unknown")


def _realtime_switch_allowed(now: datetime | None = None) -> bool:
    """实时行情可切换窗口: 连续竞价, 且避开开盘/午后开盘前 5 分钟。

    09:30 与 13:00 的首次快照可能仍是集合竞价或上午收盘价, 天然滞后,
    不能据此判定源故障。
    """
    now = now or cn_now()
    if not in_continuous_session(now):
        return False
    t = now.time()
    return dt_time(9, 35) <= t <= dt_time(11, 30) or dt_time(13, 5) <= t <= dt_time(15, 0)


def _scopes(scope: str) -> list[str]:
    if scope == "market":
        return ["realtime", "minute"]
    if scope == "slow":
        return ["daily", "financial"]
    if scope == "info":
        return ["announcements", "news"]
    if scope in CAP_LABELS:
        return [scope]
    return []


def probe_scope(data_dir: str | Path, scope: str = "all") -> dict[str, Any]:
    categories = _scopes(scope)
    selected = [
        target for target in targets(data_dir)
        if not categories or target["category"] in categories
    ]
    return probe_targets(data_dir, selected)


def resolve_provider(capability: str, primary: str) -> str:
    """按健康状态解析当前生效源。

    只有 realtime/financial 参与切换; 未达到 down 阈值时保持主源。
    切换到备源后写 ``active`` 与 ``switches`` 审计, 供页面显示。
    """
    if capability not in FALLBACK_CAPS:
        return primary
    # 非连续竞价时段实时行情本就停滞, 不据此切换, 避免午休/盘后误判。
    if capability == "realtime" and not _realtime_switch_allowed():
        return primary
    from app.services import preferences

    chain = preferences.get_provider_fallbacks(capability, primary)
    if not chain:
        return primary
    chain = [name for name in chain if name]
    if primary not in chain:
        chain.insert(0, primary)
    if not any(name != primary for name in chain):
        return primary
    with _LOCK:
        data_dir = preferences.data_dir()
        state = load_state(data_dir)
        candidates = state.get("targets") or {}

        def healthy(name: str) -> bool:
            entry = candidates.get(f"{capability}.{name}") or {}
            if entry.get("state") not in {"ok", "unknown"} or int(entry.get("consecutive_fail") or 0):
                return False
            if name != "tickflow":
                from app.data_providers import custom as custom_sources

                if not custom_sources.provider_has_dataset(name, capability):
                    return False
            return True

        chosen = primary
        if not healthy(primary):
            chosen = next((name for name in chain if name != primary and healthy(name)), primary)
        active = state.setdefault("active", {})
        previous = active.get(capability)
        if chosen != previous:
            active[capability] = chosen
            if previous is None:
                previous = primary
            if chosen != primary:
                state.setdefault("switches", []).append({
                    "at": cn_now().replace(microsecond=0).isoformat(),
                    "capability": capability,
                    "from": previous,
                    "to": chosen,
                    "reason": "primary_unhealthy",
                })
            elif previous != primary:
                state.setdefault("switches", []).append({
                    "at": cn_now().replace(microsecond=0).isoformat(),
                    "capability": capability,
                    "from": previous,
                    "to": chosen,
                    "reason": "primary_recovered",
                })
            _save_state(data_dir, state)
            logger.warning("数据源切换 %s: %s -> %s", capability, previous, chosen)
        return chosen


def mark_switch(capability: str, source: str, *, reason: str) -> None:
    """供调用方显式记录非健康判定触发的切换 (例如能力缺失)。"""
    from app.services import preferences

    with _LOCK:
        data_dir = preferences.data_dir()
        state = load_state(data_dir)
        active = state.setdefault("active", {})
        previous = active.get(capability)
        if previous == source:
            return
        active[capability] = source
        state.setdefault("switches", []).append({
            "at": cn_now().replace(microsecond=0).isoformat(),
            "capability": capability,
            "from": previous,
            "to": source,
            "reason": reason,
        })
        _save_state(data_dir, state)


# ---------------------------------------------------------------------------
# 调度入口
# ---------------------------------------------------------------------------

def scheduled_probe(app_state: Any, scope: str) -> None:
    """APScheduler 任务入口: 取 app.state 的 data_dir, 失败只记日志。"""
    try:
        from app.services import preferences

        data_dir = preferences.data_dir()
        now = cn_now()
        if scope == "market":
            if not _realtime_switch_allowed(now):
                return
        elif scope == "slow" and now.weekday() >= 5:
            return
        probe_scope(data_dir, scope)
    except Exception:  # 探测失败不阻断调度线程
        logger.exception("scheduled source probe failed: %s", scope)
