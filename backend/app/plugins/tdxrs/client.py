"""tdxrs —— 通达信免费行情 Rust/PyO3 客户端封装。

职责: 连接生命周期、批量分片、回包校验、股票池枚举、K线/逐笔/板块/分时取数。
不关心 TSP 内部契约(单位换算、symbol 归一在 provider 层)。

协议口径(2026-11 实测, 180.153.18.170:7709, 收盘后):
  - quotes: 上限 60 只/批(MAX_QUOTES_COUNT); Rust 侧超限会**静默截断**并打日志,
    所以本层主动按 60 分片, 不依赖上游截断。
    price/last_close/open/high/low = 元(float), vol = **手**, amount = 元, cur_vol = 手。
  - bars:   datetime 字符串(日K "YYYY-MM-DD", 分钟 "YYYY-MM-DD HH:MM"),
    vol = **股**(日K与分钟都要 ÷100 才到 TSP 的手口径), amount = 元。
  - 指数:   必须走 get_index_bars(_all); 用 get_security_bars 只回 1 根且日期陈旧。
  - 除权:   calc_fq_factors 给单事件 qfq_factor(=参考价/前收盘, <1 方向),
    与 TSP ex_factor(前收盘/参考价, >1 方向)互为倒数。
  - 退市/无效代码: 服务器会用别的代码顶包(实测 600355 → 600839 全零),
    必须按回包 code/market 校验, 不匹配即剔除。
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time

from app.data_providers.base import ProviderRequestError

logger = logging.getLogger(__name__)

QUOTE_BATCH = 60        # MAX_QUOTES_COUNT
KLINE_PAGE = 800        # MAX_KLINE_COUNT(单请求上限)
SECURITY_PAGE = 1000    # get_security_list 单页条数
UNIVERSE_TTL_S = 12 * 3600.0
# 北交所代码表瞬断后, 不能把进程级标记当永久事实: 启动时一次失败会导致
# 整个进程生命周期丢掉 920xxx/899050, 直到人工重启。这里改成冷却重试。
BJ_RETRY_S = 10 * 60.0
DEFAULT_TIMEOUT_S = 8.0

# 板块客户端需要显式服务器地址(协议返回里拿不到当前连接目标), 可在环境变量覆盖
BLOCK_SERVER_ENV = "TDXRS_BLOCK_SERVER"
DEFAULT_BLOCK_SERVER = ("180.153.18.170", 7709)


class TdxRsError(ProviderRequestError):
    """接口级失败(连接断开 / 协议返回异常 / 重试后仍失败)。"""


class TdxRsUnavailableError(TdxRsError):
    """客户端不可用(依赖未装 / 所有候选服务器连不上)。"""


def module_available() -> bool:
    """tdxrs 是否可导入(不触发连接)。"""
    try:
        import importlib.util

        return importlib.util.find_spec("tdxrs") is not None
    except Exception:  # noqa: BLE001 — find_spec 对损坏安装也会抛
        return False


def probe_connection(timeout: float = 4.0) -> tuple[bool, str]:
    """独立探活: 建一次连接即断开。不抛异常。"""
    if not module_available():
        return False, "tdxrs 未安装(需 pip install tdxrs>=0.6.10)"
    try:
        from tdxrs import TdxHqClient
    except Exception as e:  # noqa: BLE001
        return False, f"tdxrs 导入失败: {e}"
    client = TdxHqClient()
    try:
        ok = client.connect_to_any(timeout=timeout)
    except Exception as e:  # noqa: BLE001
        return False, f"通达信服务器连接失败: {e}"
    finally:
        with contextlib.suppress(Exception):
            client.disconnect()
    if not ok:
        return False, "所有候选服务器均连接失败(检查网络/防火墙)"
    return True, "ok"


def block_server() -> tuple[str, int]:
    """板块客户端目标服务器: 环境变量 TDXRS_BLOCK_SERVER=ip:port 覆盖默认。"""
    import os

    raw = (os.environ.get(BLOCK_SERVER_ENV) or "").strip()
    if raw and ":" in raw:
        host, _, port = raw.partition(":")
        try:
            return host.strip(), int(port)
        except ValueError:
            logger.warning("TDXRS_BLOCK_SERVER 端口非法(%r), 回退默认", raw)
    return DEFAULT_BLOCK_SERVER


class TdxRsClient:
    """线程安全的进程内客户端薄封装。

    tdxrs 的 PyO3 绑定全程持 GIL(实测 8 线程跑行情加速比 1.00×), 所以并发
    不会带来收益; 这里只保证「连接重建不互相踩」, 不假装能并行取数。
    """

    def __init__(self, timeout: float = DEFAULT_TIMEOUT_S) -> None:
        self._timeout = timeout
        self._lock = threading.RLock()
        self._client = None
        self._names: dict[tuple[int, str], str] = {}
        self._universe: list[str] = []
        self._universe_at = 0.0
        self._bj_unsupported = False
        self._bj_retry_at = 0.0
        self._last_ok_at = 0.0
        self._block = None
        self._fund = None

    # ---- 连接 ----
    def _ensure(self):
        if self._client is not None:
            return self._client
        if not module_available():
            raise TdxRsUnavailableError("tdxrs 未安装(需 pip install tdxrs>=0.6.10)")
        from tdxrs import TdxHqClient

        client = TdxHqClient()
        try:
            ok = client.connect_to_any(timeout=self._timeout)
        except Exception as e:
            raise TdxRsUnavailableError(f"通达信连接失败: {e}") from e
        if not ok:
            raise TdxRsUnavailableError("所有候选服务器均连接失败(检查网络/防火墙)")
        self._client = client
        return client

    def _drop(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            with contextlib.suppress(Exception):
                client.disconnect()

    def close(self) -> None:
        with self._lock:
            self._drop()
            for attr in ("_block", "_fund"):
                client = getattr(self, attr)
                if client is not None:
                    with contextlib.suppress(Exception):
                        client.disconnect()
                    setattr(self, attr, None)
            self._names.clear()
            self._universe.clear()
            self._universe_at = 0.0

    def _call(self, method: str, *args, **kwargs):
        """调用客户端方法; 连接类异常重连一次再试。"""
        last: Exception = TdxRsError(f"{method} 未知失败")
        for attempt in range(2):
            try:
                client = self._ensure()
                result = getattr(client, method)(*args, **kwargs)
                self._last_ok_at = time.monotonic()
                return result
            except TdxRsUnavailableError:
                raise
            except Exception as e:  # noqa: BLE001 — PyO3 抛出类型不可枚举
                last = TdxRsError(f"{method}: {e}")
                with self._lock:
                    self._drop()
                if attempt == 0:
                    time.sleep(0.3)
        raise last

    def is_connected(self) -> bool:
        client = self._client
        try:
            return bool(client is not None and client.is_connected())
        except Exception:  # noqa: BLE001 — PyO3 抛出类型不可枚举
            return False

    def last_ok_age_s(self) -> float | None:
        """最近一次成功请求距今秒数; 从未成功过返回 None。"""
        if not self._last_ok_at:
            return None
        return round(max(0.0, time.monotonic() - self._last_ok_at), 1)

    def pool_stats(self) -> dict:
        """连接池状态(设置页诊断用), 无连接时返回空。"""
        client = self._client
        if client is None:
            return {}
        try:
            return dict(client.pool_stats() or {})
        except Exception:  # noqa: BLE001
            return {}

    # ---- 实时快照 ----
    def quotes(self, pairs: list[tuple[int, str]]) -> tuple[list[dict], dict]:
        """批量快照 → (rows, stats)。按 60 分片, 逐条校验回包代码。

        校验方式: 按 (market, code) 建期望集合, 回包只收命中的; 未命中的计入
        skipped(退市码会被服务器用别的代码顶包, 从而落在这里)。
        """
        stats = {"requested": len(pairs), "skipped": 0, "failed_batches": 0}
        rows: list[dict] = []
        wanted = list(dict.fromkeys((int(m), str(c)) for m, c in pairs))
        skipped_samples: list[str] = []
        skipped_total = 0
        for i in range(0, len(wanted), QUOTE_BATCH):
            batch = wanted[i : i + QUOTE_BATCH]
            try:
                got = self._call("get_security_quotes", batch)
            except TdxRsError as e:
                stats["failed_batches"] += 1
                logger.warning("tdxrs 快照批次失败(%d 只, 第 %d 批): %s",
                               len(batch), i // QUOTE_BATCH + 1, e)
                continue
            expect = {(int(m), str(c)) for m, c in batch}
            got_keys: set[tuple[int, str]] = set()
            hit = 0
            for row in got or []:
                key = (int(row.get("market", -1)), str(row.get("code") or ""))
                got_keys.add(key)
                if key not in expect:
                    stats["skipped"] += 1
                    skipped_total += 1
                    if len(skipped_samples) < 5:
                        skipped_samples.append(f"{key[0]}/{key[1]}")
                    continue
                rows.append(row)
                hit += 1
            if hit < len(batch):
                missing = len(batch) - hit
                stats["skipped"] += missing
                skipped_total += missing
                if len(skipped_samples) < 5:
                    skipped_samples.extend(
                        f"{m}/{c}" for m, c in expect if (m, c) not in got_keys
                    )
                    skipped_samples = skipped_samples[:5]
        if skipped_total:
            # 退市码/代码顶包是供应商常态, 逐标的 WARNING 会在一次全市场拉取
            # 里刷出数千行并淹没真正的同步错误。按批次汇总 + 样例即可排障。
            logger.warning(
                "tdxrs 回包校验剔除 %d 条 (请求 %d 只, 命中 %d 只), 样例: %s",
                skipped_total, len(wanted), len(rows), ", ".join(skipped_samples) or "-",
            )
        return rows, stats

    # ---- K 线 ----
    def bars(
        self,
        market: int,
        code: str,
        category: int,
        count: int,
        *,
        index: bool = False,
        fq: int = 0,
    ) -> list[dict]:
        """一次取 count 根(内部自动分页), 返回按 datetime 升序的原始 bar 列表。

        index=True 走 get_index_bars_all(指数用证券接口只回 1 根)。
        """
        count = max(1, min(int(count), 65535))
        fn = "get_index_bars_all" if index else "get_security_bars_all"
        rows = self._call(fn, int(category), int(market), str(code), count, fq) or []
        with contextlib.suppress(Exception):  # 排序失败不影响数据正确性
            rows.sort(key=lambda r: str(r.get("datetime") or ""))
        return rows

    # ---- 复权因子 / 除权事件 ----
    def fq_factors(self, market: int, code: str, start: int = 0, count: int = KLINE_PAGE) -> dict:
        """calc_fq_factors: {factors: [...], cumulative_qfq, cumulative_hfq}。

        ⚠️ 实测限制: 只有「事件落在本次返回的 bars 窗口内」时因子才正确; 窗口外
        的旧事件会拿窗口边界价当前收盘(如 600519 start=800 时 2025 年事件的
        close_before 变成 1838.03), 因此**不能按 start 分页拼接**, 深层历史必须
        用 get_security_bars 自己取前收盘(provider.get_adj_factors 就是这么做的)。
        """
        return dict(self._call("calc_fq_factors", int(market), str(code), int(start), int(count)) or {})

    def xdxr(self, market: int, code: str) -> list[dict]:
        """除权除息事件(get_xdxr_info), 金额/比例口径为每 10 股。"""
        return list(self._call("get_xdxr_info", int(market), str(code)) or [])

    # ---- 分时 / 逐笔 / 财务 ----
    def minute_time(self, market: int, code: str, date: int = 0) -> list[dict]:
        """分时: 不传 date 取当日; date=YYYYMMDD 取历史。"""
        if date:
            return list(self._call("get_history_minute_time_data", int(market), str(code), int(date)) or [])
        return list(self._call("get_minute_time_data", int(market), str(code)) or [])

    def transactions(self, market: int, code: str, start: int = 0, count: int = 2000, date: int = 0) -> list[dict]:
        """逐笔成交: date=0 取当日, 否则取历史某日。"""
        if date:
            return list(self._call("get_history_transaction_data",
                                   int(market), str(code), int(start), int(count), int(date)) or [])
        return list(self._call("get_transaction_data", int(market), str(code), int(start), int(count)) or [])

    def finance(self, market: int, code: str) -> dict:
        """财务快照(36 字段, 单期): 股本/资产/利润/现金流 + 上市日期/行业。"""
        return dict(self._call("get_finance_info", int(market), str(code)) or {})

    # ---- 代码表 / 股票池 ----
    def security_list(self, market: int) -> list[dict]:
        """整市场证券列表(1000 条/页)。含 name/pre_close/volunit/decimal_point。

        分页按位置偏移, 单页失败不中断后续页(服务器偶发丢包不必让整轮枚举归零);
        若最终命中不足一半, 视为服务器异常抛错, 不缓存残缺股票池。
        """
        total = int(self._call("get_security_count", int(market)) or 0)
        rows: list[dict] = []
        failed = 0
        for start in range(0, total, SECURITY_PAGE):
            try:
                page = self._call("get_security_list", int(market), start) or []
            except TdxRsError as e:
                failed += 1
                logger.warning("tdxrs 代码表 market=%d start=%d 取数失败: %s", market, start, e)
                continue
            if not page:
                break
            rows.extend(page)
        if total and len(rows) < total * 0.5:
            raise TdxRsError(f"代码表只剩 {len(rows)}/{total} 条(失败 {failed} 页)")
        return rows

    def universe(self) -> list[str]:
        """全 A 股票池(TSP 代码格式), 12h 缓存。ETF/指数/债不在内。"""
        now = time.monotonic()
        # 北交所上次失败且已到冷却点: 让缓存失效一次, 重新尝试 market=2。
        if self._bj_unsupported and now >= self._bj_retry_at:
            self._universe = []
        if self._universe and now - self._universe_at < UNIVERSE_TTL_S:
            return list(self._universe)

        from app.plugins.tdxrs.symbols import is_stock, to_tsp

        symbols: list[str] = []
        names: dict[tuple[int, str], str] = {}
        for market in (1, 0, 2):
            if market == 2 and self._bj_unsupported and now < self._bj_retry_at:
                continue
            try:
                rows = self.security_list(market)
            except TdxRsError as e:
                if market == 2:  # 北交所部分服务器不返回, 缺了不影响主板
                    logger.warning(
                        "tdxrs 北交所代码表暂不可用: %s; %.0f 秒后重试, 本次跳过北交所",
                        e, BJ_RETRY_S,
                    )
                    self._bj_unsupported = True
                    self._bj_retry_at = now + BJ_RETRY_S
                    continue
                raise
            if market == 2:
                self._bj_unsupported = False
                self._bj_retry_at = 0.0
            for row in rows:
                code = str(row.get("code") or "")
                name = str(row.get("name") or "").strip()
                if code and name:
                    names[(market, code)] = name
                if is_stock(market, code):
                    try:
                        symbols.append(to_tsp(market, code))
                    except Exception:  # noqa: BLE001 — 单个脏代码不拖垮整池
                        continue
        if not symbols:
            raise TdxRsError("股票池为空(代码表枚举失败)")
        self._names = names
        self._universe = symbols
        self._universe_at = now
        logger.info("tdxrs 股票池: %d 只 A 股 (名称 %d 条)", len(symbols), len(names))
        return list(symbols)

    def name_of(self, market: int, code: str) -> str | None:
        """代码表名称(股票池枚举时顺带缓存); 未命中返回 None, 不猜。"""
        return self._names.get((int(market), str(code)))

    # ---- 板块 / ETF 专属客户端(惰性) ----
    def _ensure_block(self):
        if self._block is not None:
            return self._block
        from tdxrs import TdxBlockClient

        host, port = block_server()
        self._block = TdxBlockClient(host, port, self._timeout)
        return self._block

    def block_call(self, method: str, *args):
        """板块客户端直通调用(失败重连一次)。"""
        last: Exception = TdxRsError(f"block.{method} 未知失败")
        for attempt in range(2):
            try:
                return getattr(self._ensure_block(), method)(*args)
            except Exception as e:  # noqa: BLE001
                last = TdxRsError(f"block.{method}: {e}")
                block, self._block = self._block, None
                if block is not None:
                    with contextlib.suppress(Exception):
                        block.disconnect()
                if attempt == 0:
                    time.sleep(0.3)
        raise last

    def _ensure_fund(self):
        if self._fund is not None:
            return self._fund
        from tdxrs import TdxHqFundClient

        client = TdxHqFundClient()
        ok = client.connect_to_any(timeout=self._timeout)
        if not ok:
            raise TdxRsUnavailableError("ETF 客户端连接失败")
        self._fund = client
        return client

    def fund_call(self, method: str, *args):
        """ETF 客户端直通调用(失败重连一次)。"""
        last: Exception = TdxRsError(f"fund.{method} 未知失败")
        for attempt in range(2):
            try:
                return getattr(self._ensure_fund(), method)(*args)
            except Exception as e:  # noqa: BLE001
                last = TdxRsError(f"fund.{method}: {e}")
                fund, self._fund = self._fund, None
                if fund is not None:
                    with contextlib.suppress(Exception):
                        fund.disconnect()
                if attempt == 0:
                    time.sleep(0.3)
        raise last


__all__ = [
    "BLOCK_SERVER_ENV",
    "DEFAULT_BLOCK_SERVER",
    "KLINE_PAGE",
    "QUOTE_BATCH",
    "SECURITY_PAGE",
    "TdxRsClient",
    "TdxRsError",
    "TdxRsUnavailableError",
    "block_server",
    "module_available",
    "probe_connection",
]
