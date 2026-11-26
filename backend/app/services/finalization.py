"""日K定版状态 — 把「实时快照」和「权威日终数据」分开管理。

背景 (2026-11 线上事故):
- 实时行情服务会把当天 `kline_daily/date=.../part.parquet` 整体覆写;
- 盘后管道 16:40 正常定版后, 如果进程在 18:50 再重启一次, QuoteService
  仍可能进入 close_final 阶段, 用最后一笔实时快照把已定版分区再覆写一遍;
- 旧自检只看 `max(quote_ts)`，18:57 的少量补写行会让整分区看起来"已定版"。

本模块提供三道防线:
1. `mark_finalized()` — 盘后管道成功写入官方日K并校验覆盖后落定版标记;
2. `live_write_block_reason()` — 实时路径写盘前检查: 已定版 / 历史日期 /
   已过 15:35 实时写盘截止时间, 任一命中即拒绝覆写;
3. `inspect_official_day()` — 定版前校验分区确实由官方 batch 写入且覆盖达标,
   不满足就不打标, 让自检和补跑继续暴露问题。

设计原则: **宁可分区缺失/被标记为坏, 也不留下"看起来完整、实际是快照"的数据。**
"""
from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from datetime import date, datetime
from datetime import time as dt_time
from pathlib import Path

import polars as pl

from app.market_time import CN_TZ, cn_now, cn_today
from app.services.fs_utils import atomic_write_text

logger = logging.getLogger(__name__)

MARKER_NAME = ".finalized.json"
MARKER_VERSION = 1

# 实时写盘硬截止: 盘后固定价 15:30 结束, close_final 重试窗口也到 15:30。
# 15:35 之后再允许实时覆写只可能固化竞价前旧价; 官方日K主跑已移到 16:40,
# 15:35-16:40 只更新展示缓存, 不让 18:57 的重启把快照写回日K分区。
LIVE_WRITE_CUTOFF = dt_time(15, 35)

# 定版最低覆盖率: 官方 batch 行 / 维表标的池。生产维表包含退市/停牌标的
# (线上 2026-11-23: 5,570 / 5,894 = 94.5%, 其中仅 5 只近 3 日仍活跃),
# 不能按 98% 要求, 否则每天都会误判"未定版"并反复补跑。93% 是源侧大面积
# 缺失的分界线; 定版仍只认可 quote_ts=null 的官方行, 低于阈值不打标。
MIN_COVERAGE = 0.93

# 指数产品契约 (与实时核心四只 + 主要宽基合并; 899050.BJ 必须单独暴露缺失)。
REQUIRED_INDEX_SYMBOLS: tuple[str, ...] = (
    "000001.SH", "000002.SH", "000680.SH", "000688.SH",
    "399001.SZ", "399006.SZ", "399102.SZ", "399107.SZ", "899050.BJ",
)

_DAILY_TABLES = ("kline_daily", "kline_etf_daily", "kline_index_daily")
_ENRICHED_TABLES = ("kline_daily_enriched", "kline_etf_enriched", "kline_index_enriched")
_ENRICHED_PARENT = {
    "kline_daily_enriched": "kline_daily",
    "kline_etf_enriched": "kline_etf_daily",
    "kline_index_enriched": "kline_index_daily",
}


def _coerce_day(day: date | str) -> date:
    return day if isinstance(day, date) else date.fromisoformat(str(day))


def marker_path(data_dir: Path, table: str, day: date | str) -> Path:
    return Path(data_dir) / table / f"date={_coerce_day(day)}" / MARKER_NAME


def read_marker(data_dir: Path, table: str, day: date | str) -> dict | None:
    path = marker_path(data_dir, table, day)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(value, dict) or value.get("version") != MARKER_VERSION:
        return None
    if value.get("table") != table or value.get("date") != _coerce_day(day).isoformat():
        return None
    return value


def is_finalized(data_dir: Path, table: str, day: date | str) -> bool:
    return read_marker(data_dir, table, day) is not None


def mark_finalized(
    data_dir: Path, table: str, day: date | str, *,
    source: str, rows: int, expected: int | None = None,
    max_quote_ts: int | None = None,
) -> None:
    """写定版标记; 只允许在官方日K校验通过后调用。"""
    day = _coerce_day(day)
    path = marker_path(data_dir, table, day)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": MARKER_VERSION,
        "table": table,
        "date": day.isoformat(),
        "source": source,
        "rows": int(rows),
        "expected": int(expected) if expected is not None else None,
        "max_quote_ts": int(max_quote_ts) if max_quote_ts is not None else None,
        "finalized_at": cn_now().isoformat(),
    }
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False))
    logger.info(
        "日K定版: %s %s rows=%d/%s source=%s",
        table, day, rows, expected if expected is not None else "-", source,
    )


def clear_finalized(data_dir: Path, table: str, day: date | str) -> None:
    """修复/重写某日前清除旧标记, 防止旧标记把新写入误当已定版。"""
    marker_path(data_dir, table, day).unlink(missing_ok=True)


def clear_finalized_range(data_dir: Path, table: str, start: date, end: date) -> None:
    """清除 [start, end] 的定版标记 (按日期目录遍历, 不假设连续)。"""
    base = Path(data_dir) / table
    if not base.exists():
        return
    for part in base.glob("date=*"):
        try:
            day = date.fromisoformat(part.name[5:])
        except ValueError:
            continue
        if start <= day <= end:
            (part / MARKER_NAME).unlink(missing_ok=True)


def live_write_block_reason(
    data_dir: Path, table: str, day: date | str, *, now: datetime | None = None,
) -> str | None:
    """实时写盘守卫: 返回拒绝原因; None 表示允许。

    允许的条件必须同时满足:
    - 该日尚未定版;
    - 该日就是北京今天 (历史分区绝不允许实时覆写);
    - 当前北京时间早于 15:35 实时写盘截止。
    """
    day = _coerce_day(day)
    now = now or cn_now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=CN_TZ)
    if is_finalized(data_dir, table, day):
        return "该日已由盘后管道定版"
    parent = _ENRICHED_PARENT.get(table)
    if parent and is_finalized(data_dir, parent, day):
        return f"原始日K已定版 ({parent})"
    if day < cn_today():
        return "历史分区禁止实时覆写"
    if now.date() > day:
        return "已跨日, 历史分区禁止实时覆写"
    if now.date() == day and now.time() >= LIVE_WRITE_CUTOFF:
        return f"已过实时写盘截止时间 {LIVE_WRITE_CUTOFF.strftime('%H:%M')}"
    return None


def finalize_window_open(now: datetime | None = None) -> bool:
    """盘后定版窗口是否已打开 (北京时间 15:35 之后)。

    手动触发盘后管道不能因为"现在是盘中"就把当天半日行情打成定版:
    官方 batch 在 15:35 前拉到的是未收盘日线, 一旦打标, 实时路径会被守卫
    全部拒绝, 下午数据将永久缺失。因此定版/清理实时行只允许发生在窗口内。
    """
    now = now or cn_now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=CN_TZ)
    return now.time() >= LIVE_WRITE_CUTOFF


def _read_day(frame: pl.DataFrame, day: date) -> pl.DataFrame:
    if "date" in frame.columns:
        return frame.filter(pl.col("date") == day)
    return frame


def inspect_official_day(
    data_dir: Path, table: str, day: date | str, *,
    expected_symbols: Iterable[str] | None = None,
) -> tuple[bool, int, int, str]:
    """校验某日分区是否达到官方定版标准。

    返回 (ok, official_rows, expected_count, reason)。
    official 行 = `quote_ts` 为 null 的 batch 行; 实时快照一律不算。
    """
    day = _coerce_day(day)
    part = Path(data_dir) / table / f"date={day}"
    if not part.is_dir():
        return False, 0, 0, "日期分区不存在"
    expected_set = set(expected_symbols or ())
    expected = len(expected_set)
    try:
        files = sorted(part.glob("*.parquet"))
        if not files:
            return False, 0, expected, "日期分区没有 parquet 文件"
        frame = pl.read_parquet(files, columns=None)
        frame = _read_day(frame, day)
        if frame.is_empty():
            return False, 0, expected, "日期分区为空"
        if "symbol" not in frame.columns:
            return False, 0, expected, "缺少 symbol 列"
        symbols = set(frame["symbol"].drop_nulls().cast(pl.String).to_list())
        if "quote_ts" in frame.columns:
            official = frame.filter(pl.col("quote_ts").is_null())
        else:
            # 官方 batch 写出的分区可以没有 quote_ts 列 (典型: 指数/ETF)。
            # 没有该列本身即证明不是实时 flush 写的, 按官方行处理。
            official = frame
        official_symbols = set(official["symbol"].drop_nulls().cast(pl.String).to_list())
    except Exception as exc:  # noqa: BLE001
        logger.warning("定版校验读取失败 %s %s: %s", table, day, exc)
        return False, 0, expected, f"读取失败: {exc}"

    official_rows = official.height
    if official_rows <= 0:
        return False, 0, expected, "没有官方 batch 行(仍为实时快照/无 quote_ts)"
    if expected:
        covered = len(official_symbols & expected_set)
        ratio = covered / expected
        if ratio < MIN_COVERAGE:
            return (
                False, official_rows, expected,
                f"官方覆盖不足 {covered}/{expected} ({ratio:.1%} < {MIN_COVERAGE:.0%})",
            )
        if symbols - official_symbols:
            # 允许少量未请求标的 (如停牌/北交所暂不可用) 残留实时行, 但必须显式记录。
            logger.warning(
                "定版: %s %s 有 %d 个标的仍为非官方行 (样例: %s)",
                table, day, len(symbols - official_symbols),
                sorted(symbols - official_symbols)[:5],
            )
    return True, official_rows, expected, "ok"


def finalized_dates(data_dir: Path, table: str) -> set[date]:
    """已定版日期集合 (按 marker 遍历; 用于启动补跑缺口计算)。"""
    base = Path(data_dir) / table
    if not base.exists():
        return set()
    out: set[date] = set()
    for part in base.glob("date=*"):
        try:
            day = date.fromisoformat(part.name[5:])
        except ValueError:
            continue
        if is_finalized(data_dir, table, day):
            out.add(day)
    return out


__all__ = [
    "LIVE_WRITE_CUTOFF", "MIN_COVERAGE", "REQUIRED_INDEX_SYMBOLS",
    "clear_finalized", "clear_finalized_range", "finalized_dates",
    "inspect_official_day", "is_finalized", "live_write_block_reason",
    "mark_finalized", "marker_path", "read_marker",
]
