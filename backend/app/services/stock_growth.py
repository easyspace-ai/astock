"""三年五倍选股服务。

口径（与既有日 K 语义一致，CONTRIBUTING §3.2/§3.3）：
- 价格使用 enriched 前复权 OHLC；窗口按交易日计数，不使用自然日替代。
- 在过去 ``years`` 个自然年内，任意连续 ≤ ``WINDOW_DAYS`` 个交易日中，
  窗口内最低价 → 之后某日最高价涨幅 ≥ ``GAIN_THRESHOLD`` 倍，即入选。
- 结果落 ``data/user_data/three_year_five_times.csv``（原子写），查询与扫描分离：
  页面列表读已落盘结果；``scan`` 由后台任务触发，不进入请求热路径。
"""
from __future__ import annotations

import csv
import logging
import threading
from collections import abc, deque
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import polars as pl

from app.config import settings
from app.market_time import cn_today

logger = logging.getLogger(__name__)

#: 滑动窗口交易日数（约 3 年）。
WINDOW_DAYS = 756
#: 入选倍数阈值（5 倍）。
GAIN_THRESHOLD = 5.0
#: 默认回看自然年数。
DEFAULT_LOOKBACK_YEARS = 10
#: 单只标的最少交易日，不足不参与计算。
MIN_ROWS = 60
#: 批量扫描标的数。
BATCH_SIZE = 100
#: 每页条数。
PAGE_SIZE = 50

RESULT_FILENAME = "three_year_five_times.csv"

#: 扫描状态（进程级；后台线程与请求线程并发读写，加锁保护）。
_lock = threading.Lock()
_status: dict[str, Any] = {
    "running": False,
    "stage": "idle",
    "pct": 0,
    "message": "",
    "last_run_at": None,
    "last_count": 0,
    "error": None,
}

_PROGRESS_KEYS = ("running", "stage", "pct", "message", "last_run_at", "last_count", "error")


def result_path() -> Path:
    """结果 CSV 路径（全市场共享，放全局 user_data，不按用户分区）。

    榜单由后台全市场扫描产出，所有用户同一份结果；固定全局目录可避免后台
    线程丢失租户上下文后写到与请求线程不一致的私有分区。
    """
    p = settings.data_dir / "user_data" / RESULT_FILENAME
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def get_status() -> dict[str, Any]:
    """返回扫描状态快照（拷贝，避免外部修改内部状态）。"""
    with _lock:
        return {k: _status[k] for k in _PROGRESS_KEYS}


def _set_status(**kwargs: Any) -> None:
    with _lock:
        for key, value in kwargs.items():
            if key in _PROGRESS_KEYS:
                _status[key] = value


def _best_trough_to_peak(
    dates: list[date],
    lows: list[float],
    highs: list[float],
) -> dict[str, Any] | None:
    """求单只标的窗口内最佳「谷底 → 峰值」对（单调队列 O(n)）。

    谷底必须早于峰值，两者间隔 ≤ ``WINDOW_DAYS`` 个交易日；返回倍数最大的一对。
    数据不足 ``MIN_ROWS`` 或无达标对时返回 None。
    """
    n = len(dates)
    if n < MIN_ROWS:
        return None

    best: dict[str, Any] | None = None
    # 队列存候选谷底下标，对应 low 单调递增，队首为窗口内最低价。
    dq: deque[int] = deque()

    for j in range(n):
        # 峰值候选 j，谷底候选为 j 之前的交易日。
        i = j - 1
        if i >= 0:
            # 并列最低价保留更早的下标（更早的谷底），故仅在严格更小时弹出队尾。
            while dq and lows[dq[-1]] > lows[i]:
                dq.pop()
            dq.append(i)

        left = j - WINDOW_DAYS
        while dq and dq[0] < left:
            dq.popleft()

        if not dq:
            continue
        trough_idx = dq[0]
        low_price = lows[trough_idx]
        high_price = highs[j]
        if low_price is None or high_price is None or low_price <= 0 or high_price <= 0:
            continue
        ratio = high_price / low_price
        if ratio < GAIN_THRESHOLD:
            continue
        if best is None or ratio > best["multiple"]:
            best = {
                "trough_date": dates[trough_idx].isoformat(),
                "peak_date": dates[j].isoformat(),
                "trough_price": round(float(low_price), 4),
                "peak_price": round(float(high_price), 4),
                "multiple": round(float(ratio), 2),
                "gain_pct": round(float((ratio - 1) * 100), 1),
                "bars": j - trough_idx,
            }
    return best


def _scan_frame(df: pl.DataFrame, name_map: dict[str, str]) -> list[dict[str, Any]]:
    """对批量日 K DataFrame 分组计算达标标的。"""
    rows: list[dict[str, Any]] = []
    if df.is_empty() or "symbol" not in df.columns:
        return rows

    required = {"symbol", "date", "low", "high"}
    if not required.issubset(df.columns):
        logger.warning("三年五倍: 日K缺少必要列 %s", sorted(required - set(df.columns)))
        return rows

    for key, group in df.sort(["symbol", "date"]).group_by("symbol", maintain_order=True):
        sym_key = key[0] if isinstance(key, tuple) else key
        sym = str(sym_key)
        dates = group["date"].to_list()
        lows = group["low"].to_list()
        highs = group["high"].to_list()
        hit = _best_trough_to_peak(dates, lows, highs)
        if hit is None:
            continue
        hit["symbol"] = sym
        hit["name"] = name_map.get(sym, "")
        hit["peak_year"] = hit["peak_date"][:4]
        rows.append(hit)
    return rows


def _load_universe(repo: Any) -> tuple[list[str], dict[str, str]]:
    """读取股票标的清单与名称映射，剔除指数。"""
    instruments = repo.get_instruments_asset("stock")
    name_map: dict[str, str] = {}
    symbols: list[str] = []
    if not instruments.is_empty() and "symbol" in instruments.columns:
        name_col = "name" if "name" in instruments.columns else None
        for row in instruments.iter_rows(named=True):
            sym = row.get("symbol")
            if sym is None or str(sym).strip() == "":
                continue
            sym = str(sym)
            symbols.append(sym)
            if name_col:
                nm = row.get(name_col)
                if nm is not None and str(nm).strip():
                    name_map[sym] = str(nm).strip()

    index_set = repo.get_index_symbol_set()
    symbols = [s for s in dict.fromkeys(symbols) if s not in index_set]
    return symbols, name_map


def scan(
    repo: Any,
    *,
    years: int = DEFAULT_LOOKBACK_YEARS,
    on_progress: abc.Callable[[str, int, str], None] | None = None,
) -> list[dict[str, Any]]:
    """全市场扫描达标标的并落盘。返回本次命中行。

    ``on_progress(stage, pct, message)`` 用于后台任务进度回写。
    """
    today = cn_today()
    start = today - timedelta(days=int(years * 365))

    def progress(stage: str, pct: int, message: str) -> None:
        if on_progress:
            on_progress(stage, pct, message)

    _set_status(running=True, stage="universe", pct=0, message="解析标的池…", error=None)
    symbols, name_map = _load_universe(repo)
    if not symbols:
        _set_status(running=False, stage="idle", pct=100, message="无可用股票标的",
                    last_run_at=today.isoformat(), last_count=0, error=None)
        return []

    progress("universe", 5, f"标的池 {len(symbols)} 只")

    columns = ["symbol", "date", "open", "high", "low", "close", "volume"]
    frames: list[pl.DataFrame] = []
    total = len(symbols)
    for idx in range(0, total, BATCH_SIZE):
        batch = symbols[idx:idx + BATCH_SIZE]
        frame = repo.scan_history(batch, start, today, columns)
        if frame is not None and not frame.is_empty():
            frames.append(frame)
        pct = 5 + int(idx / total * 80)
        progress("scan", pct, f"扫描日K… {min(idx + BATCH_SIZE, total)}/{total}")

    history = pl.concat(frames, how="diagonal_relaxed") if frames else pl.DataFrame()

    progress("compute", 90, "计算谷底→峰值区间…")
    rows = _scan_frame(history, name_map)
    rows.sort(key=lambda r: r["multiple"], reverse=True)

    save_rows(rows)
    _set_status(running=False, stage="idle", pct=100, message=f"命中 {len(rows)} 只",
                last_run_at=today.isoformat(), last_count=len(rows), error=None)
    progress("done", 100, f"命中 {len(rows)} 只")
    return rows


def save_rows(rows: list[dict[str, Any]]) -> None:
    """原子写入结果 CSV。"""
    path = result_path()
    fieldnames = [
        "symbol", "name", "trough_date", "peak_date", "trough_price",
        "peak_price", "multiple", "gain_pct", "bars", "peak_year",
    ]
    lines = [",".join(fieldnames)]
    for row in rows:
        lines.append(",".join(_csv_cell(row.get(k, "")) for k in fieldnames))
    from app.services.fs_utils import atomic_write_text
    atomic_write_text(path, "\n".join(lines) + "\n")


def _csv_cell(value: Any) -> str:
    text = "" if value is None else str(value)
    if any(ch in text for ch in (",", "\"", "\n")):
        text = "\"" + text.replace("\"", "\"\"") + "\""
    return text


def load_saved_rows() -> list[dict[str, Any]]:
    """读取已落盘结果；文件缺失或损坏时返回空列表（不抛错）。"""
    path = result_path()
    if not path.exists():
        return []
    try:
        with path.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows: list[dict[str, Any]] = []
            for raw in reader:
                if not raw.get("symbol"):
                    continue
                rows.append({
                    "symbol": raw["symbol"],
                    "name": raw.get("name") or "",
                    "trough_date": raw.get("trough_date") or "",
                    "peak_date": raw.get("peak_date") or "",
                    "trough_price": _to_float(raw.get("trough_price")),
                    "peak_price": _to_float(raw.get("peak_price")),
                    "multiple": _to_float(raw.get("multiple")) or 0.0,
                    "gain_pct": _to_float(raw.get("gain_pct")) or 0.0,
                    "bars": int(_to_float(raw.get("bars")) or 0),
                    "peak_year": raw.get("peak_year") or "",
                })
            return rows
    except Exception as exc:  # noqa: BLE001
        logger.warning("三年五倍结果读取失败: %s", exc)
        return []


def _to_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def query(
    rows: list[dict[str, Any]],
    *,
    years: int | None = None,
    keyword: str = "",
    sort: str = "multiple",
    page: int = 1,
    page_size: int = PAGE_SIZE,
) -> dict[str, Any]:
    """对已落盘结果做年份/关键词过滤、排序与分页。"""
    filtered = list(rows)

    if years and years > 0:
        cutoff = str(cn_today().year - years + 1)
        filtered = [r for r in filtered if (r.get("peak_year") or "") >= cutoff]

    if keyword:
        kw = keyword.strip().lower()
        filtered = [
            r for r in filtered
            if kw in (r.get("symbol") or "").lower() or kw in (r.get("name") or "").lower()
        ]

    sort_key = {
        "multiple": lambda r: r.get("multiple") or 0,
        "gain_pct": lambda r: r.get("gain_pct") or 0,
        "peak_date": lambda r: r.get("peak_date") or "",
    }.get(sort, lambda r: r.get("multiple") or 0)
    filtered.sort(key=sort_key, reverse=True)

    total = len(filtered)
    page = max(1, page)
    page_size = max(1, min(page_size, 200))
    start = (page - 1) * page_size
    return {
        "rows": filtered[start:start + page_size],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def symbol_window(
    repo: Any,
    symbol: str,
    trough_date: str,
    peak_date: str,
    pad_days: int = 90,
) -> list[dict[str, Any]]:
    """取谷底→峰值区间（前后各垫 pad_days 自然日）的日 K，供验证图使用。"""
    try:
        start = date.fromisoformat(trough_date) - timedelta(days=pad_days)
        end = date.fromisoformat(peak_date) + timedelta(days=pad_days)
    except (TypeError, ValueError):
        return []

    frame = repo.scan_history([symbol], start, end,
                              ["symbol", "date", "open", "high", "low", "close", "volume"])
    if frame is None or frame.is_empty():
        return []

    rows: list[dict[str, Any]] = []
    for row in frame.sort("date").iter_rows(named=True):
        rows.append({
            "date": row["date"].isoformat() if isinstance(row["date"], date) else str(row["date"]),
            "open": _to_float(row.get("open")),
            "high": _to_float(row.get("high")),
            "low": _to_float(row.get("low")),
            "close": _to_float(row.get("close")),
            "volume": _to_float(row.get("volume")),
        })
    return rows
