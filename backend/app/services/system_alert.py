"""系统级告警 — 把运维事件写入统一告警中心, 并复用现有通知通道。

与 ``alert_store`` 的业务告警区分:
  - ``source="system"``: 数据源 down/恢复、管道失败、完整性阻塞、连续 5xx、
    磁盘水位、日志停用、watchdog 退出。
  - 业务告警仍由策略/监控规则写入, 两边共用 alerts.jsonl 与 SSE, 页面可见。

去重策略:
  - 每个 ``fingerprint`` 保存 active/severity/last_sent。
  - 同一故障在冷却窗口内不重复写/推; 严重度升级 (warn -> critical) 可穿透。
  - ``*_recovered`` 只在之前 active 时发送一次, 并清除 active。
"""
from __future__ import annotations

import contextlib
import json
import logging
import threading
import time
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any

from app.config import settings
from app.log_safety import redact_text
from app.services import alert_store
from app.services.fs_utils import atomic_write_text

logger = logging.getLogger(__name__)

_STATE_VERSION = 1
_LOCK = threading.RLock()
_HTTP_LOCK = threading.Lock()
_HTTP_5XX: deque[float] = deque()
_http_5xx_last_emit = 0.0
_EXECUTOR: ThreadPoolExecutor | None = None
_EXECUTOR_LOCK = threading.Lock()

_SEVERITY_RANK = {"info": 0, "warn": 1, "critical": 2}


def _state_path(data_dir: Path) -> Path:
    p = data_dir / "user_data" / "system_alert_state.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _load_state(data_dir: Path) -> dict:
    try:
        value = json.loads(_state_path(data_dir).read_text(encoding="utf-8"))
        if value.get("version") != _STATE_VERSION or not isinstance(value.get("events"), dict):
            return {"version": _STATE_VERSION, "events": {}}
        return value
    except (OSError, ValueError, TypeError):
        return {"version": _STATE_VERSION, "events": {}}


def _save_state(data_dir: Path, state: dict) -> None:
    _state_path(data_dir).parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        _state_path(data_dir),
        json.dumps(state, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
    )


def _get_executor() -> ThreadPoolExecutor:
    global _EXECUTOR
    with _EXECUTOR_LOCK:
        if _EXECUTOR is None:
            _EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ops-alert")
        return _EXECUTOR


def _now_ms() -> int:
    return int(time.time() * 1000)


def _is_recovery(event_type: str) -> bool:
    return event_type.endswith("_recovered")


def _dispatch_webhooks(title: str, body: str, event_type: str, event: dict) -> list[Future]:
    """按已配置渠道异步推送; 未配置任何渠道时返回空列表。"""
    try:
        from app import secrets_store
        from app.services import preferences, webhook_adapter

        feishu_url = preferences.get_feishu_webhook_url()
        feishu_secret = preferences.get_feishu_webhook_secret()
        wecom_url = preferences.get_wecom_webhook_url()
        custom_url = preferences.get_custom_webhook_url()
        custom_secret = secrets_store.get_custom_webhook_secret()
    except Exception as exc:  # noqa: BLE001
        logger.warning("系统告警读取通知配置失败: %s", exc)
        return []

    tasks: list[Future] = []
    executor = _get_executor()
    if feishu_url:
        tasks.append(executor.submit(
            webhook_adapter.send_feishu, feishu_url, title, body, feishu_secret,
        ))
    if wecom_url:
        tasks.append(executor.submit(
            webhook_adapter.send_wecom, wecom_url, title, body,
        ))
    if custom_url:
        tasks.append(executor.submit(
            webhook_adapter.send_custom, custom_url, title, body, event_type, event,
            custom_secret,
        ))
    return tasks


def emit(
    event_type: str,
    severity: str,
    title: str,
    message: str,
    *,
    dedupe_key: str | None = None,
    data: dict[str, Any] | None = None,
    data_dir: str | Path | None = None,
    dispatch_webhooks: bool = True,
    sync_wait_s: float = 0.0,
) -> bool:
    """记录并推送一条系统级告警; 返回是否真正写入。

    任何异常都吞掉并记 debug: 可观测性链路不得阻断主业务。
    """
    if not settings.ops_alert_enabled:
        return False
    severity = severity if severity in _SEVERITY_RANK else "warn"
    event_type = str(event_type or "system_event").strip() or "system_event"
    # dedupe_key 是跨“故障/恢复”的稳定指纹; 没有显式 key 时才用事件类型兜底。
    key = str(dedupe_key or f"{event_type}:{title}").strip()
    data_dir = Path(data_dir or settings.data_dir)
    recovery = _is_recovery(event_type)
    now_ms = _now_ms()
    cooldown_ms = max(0, int(settings.ops_alert_cooldown_s)) * 1000

    try:
        with _LOCK:
            state = _load_state(data_dir)
            events = state.setdefault("events", {})
            previous = events.get(key) or {}
            active = bool(previous.get("active"))
            last_sent = int(previous.get("last_sent") or 0)
            previous_rank = _SEVERITY_RANK.get(str(previous.get("severity") or "info"), 0)
            current_rank = _SEVERITY_RANK[severity]

            if recovery:
                if not active:
                    return False
            else:
                if (
                    active and last_sent and now_ms - last_sent < cooldown_ms
                    and current_rank <= previous_rank
                ):
                    return False
                # 非恢复事件在冷却窗口内只允许严重度升级穿透

            if recovery:
                events[key] = {
                    "active": False,
                    "severity": "info",
                    "last_sent": now_ms,
                    "count": int(previous.get("count") or 0),
                }
            else:
                events[key] = {
                    "active": True,
                    "severity": severity,
                    "last_sent": now_ms,
                    "count": int(previous.get("count") or 0) + 1,
                }
            _save_state(data_dir, state)
    except Exception as exc:  # noqa: BLE001
        logger.debug("系统告警状态读写失败 (%s): %s", key, exc)
        return False

    safe_title = redact_text(str(title or ""))[:120]
    safe_message = redact_text(str(message or ""))[:2000]
    payload = data if isinstance(data, dict) else {}
    event = {
        "ts": now_ms,
        "rule_id": f"system:{key}"[:200],
        "rule_name": safe_title,
        "source": "system",
        "type": event_type,
        "symbol": "",
        "name": "",
        "message": safe_message,
        "severity": severity,
        "fingerprint": key,
        "data": payload,
    }
    try:
        alert_store.append(data_dir, event)
    except Exception as exc:  # noqa: BLE001
        logger.warning("系统告警写入 alert_store 失败: %s", exc)
        return False

    logger.info("system alert | type=%s severity=%s key=%s", event_type, severity, key)
    if not dispatch_webhooks:
        return True

    body = safe_message
    if payload:
        with contextlib.suppress(TypeError, ValueError):
            body += "\n" + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))[:500]
    try:
        futures = _dispatch_webhooks(safe_title, body, event_type, event)
    except Exception as exc:  # noqa: BLE001
        logger.warning("系统告警 Webhook 提交失败: %s", exc)
        return True

    if sync_wait_s > 0 and futures:
        deadline = time.monotonic() + min(float(sync_wait_s), 5.0)
        for future in futures:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            with contextlib.suppress(Exception):  # 推送失败已由 adapter 记录
                future.result(timeout=remaining)
    return True


def record_http_status(status_code: int) -> None:
    """记录 5xx 并触发“连续 5xx”告警; 1xx-4xx 直接返回。"""
    if status_code < 500:
        return
    global _http_5xx_last_emit
    now = time.time()
    window_s = max(1, int(settings.ops_http_5xx_window_s))
    with _HTTP_LOCK:
        _HTTP_5XX.append(now)
        while _HTTP_5XX and now - _HTTP_5XX[0] > window_s:
            _HTTP_5XX.popleft()
        count = len(_HTTP_5XX)
        should_emit = count >= max(1, int(settings.ops_http_5xx_threshold))
        if should_emit:
            if now - _http_5xx_last_emit < max(1, int(settings.ops_alert_cooldown_s)):
                should_emit = False
            else:
                _http_5xx_last_emit = now
    if should_emit:
        emit(
            "http_5xx",
            "critical",
            "服务连续返回 5xx",
            f"{window_s} 秒内出现 {count} 次 5xx, 请检查后端日志与依赖状态",
            dedupe_key="http_5xx",
            data={"count": count, "window_s": window_s},
        )


def reset_http_5xx() -> None:
    """测试/诊断用: 清空内存窗口。"""
    global _http_5xx_last_emit
    with _HTTP_LOCK:
        _HTTP_5XX.clear()
        _http_5xx_last_emit = 0.0


def shutdown() -> None:
    """应用关闭时放弃排队推送。"""
    global _EXECUTOR
    with _EXECUTOR_LOCK:
        if _EXECUTOR is not None:
            _EXECUTOR.shutdown(wait=False, cancel_futures=True)
            _EXECUTOR = None
