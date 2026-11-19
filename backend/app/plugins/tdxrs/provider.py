"""tdxrs —— 通达信免费行情(进程内 Rust 客户端)数据源 provider。

拓扑: TSP backend ──PyO3──▶ tdxrs(Rust) ──TCP──▶ 通达信行情服务器
与 tdxquant 的差别: 不需要 API Key / 积分, 也不需要常驻 sidecar(进程内 PyO3 直连
协议服务器, 没有额外端口与预热窗口)。

实现数据集:
  - realtime   全 A 快照(60 只/批, 全市场约 93 批 ≈ 3-4 秒)
  - depth5     五档盘口(复用同一快照, 不产生额外请求; 指数无盘口)
  - daily      A 股/ETF 原始日K(单标的一次取全历史, 实测 6044 根 / 0.59 秒)
  - adj_factor 除权因子(calc_fq_factors 直接给单事件因子 + 每股分红, 含 ETF)
  - minute     1 分钟K(逐标的, 深度约 90 交易日)
未声明 full_minute(无批量端点, 逐标的增量一轮 3-4 分钟, 打不住 6s 节奏)与
financial(只有单期快照, 无 period_end/announce_date 时序) → 自动回退 TickFlow。

单位与口径(CONTRIBUTING §3, 2026-11 实测, 不可凭字段名推断):
  - 快照: price/last_close/open/high/low 为**元**(float), vol 已是**手**直接透传,
    amount 为**元**; servertime 形如 "15:15:4431"(HH:MM:SS + 亚秒尾数, 尾数不可靠,
    只取到秒, 并做跨日回退)。
  - 日K: vol 为**股** → floor(/100) 转手, amount 为**元**(无需换算),
    datetime 为 "YYYY-MM-DD"。
  - 分钟K: vol 同为**股** → floor(/100) 转手, datetime 为 "YYYY-MM-DD HH:MM"(已北京墙钟)。
  - 除权: calc_fq_factors 的 qfq_factor = 参考价/前收盘(<1 方向), TSP 的 ex_factor
    = 前收盘/参考价(>1 方向) → **必须取倒数**; div_per_share 已是每股, 不再 ÷10。
    底层 f32 尾数(如 div=280.2423095703125)统一 round(6)。
  - 指数: 必须走 get_index_bars(_all); 用证券接口只回 1 根且日期陈旧。
  - 退市/无效代码: 服务器会用别的代码顶包(600355→600839 全零), client 层按回包
    code/market 校验后剔除, 不会写 0 价。
"""

from __future__ import annotations

import contextlib
import logging
import math
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import polars as pl

from app.data_providers.base import ProviderRequestError
from app.plugins.tdxrs import client as tdx_client
from app.plugins.tdxrs.client import TdxRsClient, TdxRsError
from app.plugins.tdxrs.symbols import SymbolMapError, is_index, to_tdx, to_tsp

logger = logging.getLogger(__name__)

# 只声明真实提供的数据集; 其余数据集 provider_has_dataset 返回 False → 回退 tickflow
_DATASETS = ("realtime", "depth5", "daily", "adj_factor", "minute")

_SNAPSHOT_BATCH = tdx_client.QUOTE_BATCH  # 60: 协议硬上限
_HIST_INTERVAL_S = 0.02   # 单标的 K 线请求节流(实测单请求 40-100ms)
_MINUTE_INTERVAL_S = 0.02
_DAILY_MAX_COUNT = 8000   # ≈32 年日K; 实测 600519 全史 6044 根
_MINUTE_MAX_COUNT = 21600  # 90 交易日 × 240 根
_MINUTE_BARS_PER_DAY = 240
_ADJ_BACKDAYS = 30       # 推导因子时向前找"除权日前收盘"的回看天数(容忍长期停牌)
_ADJ_FUTURE_DAYS = 15     # 未来已公告事件窗口: 无前收盘自然跳过, 滚动增量轮补上
_HIST_FAIL_BREAK = 20     # 连续失败阈值(超过视为服务端异常, 让任务失败)
_BEIJING = ZoneInfo("Asia/Shanghai")

# 周期名 → 通达信 category(1m 必须是 7: EXHQ_1MIN; 8 返回 23 根同时间戳假数据)
_PERIOD_CATEGORY = {
    "1m": 7, "5m": 0, "15m": 1, "30m": 2, "1h": 3, "day": 4, "week": 5, "month": 6,
}

# XDXR category → 语义(通达信协议 14 类)
_XDXR_CATEGORY = {
    1: "除权除息", 2: "送配股上市", 3: "非流通股上市", 4: "未知股本变动",
    5: "股本变化", 6: "增发新股", 7: "股份回购", 8: "增发新股上市",
    9: "转配股上市", 10: "可转债上市", 11: "扩缩股", 12: "非流通股股份性质变更",
    13: "可转债赎回", 14: "股份性质变更",
}

_DAILY_SCHEMA = {
    "symbol": pl.String, "date": pl.Date,
    "open": pl.Float64, "high": pl.Float64, "low": pl.Float64, "close": pl.Float64,
    "volume": pl.Int64, "amount": pl.Float64,
}
_MINUTE_SCHEMA = {
    "symbol": pl.String, "datetime": pl.Datetime("us"),
    "open": pl.Float64, "high": pl.Float64, "low": pl.Float64, "close": pl.Float64,
    "volume": pl.Float64, "amount": pl.Float64,
}
_ADJ_SCHEMA = {
    "symbol": pl.String, "trade_date": pl.Date, "ex_factor": pl.Float64,
    "dividend": pl.Float64, "bonus": pl.Float64, "allot": pl.Float64,
    "allot_price": pl.Float64, "prev_close": pl.Float64,
}


class TdxRsProviderError(ProviderRequestError):
    """本数据源的请求级失败(继承项目统一异常, 上层据此标记任务失败)。"""


@dataclass
class _TdxRsConfig:
    """轻量 config shim, 让 custom loader 的 provider_has_dataset 能识别本 provider。"""

    name: str = "tdxrs"
    display_name: str = "通达信 (tdxrs 自建直连)"
    datasets: dict = field(default_factory=lambda: dict.fromkeys(_DATASETS))
    path: None = None
    builtin: bool = True


def availability() -> tuple[bool, str]:
    """loader 启动自检: tdxrs 可导入 + 能连上任一服务器。不抛异常。"""
    return tdx_client.probe_connection(timeout=4.0)


def _to_float(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    out = float(value)
    return out if math.isfinite(out) else None


def _to_int(value) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def _beijing_now() -> datetime:
    return datetime.now(_BEIJING)


def _compact_date(value) -> date | None:
    """YYYYMMDD(int/str) → date。"""
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())[:8]
    if len(digits) != 8:
        return None
    try:
        return date(int(digits[:4]), int(digits[4:6]), int(digits[6:8]))
    except ValueError:
        return None


def _xdxr_date(row: dict) -> date | None:
    """XDXR 事件 → date(协议给 year/month/day 三个整数)。"""
    try:
        y, m, d = int(row.get("year") or 0), int(row.get("month") or 0), int(row.get("day") or 0)
        if y < 1990 or not 1 <= m <= 12 or not 1 <= d <= 31:
            return None
        return date(y, m, d)
    except (TypeError, ValueError):
        return None


def _event_in_window(row: dict, start_d: date | None, horizon: date) -> bool:
    """XDXR 事件是否落在 [start_d, horizon] 内。"""
    d = _xdxr_date(row)
    if d is None or d > horizon:
        return False
    return start_d is None or d >= start_d


def _ref_price(
    prev_close: float, dividend: float, bonus: float, allot: float, allot_price: float
) -> float | None:
    """交易所除权参考价: (P - D + AR·AP) / (1 + S + AR), half-up 保留 2 位。

    与 tdxquant 完全同式(项目内唯一权威口径), 便于两源结果对齐。
    """
    denom = 1.0 + bonus + allot
    if denom <= 0:
        return None
    x = (prev_close - dividend + allot * allot_price) / denom
    return math.floor(x * 100 + 0.5) / 100


def _price_limit(symbol: str) -> float:
    """按代码前缀给涨跌停幅度(因子自检容差): 创业板/科创 20%, 北交所 30%, 主板 10%。"""
    code = symbol.split(".")[0]
    if code.startswith(("300", "301", "302", "688", "689")):
        return 0.20
    if code.startswith(("8", "4", "92")):
        return 0.30
    return 0.10


def _bar_dt(row: dict) -> datetime | None:
    """bar 的 datetime 字段 → naive datetime(北京墙钟)。"""
    text = str(row.get("datetime") or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        if not text:
            break
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    # 兜底: 由 year/month/day/hour/minute 拼(部分服务器 datetime 为空)
    try:
        parts = (
            int(row.get("year") or 0), int(row.get("month") or 0), int(row.get("day") or 0),
            int(row.get("hour") or 0), int(row.get("minute") or 0),
        )
        if parts[0] >= 1990 and 1 <= parts[1] <= 12 and 1 <= parts[2] <= 31:
            return datetime(*parts)
    except (TypeError, ValueError):
        return None
    return None


def _server_ms(value, fallback_ms: int) -> int:
    """报价 servertime("15:15:4431") → 毫秒时间戳。

    协议只可靠到 HH:MM:SS(尾部亚秒位口径未确认, 丢弃不猜); 交易所日期用北京当天。
    若拼出的时刻比现在晚 1 小时以上(说明服务端日期还没翻页), 回退一天。
    """
    text = str(value or "").strip()
    if len(text) < 8 or text[2] != ":" or text[5] != ":":
        return fallback_ms
    try:
        hh, mm, ss = int(text[:2]), int(text[3:5]), int(text[6:8])
        today = _beijing_now().date()
        stamp = datetime(today.year, today.month, today.day, hh, mm, ss, tzinfo=_BEIJING)
    except (TypeError, ValueError):
        return fallback_ms
    ms = int(stamp.timestamp() * 1000)
    if ms - fallback_ms > 3600_000:
        ms -= 86_400_000
    return ms


def _quote_record(symbol: str, row: dict, fallback_ms: int, name: str | None) -> dict | None:
    """协议快照行 → 内部 realtime record。无价快照不落库, 不伪造 0。"""
    last = _to_float(row.get("price"))
    prev = _to_float(row.get("last_close"))
    if not last and not prev:
        return None
    if last is not None and last <= 0 and prev is None:
        return None

    change_amount = (last - prev) if (last is not None and prev is not None) else None
    # 与 quote_service 推导同口径: 小数制, 不乘 100
    change_pct = change_amount / prev if (change_amount is not None and prev) else None
    volume = _to_float(row.get("vol"))  # 快照已是手, 零换算

    return {
        "symbol": symbol,
        "name": name,  # 代码表缓存里的名称; 取不到置 None 由维表关联
        "last_price": last,
        "prev_close": prev,
        "open": _to_float(row.get("open")),
        "high": _to_float(row.get("high")),
        "low": _to_float(row.get("low")),
        "volume": math.floor(volume) if volume is not None else None,
        "amount": _to_float(row.get("amount")),
        "change_pct": change_pct,
        "change_amount": change_amount,
        "amplitude": None,       # 快照未提供, 不启发式计算
        "turnover_rate": None,   # 需股本口径 (§3.4), 交给 enriched 管道
        "timestamp": _server_ms(row.get("servertime"), fallback_ms),
        "session": None,
    }


def _bar_rows(symbol: str, bars: list[dict], start_d: date | None, end_d: date) -> list[dict]:
    """日K bar → 内部行(vol 股→手, amount 元)。"""
    out: list[dict] = []
    for bar in bars:
        dt = _bar_dt(bar)
        if dt is None:
            continue
        d = dt.date()
        if d > end_d or (start_d is not None and d < start_d):
            continue
        close = _to_float(bar.get("close"))
        if close is None or close <= 0:
            continue
        vol = _to_float(bar.get("vol"))
        out.append({
            "symbol": symbol,
            "date": d,
            "open": _to_float(bar.get("open")),
            "high": _to_float(bar.get("high")),
            "low": _to_float(bar.get("low")),
            "close": close,
            "volume": math.floor(vol / 100.0) if vol is not None else None,
            "amount": _to_float(bar.get("amount")),
        })
    out.sort(key=lambda r: r["date"])
    return out


def _minute_rows(symbol: str, bars: list[dict], start_dt: datetime | None, end_dt: datetime) -> list[dict]:
    """分钟 bar → 内部行(vol 股→手, datetime 已北京墙钟)。"""
    out: list[dict] = []
    for bar in bars:
        dt = _bar_dt(bar)
        if dt is None or dt > end_dt or (start_dt is not None and dt < start_dt):
            continue
        close = _to_float(bar.get("close"))
        if close is None or close <= 0:
            continue
        vol = _to_float(bar.get("vol"))
        out.append({
            "symbol": symbol,
            "datetime": dt,
            "open": _to_float(bar.get("open")),
            "high": _to_float(bar.get("high")),
            "low": _to_float(bar.get("low")),
            "close": close,
            "volume": float(math.floor(vol / 100.0)) if vol is not None else None,
            "amount": _to_float(bar.get("amount")),
        })
    out.sort(key=lambda r: r["datetime"])
    return out


def _daily_count(start_d: date | None, end_d: date) -> int:
    """窗口 → 日K请求根数(交易日≈自然日×5/7, 加停牌冗余)。"""
    if start_d is None:
        return _DAILY_MAX_COUNT
    span = max(0, (end_d - start_d).days)
    return max(30, min(_DAILY_MAX_COUNT, int(span * 5 / 7) + 30))


def _minute_count(start_dt: datetime | None, end_dt: datetime) -> int:
    if start_dt is None:
        return _MINUTE_MAX_COUNT
    days = max(1, (end_dt.date() - start_dt.date()).days + 1)
    return max(_MINUTE_BARS_PER_DAY, min(_MINUTE_MAX_COUNT, days * _MINUTE_BARS_PER_DAY + _MINUTE_BARS_PER_DAY))


def _depth_entry(row: dict, timestamp_ms: int) -> dict | None:
    """快照行 → 标准盘口字典。零盘口(停牌/无效码)不落库。"""
    bid_prices = [_to_float(row.get(f"bid{i}")) for i in range(1, 6)]
    ask_prices = [_to_float(row.get(f"ask{i}")) for i in range(1, 6)]
    if not any(p for p in bid_prices if p) or not any(p for p in ask_prices if p):
        return None
    if any((p is not None and p > 1e5) for p in bid_prices + ask_prices):
        return None  # 指数/异常标的的哨兵值(实测 376173.22), 不落库
    return {
        "bid_prices": [p or 0.0 for p in bid_prices],
        "bid_volumes": [_to_int(row.get(f"bid_vol{i}")) or 0 for i in range(1, 6)],
        "ask_prices": [p or 0.0 for p in ask_prices],
        "ask_volumes": [_to_int(row.get(f"ask_vol{i}")) or 0 for i in range(1, 6)],
        "timestamp": timestamp_ms,
    }


class TdxRsProvider:
    """通达信免费行情数据源。realtime/depth5 共用一次快照请求。"""

    name = "tdxrs"
    builtin = True
    # 1 分钟历史深度(交易日): 实测 get_security_bars(7) 21600 根 = 90 个交易日
    minute_history_days = 90

    def __init__(self, client: TdxRsClient | None = None) -> None:
        self.config = _TdxRsConfig()
        self._client = client  # 测试可注入假 client; None 时按需创建
        self._owns_client = client is None

    # ---- 生命周期 ----
    def close(self) -> None:  # loader.load_all 重建注册表时会对每个 provider 调 close
        if self._client is not None and self._owns_client:
            with contextlib.suppress(Exception):
                self._client.close()
        self._client = None

    def _get_client(self) -> TdxRsClient:
        if self._client is None:
            self._client = TdxRsClient()
        return self._client

    # ---- 共享: 快照批拉 ----
    def _snapshot(self, symbols: list[str], *, soft: bool) -> tuple[list[str], list[dict], dict]:
        """分批取原始报价行。soft=True 表示轮询路径: 单批失败只丢该批并记 warning。"""
        client = self._get_client()
        stats = {"requested": len(symbols), "unknown": 0, "failed_batches": 0}
        pairs: list[tuple[int, str]] = []
        kept: list[str] = []
        for sym in symbols:
            try:
                market, code = to_tdx(sym)
            except SymbolMapError as e:
                stats["unknown"] += 1
                logger.debug("tdxrs 跳过无法映射的代码 %s: %s", sym, e)
                continue
            pairs.append((market, code))
            kept.append(sym)
        if not pairs:
            return kept, [], stats
        try:
            rows, qstats = client.quotes(pairs)
        except TdxRsError as e:
            if not soft:
                raise TdxRsProviderError(f"报价批次失败: {e}") from e
            logger.warning("tdxrs 报价失败(%d 只): %s", len(pairs), e)
            stats["failed_batches"] += 1
            return kept, [], stats
        stats["failed_batches"] += int(qstats.get("failed_batches") or 0)
        stats["skipped"] = int(qstats.get("skipped") or 0)
        return kept, rows, stats

    def _names(self) -> dict[str, str]:
        """symbol → 名称(来自代码表缓存), 供 realtime 行填充。"""
        client = self._client
        if client is None:
            return {}
        getter = getattr(client, "universe", None)
        if getter is None:
            return {}
        try:
            getter()  # 触发一次枚举, 顺带填 _names 缓存
        except TdxRsError:
            return {}
        names = getattr(client, "_names", None) or {}
        out: dict[str, str] = {}
        for (market, code), name in names.items():
            try:
                out[to_tsp(market, code)] = name
            except SymbolMapError:
                continue
        return out

    # ---- realtime ----
    def get_realtime(self) -> list[dict]:
        """全市场实时快照 → 内部 realtime records。失败软返回 [](不阻断轮询)。"""
        client = self._get_client()
        try:
            symbols = client.universe()
        except TdxRsError as e:
            logger.warning("tdxrs 股票池枚举失败: %s", e)
            return []
        kept, rows, stats = self._snapshot(symbols, soft=True)
        records = self._realtime_records(kept, rows)
        if stats["failed_batches"]:
            logger.warning("tdxrs 快照 %d 批失败, 本轮仅返回 %d 条",
                           stats["failed_batches"], len(records))
        if not records and stats["requested"]:
            logger.warning("tdxrs 实时行情本轮 0 条(请求 %d 只), 疑似连接或协议异常",
                           stats["requested"])
        elif stats.get("skipped"):
            logger.info("tdxrs 实时行情 %d 条, 回包校验剔除 %d 条",
                        len(records), stats["skipped"])
        return records

    def get_realtime_symbols(self, symbols: list[str], *, asset_type: str = "stock") -> list[dict] | None:
        """指定标的快照(股票/ETF 均可)。失败返回 None(保留上轮缓存)。"""
        if not symbols:
            return []
        try:
            kept, rows, _stats = self._snapshot(list(symbols), soft=False)
        except (TdxRsError, TdxRsProviderError) as e:
            logger.warning("tdxrs 指定标的行情拉取失败: %s", e)
            return None
        return self._realtime_records(kept, rows)

    def get_realtime_indices(self, symbols: list[str]) -> list[dict] | None:
        """指数实时快照(价格/成交额可用, 五档无效)。失败返回 None 保留上轮缓存。"""
        wanted = [s for s in (symbols or []) if s]
        if not wanted:
            return []
        try:
            kept, rows, _stats = self._snapshot(wanted, soft=False)
        except (TdxRsError, TdxRsProviderError) as e:
            logger.warning("tdxrs 指数行情拉取失败: %s", e)
            return None
        records = self._realtime_records(kept, rows)
        logger.info("tdxrs 指数行情拉取完成: %d 条(请求 %d 只)", len(records), len(kept))
        return records

    def _realtime_records(self, kept: list[str], rows: list[dict], *, with_name: bool = True) -> list[dict]:
        """原始报价行 → realtime records(按 symbol 对齐, 无价行丢弃)。"""
        by_symbol: dict[str, dict] = {}
        for row in rows:
            try:
                by_symbol[to_tsp(row.get("market"), row.get("code"))] = row
            except SymbolMapError:
                continue
        names = self._names() if with_name else {}
        ts = int(time.time() * 1000)
        out: list[dict] = []
        for sym in kept:
            row = by_symbol.get(sym)
            if row is None:
                continue
            rec = _quote_record(sym, row, ts, names.get(sym))
            if rec is not None:
                out.append(rec)
        return out

    # ---- daily ----
    def get_daily(
        self,
        symbols: list[str],
        start_time: datetime | None,
        end_time: datetime | None,
        asset_type: str = "stock",
        on_chunk_done: Callable[[int, int], None] | None = None,
    ) -> pl.DataFrame:
        parts = [
            df for df in self.iter_daily(
                symbols, start_time=start_time, end_time=end_time,
                asset_type=asset_type, on_chunk_done=on_chunk_done,
            ) if not df.is_empty()
        ]
        return pl.concat(parts, how="diagonal_relaxed") if parts else pl.DataFrame(schema=_DAILY_SCHEMA)

    def iter_daily(
        self,
        symbols: list[str],
        start_time: datetime | None,
        end_time: datetime | None,
        asset_type: str = "stock",
        on_chunk_done: Callable[[int, int], None] | None = None,
    ) -> Iterator[pl.DataFrame]:
        """逐标的取原始日K(不复权), 回到本层的已是 TSP 口径(volume 手 / amount 元)。

        股票走证券接口, 指数走指数接口, ETF/LOF 与股票同接口(实测 510300 正常)。
        单标的失败记 warning 继续(不让一只坏票拖垮全市场同步), 但连续失败超过
        _HIST_FAIL_BREAK 视为服务端异常 → 抛错让任务失败, 不静默产出空表。
        """
        if not symbols:
            if on_chunk_done:
                on_chunk_done(0, 0)
            return
        client = self._get_client()
        start_d = start_time.date() if start_time else None
        end_d = end_time.date() if end_time else _beijing_now().date()
        count = _daily_count(start_d, end_d)
        streak = 0
        for i, symbol in enumerate(symbols):
            rows: list[dict] = []
            try:
                market, code = to_tdx(symbol)
                bars = client.bars(market, code, 4, count, index=is_index(symbol))
                rows = _bar_rows(symbol, bars, start_d, end_d)
                streak = 0
            except (SymbolMapError, TdxRsError) as e:
                streak += 1
                logger.warning("tdxrs 日K %s 拉取失败(%d): %s", symbol, streak, e)
                if streak >= _HIST_FAIL_BREAK:
                    raise TdxRsProviderError(
                        f"日K 连续 {streak} 只标的失败, 疑似服务端异常: {e}"
                    ) from e
            if rows and start_d is not None and rows[0]["date"] > start_d:
                logger.info("tdxrs 日K %s: 服务端深度仅回溯到 %s(窗口起点 %s)",
                            symbol, rows[0]["date"], start_d)
            yield pl.DataFrame(rows, schema=_DAILY_SCHEMA)
            if on_chunk_done:
                on_chunk_done(i + 1, len(symbols))
            time.sleep(_HIST_INTERVAL_S)

    # ---- adj_factor ----
    def get_adj_factors(
        self,
        symbols: list[str],
        start_time: datetime | None,
        end_time: datetime | None,
        asset_type: str = "stock",
        on_chunk_done: Callable[[int, int], None] | None = None,
    ) -> pl.DataFrame:
        """除权因子 → 内部契约(symbol/trade_date/ex_factor 单事件比值 + 明细列)。

        数据链(与 tdxquant 同口径, 但底层换成免费协议):
          get_xdxr_info 事件(每 10 股口径) → ÷10 转每股
          → 同一标的按窗口取一次原始日K, 拿除权日前收盘 P
          → 交易所公式 ref=(P-D+AR·AP)/(1+S+AR) → ex_factor=P/ref
          → 涨跌停自检(错误因子会让复权后除权日涨跌幅越过板限)剔除

        为什么不用 calc_fq_factors: 实测它只对「事件落在本次 bars 窗口内」正确,
        窗口外旧事件会拿窗口边界价当收盘(600519 start=800 时 2025 年事件
        close_before 变成 1838.03), 分页拼接会产出大量错误因子, 故不可用。

        ETF 同路可算(510300 实测 14 个事件); 指数无除权, 直接跳过。
        """
        if not symbols or asset_type == "index":
            return pl.DataFrame(schema=_ADJ_SCHEMA)
        client = self._get_client()
        start_d = start_time.date() if start_time else None
        end_d = end_time.date() if end_time else _beijing_now().date()
        horizon = end_d + timedelta(days=_ADJ_FUTURE_DAYS)

        out_rows: list[dict] = []
        for i, symbol in enumerate(symbols):
            try:
                market, code = to_tdx(symbol)
                if is_index(symbol):
                    events: list[dict] = []
                else:
                    events = [ev for ev in client.xdxr(market, code)
                              if _to_int(ev.get("category")) == 1]
                events = [ev for ev in events if _event_in_window(ev, start_d, horizon)]
                if events:
                    events.sort(key=lambda ev: _xdxr_date(ev) or date(1970, 1, 1))
                    first_ex = _xdxr_date(events[0]) or end_d
                    closes = self._close_map(client, symbol, market, code,
                                             first_ex - timedelta(days=_ADJ_BACKDAYS), end_d)
                    out_rows.extend(self._factor_rows(symbol, events, closes, start_d, end_d))
            except SymbolMapError as e:
                logger.warning("tdxrs 除权因子跳过无法映射的代码 %s: %s", symbol, e)
            except TdxRsError as e:
                # 事件接口失败不能当作「该股无除权」, 但也不该中断全市场一轮;
                # 下一轮同步会重试, 落表按 (symbol, trade_date) 去重不会留脏。
                logger.warning("tdxrs 除权因子 %s 拉取失败: %s", symbol, e)
            if on_chunk_done:
                on_chunk_done(i + 1, len(symbols))
            time.sleep(_HIST_INTERVAL_S)
        if not out_rows:
            return pl.DataFrame(schema=_ADJ_SCHEMA)
        return (
            pl.DataFrame(out_rows, schema=_ADJ_SCHEMA)
            .unique(subset=["symbol", "trade_date"], keep="last")
            .sort(["symbol", "trade_date"])
        )

    def _close_map(
        self, client: TdxRsClient, symbol: str, market: int, code: str,
        start_d: date, end_d: date,
    ) -> dict[date, float]:
        """窗口内原始日K → {交易日: 收盘价}(推导前收盘与涨跌停自检共用)。"""
        count = _daily_count(start_d, end_d)
        bars = client.bars(market, code, 4, count, index=is_index(symbol))
        out: dict[date, float] = {}
        for bar in bars:
            dt = _bar_dt(bar)
            close = _to_float(bar.get("close"))
            if dt is None or close is None or close <= 0:
                continue
            d = dt.date()
            if d < start_d or d > end_d:
                continue
            out[d] = close
        return out

    @staticmethod
    def _factor_rows(
        symbol: str, events: list[dict], closes: dict[date, float],
        start_d: date | None, end_d: date,
    ) -> list[dict]:
        """XDXR 事件 + 收盘序列 → 内部行(ex_factor=P/ref, 每股口径)。"""
        days = sorted(closes)
        out: list[dict] = []
        for ev in events:
            exd = _xdxr_date(ev)
            if exd is None or exd > end_d or (start_d is not None and exd < start_d):
                continue
            # 每 10 股 → 每股(协议 fenhong=10派X元, songzhuangu=10送Y股, peigu=10配Z股)
            dividend = (_to_float(ev.get("fenhong")) or 0.0) / 10.0
            bonus = (_to_float(ev.get("songzhuangu")) or 0.0) / 10.0
            allot = (_to_float(ev.get("peigu")) or 0.0) / 10.0
            allot_price = _to_float(ev.get("peigujia")) or 0.0
            prev_days = [d for d in days if d < exd]
            if not prev_days:
                continue  # 窗口未覆盖到除权前收盘(远古事件 / 未来已公告事件)
            prev_close = closes[prev_days[-1]]
            ref = _ref_price(prev_close, dividend, bonus, allot, allot_price)
            if ref is None or ref <= 0:
                logger.warning(
                    "tdxrs 除权因子 %s %s 参考价无法计算(P=%s D=%s S=%s AR=%s AP=%s), 跳过",
                    symbol, exd, prev_close, dividend, bonus, allot, allot_price)
                continue
            factor = prev_close / ref
            ex_days = [d for d in days if d >= exd]
            if ex_days:  # 涨跌停自检: 因子错会让复权后除权日涨跌幅超出板限
                ret = closes[ex_days[0]] / (prev_close / factor) - 1.0
                if abs(ret) > _price_limit(symbol) + 0.02:
                    logger.warning(
                        "tdxrs 除权因子自检剔除 %s %s: 复权后除权日涨跌幅 %.1f%% 超出涨跌停",
                        symbol, exd, ret * 100)
                    continue
            if not 0.01 < factor < 100:  # 明显异常比值(如顶包码)不写
                logger.warning("tdxrs 除权因子 %s %s 比值异常(%s), 跳过", symbol, exd, factor)
                continue
            out.append({
                "symbol": symbol,
                "trade_date": exd,
                "ex_factor": round(factor, 8),
                "dividend": round(dividend, 6),
                "bonus": round(bonus, 6),
                "allot": round(allot, 6),
                "allot_price": round(allot_price, 4),
                "prev_close": round(prev_close, 4),
            })
        return out

    # ---- minute ----
    def get_minute(
        self,
        symbols: list[str],
        start_time: datetime | None,
        end_time: datetime | None,
        asset_type: str = "stock",
        freq: str = "1m",
        on_chunk_done: Callable[[int, int], None] | None = None,
    ) -> pl.DataFrame:
        chunks = list(self.iter_minute(
            symbols, start_time=start_time, end_time=end_time,
            asset_type=asset_type, freq=freq, on_chunk_done=on_chunk_done,
        ))
        parts = [df for df in chunks if not df.is_empty()]
        return pl.concat(parts, how="diagonal_relaxed") if parts else pl.DataFrame(schema=_MINUTE_SCHEMA)

    def iter_minute(
        self,
        symbols: list[str],
        start_time: datetime | None,
        end_time: datetime | None,
        asset_type: str = "stock",
        freq: str = "1m",
        on_chunk_done: Callable[[int, int], None] | None = None,
    ) -> Iterator[pl.DataFrame]:
        """1 分钟K, datetime 为北京墙钟 naive; 指数不在本数据集(走 tx_index_kline)。

        深度上限 21600 根(约 90 交易日), 超出部分服务端不再返回; 请求失败按
        ProviderRequestError 上抛, 不跨源、不伪造成 0 行。
        """
        if not symbols or freq != "1m" or asset_type == "index":
            if on_chunk_done:
                on_chunk_done(0, 0)
            return
        client = self._get_client()
        start_dt = start_time.astimezone(_BEIJING).replace(tzinfo=None) if (
            start_time is not None and start_time.tzinfo is not None) else start_time
        end_dt = end_time or _beijing_now()
        if end_dt.tzinfo is not None:
            end_dt = end_dt.astimezone(_BEIJING).replace(tzinfo=None)
        count = _minute_count(start_dt, end_dt)
        parts: list[pl.DataFrame] = []
        pending = 0
        for i, symbol in enumerate(symbols):
            try:
                market, code = to_tdx(symbol)
            except SymbolMapError as e:
                logger.warning("tdxrs 分钟K 跳过无法映射的代码 %s: %s", symbol, e)
                if on_chunk_done:
                    on_chunk_done(i + 1, len(symbols))
                continue
            # 分钟K 请求失败必须中止历史同步并标记失败, 不得当作无数据继续遍历。
            bars = client.bars(market, code, 7, count)
            try:
                rows = _minute_rows(symbol, bars, start_dt, end_dt)
            except Exception as e:
                raise TdxRsProviderError(f"分钟K {symbol} 解析失败: {e}") from e
            part = pl.DataFrame(rows, schema=_MINUTE_SCHEMA)
            if parts and pending + part.height > 100_000:
                yield pl.concat(parts, how="diagonal_relaxed")
                parts, pending = [], 0
            if not part.is_empty():
                parts.append(part)
                pending += part.height
            if len(parts) >= 50 or pending >= 100_000:
                yield pl.concat(parts, how="diagonal_relaxed")
                parts, pending = [], 0
            if on_chunk_done:
                on_chunk_done(i + 1, len(symbols))
            time.sleep(_MINUTE_INTERVAL_S)
        if parts:
            yield pl.concat(parts, how="diagonal_relaxed")

    # ---- depth5 ----
    def get_depth_batch(self, symbols: list[str]) -> dict[str, dict]:
        """五档盘口 → {symbol: 标准盘口字典}。价格元, 数量手(透传)。

        指数无盘口(协议该位置是无效字节), 直接跳过不产出。
        """
        if not symbols:
            return {}
        wanted = [s for s in symbols if s and not is_index(s)]
        if not wanted:
            return {}
        client = self._get_client()
        pairs: list[tuple[int, str]] = []
        for sym in wanted:
            try:
                pairs.append(to_tdx(sym))
            except SymbolMapError:
                continue
        if not pairs:
            return {}
        ts = int(time.time() * 1000)
        out: dict[str, dict] = {}
        for i in range(0, len(pairs), _SNAPSHOT_BATCH):
            try:
                rows, _stats = client.quotes(pairs[i : i + _SNAPSHOT_BATCH])
            except TdxRsError as e:
                logger.warning("tdxrs 盘口批次失败(%d 只): %s", len(pairs[i : i + _SNAPSHOT_BATCH]), e)
                continue
            for row in rows:
                try:
                    sym = to_tsp(row.get("market"), row.get("code"))
                except SymbolMapError:
                    continue
                if is_index(sym):
                    continue  # 指数无盘口, 防止回包混入
                entry = _depth_entry(row, ts)
                if entry is not None:
                    out[sym] = entry
        return out

    # ---- 设置页试拉 ----
    def test_dataset(self, dataset: str, symbols: list[str] | None = None) -> dict:
        if dataset in ("daily", "minute", "adj_factor"):
            syms = [s for s in (symbols or [])][:3] or ["600519.SH"]
            try:
                if dataset == "daily":
                    df = self.get_daily(syms, _beijing_now() - timedelta(days=30), _beijing_now())
                elif dataset == "minute":
                    end = _beijing_now().replace(tzinfo=None)
                    df = self.get_minute(syms, end - timedelta(days=3), end)
                else:
                    df = self.get_adj_factors(syms, None, _beijing_now())
            except (TdxRsError, ProviderRequestError) as e:
                return {"provider": self.name, "dataset": dataset, "rows": 0, "error": str(e)}
            head = df.head(5).to_dicts()
            for row in head:  # date/datetime → ISO 字符串, 保证 JSON 可序列化
                for k, v in list(row.items()):
                    if isinstance(v, (date, datetime)):
                        row[k] = v.isoformat()
            return {"provider": self.name, "dataset": dataset, "rows": df.height,
                    "columns": df.columns, "preview": head}

        if dataset in ("realtime", "depth5"):
            syms = [s for s in (symbols or [])][:5] or ["600519.SH", "000001.SZ", "510300.SH"]
            try:
                kept, rows, _stats = self._snapshot(syms, soft=False)
            except (TdxRsError, ProviderRequestError) as e:
                return {"provider": self.name, "dataset": dataset, "rows": 0, "error": str(e)}
            ts = int(time.time() * 1000)
            by_symbol: dict[str, dict] = {}
            for row in rows:
                try:
                    by_symbol[to_tsp(row.get("market"), row.get("code"))] = row
                except SymbolMapError:
                    continue
            if dataset == "realtime":
                names = self._names()
                head = [rec for rec in (
                    _quote_record(sym, by_symbol[sym], ts, names.get(sym))
                    for sym in kept if sym in by_symbol) if rec]
                return {"provider": self.name, "dataset": dataset, "rows": len(head),
                        "columns": list(head[0].keys()) if head else
                        ["symbol", "last_price", "prev_close", "open", "high", "low",
                         "volume", "amount", "change_pct", "change_amount", "timestamp"],
                        "preview": head[:5]}
            depths: dict[str, dict] = {}
            for sym, row in by_symbol.items():
                if is_index(sym):
                    continue
                entry = _depth_entry(row, ts)
                if entry is not None:
                    depths[sym] = entry
            return {"provider": self.name, "dataset": dataset, "rows": len(depths),
                    "columns": ["symbol", "bid_prices", "bid_volumes", "ask_prices", "ask_volumes", "timestamp"],
                    "preview": [{"symbol": c, **d} for c, d in list(depths.items())[:5]]}
        return {"provider": self.name, "dataset": dataset, "rows": 0,
                "error": f"tdxrs 插件未接入 {dataset} 数据集(自动回退 TickFlow)"}

    # ================================================================
    # 扩展接口 (tx_*): 不参与数据集路由, 供页面/新功能按需调用。
    # 命名带 tx_ 前缀, 与契约方法(get_*/iter_*)区分; ManagedProvider 会原样透传。
    # ================================================================
    def tx_kline(
        self, symbol: str, period: str = "day", count: int = 800, fq: int = 0,
    ) -> pl.DataFrame:
        """任意周期 K 线(1m/5m/15m/30m/1h/day/week/month × 3 复权), 列同 get_daily。"""
        category = _PERIOD_CATEGORY.get(period)
        if category is None:
            raise TdxRsProviderError(f"不支持的周期: {period}(可选 {sorted(_PERIOD_CATEGORY)})")
        market, code = to_tdx(symbol)
        bars = self._get_client().bars(market, code, category, count, index=is_index(symbol), fq=int(fq))
        if period in ("1m", "5m", "15m", "30m", "1h"):
            end = _beijing_now().replace(tzinfo=None)
            return pl.DataFrame(_minute_rows(symbol, bars, None, end), schema=_MINUTE_SCHEMA)
        return pl.DataFrame(_bar_rows(symbol, bars, None, _beijing_now().date()), schema=_DAILY_SCHEMA)

    def tx_index_kline(self, symbol: str, period: str = "day", count: int = 800) -> pl.DataFrame:
        """指数 K 线(必须走 index 接口)。1m 深度只有 4-10 个交易日, 日K 稳定。"""
        if not is_index(symbol):
            raise TdxRsProviderError(f"不是指数代码: {symbol}")
        category = _PERIOD_CATEGORY.get(period)
        if category is None:
            raise TdxRsProviderError(f"不支持的周期: {period}")
        market, code = to_tdx(symbol)
        bars = self._get_client().bars(market, code, category, count, index=True)
        if period in ("1m", "5m", "15m", "30m", "1h"):
            end = _beijing_now().replace(tzinfo=None)
            return pl.DataFrame(_minute_rows(symbol, bars, None, end), schema=_MINUTE_SCHEMA)
        return pl.DataFrame(_bar_rows(symbol, bars, None, _beijing_now().date()), schema=_DAILY_SCHEMA)

    def tx_minute_time(self, symbol: str, trade_date: date | None = None) -> pl.DataFrame:
        """分时(240 点/日): 当日或历史某日; vol 为手, price/avg_price 为元。"""
        market, code = to_tdx(symbol)
        stamp = int(trade_date.strftime("%Y%m%d")) if trade_date else 0
        rows = self._get_client().minute_time(market, code, stamp)
        out = [{
            "symbol": symbol,
            "time": str(row.get("time") or ""),
            "price": _to_float(row.get("price")),
            "avg_price": _to_float(row.get("avg_price")),
            "vol": _to_int(row.get("vol")),
        } for row in rows]
        return pl.DataFrame(out, schema={
            "symbol": pl.String, "time": pl.String, "price": pl.Float64,
            "avg_price": pl.Float64, "vol": pl.Int64,
        })

    def tx_transactions(
        self, symbol: str, count: int = 2000, offset: int = 0, trade_date: date | None = None,
        drop_empty: bool = True,
    ) -> pl.DataFrame:
        """逐笔成交: vol 为手, buyorsell(0=买/1=卖/2=中性)。

        收盘后协议会回填充记录(vol=0/num=0), drop_empty=True 时剔除(默认)。
        """
        market, code = to_tdx(symbol)
        stamp = int(trade_date.strftime("%Y%m%d")) if trade_date else 0
        rows = self._get_client().transactions(market, code, offset, count, stamp)
        out = []
        for row in rows:
            vol = _to_int(row.get("vol"))
            num = _to_int(row.get("num"))
            if drop_empty and (vol in (None, 0) or num in (None, 0)):
                continue
            out.append({
                "symbol": symbol,
                "time": str(row.get("time") or ""),
                "price": _to_float(row.get("price")),
                "vol": vol,
                "num": num,
                "buyorsell": _to_int(row.get("buyorsell")),
            })
        return pl.DataFrame(out, schema={
            "symbol": pl.String, "time": pl.String, "price": pl.Float64,
            "vol": pl.Int64, "num": pl.Int64, "buyorsell": pl.Int64,
        })

    def tx_xdxr(self, symbol: str) -> pl.DataFrame:
        """除权除息事件明细(14 类)。金额/比例原始口径为**每 10 股**, 列名保留原值。"""
        market, code = to_tdx(symbol)
        rows = self._get_client().xdxr(market, code)
        out = []
        for row in rows:
            d = _compact_date(f"{row.get('year')}{int(row.get('month') or 0):02d}{int(row.get('day') or 0):02d}")
            out.append({
                "symbol": symbol,
                "date": d,
                "category": _to_int(row.get("category")),
                "category_name": _XDXR_CATEGORY.get(int(row.get("category") or 0), "其他"),
                "fenhong_per10": _to_float(row.get("fenhong")),
                "peigujia": _to_float(row.get("peigujia")),
                "songzhuangu_per10": _to_float(row.get("songzhuangu")),
                "peigu_per10": _to_float(row.get("peigu")),
                "suogu_per10": _to_float(row.get("suogu")),
            })
        return pl.DataFrame(out, schema={
            "symbol": pl.String, "date": pl.Date, "category": pl.Int64, "category_name": pl.String,
            "fenhong_per10": pl.Float64, "peigujia": pl.Float64,
            "songzhuangu_per10": pl.Float64, "peigu_per10": pl.Float64, "suogu_per10": pl.Float64,
        })

    def tx_finance_snapshot(self, symbol: str) -> dict:
        """财务快照(36 字段, **单期**, 无 period_end/announce_date)。

        股本单位为万股; 仅供当期换手率/规模参考, 不能替代财务三表时序。
        """
        market, code = to_tdx(symbol)
        return self._get_client().finance(market, code)

    def tx_security_list(self, market: str = "SH") -> pl.DataFrame:
        """代码表(带名称/前收/每手股数/小数位)。ETF 与指数需要各自过滤前缀。"""
        market_id = {"SH": 1, "SZ": 0, "BJ": 2}.get((market or "").upper())
        if market_id is None:
            raise TdxRsProviderError(f"market 只接受 SH/SZ/BJ: {market!r}")
        rows = self._get_client().security_list(market_id)
        out = []
        for row in rows:
            code = str(row.get("code") or "")
            try:
                symbol = to_tsp(market_id, code)
            except SymbolMapError:
                continue
            out.append({
                "symbol": symbol,
                "code": code,
                "name": str(row.get("name") or "").strip() or None,
                "pre_close": _to_float(row.get("pre_close")),
                "volunit": _to_int(row.get("volunit")),
                "decimal_point": _to_int(row.get("decimal_point")),
            })
        return pl.DataFrame(out, schema={
            "symbol": pl.String, "code": pl.String, "name": pl.String,
            "pre_close": pl.Float64, "volunit": pl.Int64, "decimal_point": pl.Int64,
        })

    def tx_block_components(self, kind: str = "industry") -> pl.DataFrame:
        """板块成分(FG=行业/风格, GN=概念, SZ=指数)。行是「板块名→成分股」。"""
        method = {"industry": "get_industry_blocks", "concept": "get_concept_blocks",
                  "index": "get_index_blocks"}.get(kind)
        if method is None:
            raise TdxRsProviderError(f"kind 只接受 industry/concept/index: {kind!r}")
        rows = self._get_client().block_call(method) or []
        out = [{
            "block_name": str(row.get("blockname") or ""),
            "block_type": _to_int(row.get("block_type")),
            "code_index": _to_int(row.get("code_index")),
            "constituent": str(row.get("code") or ""),
        } for row in rows]
        return pl.DataFrame(out, schema={
            "block_name": pl.String, "block_type": pl.Int64,
            "code_index": pl.Int64, "constituent": pl.String,
        })

    def tx_block_quotes(self, codes: list[str]) -> pl.DataFrame:
        """板块指数行情(880xxx/881xxx)。"""
        rows = self._get_client().block_call("get_block_quotes", list(codes)) or []
        out = [{
            "code": str(row.get("code") or ""),
            "price": _to_float(row.get("price")),
            "prev_close": _to_float(row.get("last_close")),
            "open": _to_float(row.get("open")),
            "high": _to_float(row.get("high")),
            "low": _to_float(row.get("low")),
            "vol": _to_int(row.get("vol")),
            "amount": _to_float(row.get("amount")),
        } for row in rows]
        return pl.DataFrame(out, schema={
            "code": pl.String, "price": pl.Float64, "prev_close": pl.Float64,
            "open": pl.Float64, "high": pl.Float64, "low": pl.Float64,
            "vol": pl.Int64, "amount": pl.Float64,
        })

    def tx_block_kline(self, code: str, period: str = "day", count: int = 800) -> pl.DataFrame:
        """板块指数 K 线(880xxx)。"""
        category = _PERIOD_CATEGORY.get(period)
        if category is None:
            raise TdxRsProviderError(f"不支持的周期: {period}")
        rows = self._get_client().block_call("get_block_bars", category, str(code), 0, int(count)) or []
        out = [{
            "code": str(code),
            "datetime": _bar_dt(row),
            "open": _to_float(row.get("open")),
            "high": _to_float(row.get("high")),
            "low": _to_float(row.get("low")),
            "close": _to_float(row.get("close")),
            "vol": _to_int(row.get("vol")),
            "amount": _to_float(row.get("amount")),
        } for row in rows if _bar_dt(row) is not None]
        return pl.DataFrame(out, schema={
            "code": pl.String, "datetime": pl.Datetime("us"),
            "open": pl.Float64, "high": pl.Float64, "low": pl.Float64, "close": pl.Float64,
            "vol": pl.Int64, "amount": pl.Float64,
        })

    def tx_fund_list(self, market: str = "SH") -> pl.DataFrame:
        """ETF/基金列表(827 只左右), 含名称与基金类型中文名。"""
        market_id = {"SH": 1, "SZ": 0}.get((market or "").upper())
        if market_id is None:
            raise TdxRsProviderError(f"market 只接受 SH/SZ: {market!r}")
        rows = self._get_client().fund_call("get_fund_list", market_id) or []
        out = [{
            "symbol": to_tsp(market_id, str(row.get("code") or "")),
            "code": str(row.get("code") or ""),
            "name": str(row.get("name") or "").strip() or None,
            "fund_type": str(row.get("fund_type_zh") or row.get("fund_type") or ""),
            "pre_close": _to_float(row.get("pre_close")),
            "vol_unit": _to_int(row.get("vol_unit")),
        } for row in rows]
        return pl.DataFrame(out, schema={
            "symbol": pl.String, "code": pl.String, "name": pl.String,
            "fund_type": pl.String, "pre_close": pl.Float64, "vol_unit": pl.Int64,
        })

    def tx_fund_bars(self, symbol: str, period: str = "day", count: int = 800) -> pl.DataFrame:
        """ETF/基金 K 线(走基金客户端, 与股票接口交叉验证过口径一致)。"""
        category = _PERIOD_CATEGORY.get(period)
        if category is None:
            raise TdxRsProviderError(f"不支持的周期: {period}")
        market, code = to_tdx(symbol)
        rows = self._get_client().fund_call("get_fund_bars_all", category, market, code, int(count)) or []
        if period in ("1m", "5m", "15m", "30m", "1h"):
            end = _beijing_now().replace(tzinfo=None)
            return pl.DataFrame(_minute_rows(symbol, rows, None, end), schema=_MINUTE_SCHEMA)
        return pl.DataFrame(_bar_rows(symbol, rows, None, _beijing_now().date()), schema=_DAILY_SCHEMA)

    def tx_server_status(self) -> dict:
        """连接与池状态(设置页诊断用)。"""
        client = self._get_client()
        universe = 0
        try:
            universe = len(client.universe())
        except TdxRsError:
            universe = 0
        return {
            "provider": self.name,
            "connected": client.is_connected(),
            "last_ok_age_s": client.last_ok_age_s(),
            "pool": client.pool_stats(),
            "universe": universe,
            "quote_batch": _SNAPSHOT_BATCH,
            "minute_history_days": self.minute_history_days,
            "datasets": list(_DATASETS),
        }


__all__ = [
    "TdxRsProvider",
    "TdxRsProviderError",
    "availability",
]
