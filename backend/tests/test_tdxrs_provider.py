"""TdxRsProvider 契约、单位与符号映射测试。

不依赖真实网络: 用假 client 注入 (provider 自带 `client` 参数就是为了这个), 验证
CONTRIBUTING §3 的单位口径(手/元/小数制)、时间契约(北京墙钟 naive)、分片与回包
校验、软/硬失败语义、除权公式方向、扩展接口与能力声明。

真实协议的实测脚本见 /tmp/tdxrs_probe*.py(不进仓库), 口径结论写在 provider 文档串。
"""

from __future__ import annotations

from datetime import date, datetime

import polars as pl
import pytest

from app.data_providers.base import ProviderRequestError
from app.plugins.tdxrs import provider as tp
from app.plugins.tdxrs import symbols as ts
from app.plugins.tdxrs.client import TdxRsClient, TdxRsError


# ---------------------------------------------------------------- 假 client
class FakeClient:
    """按需返回固定数据的假客户端; 记录每次调用以便断言分片与次数。"""

    def __init__(
        self,
        *,
        universe: list[str] | None = None,
        quotes: dict[str, dict] | None = None,
        bars: dict[str, list[dict]] | None = None,
        xdxr: dict[str, list[dict]] | None = None,
        quote_error: Exception | None = None,
        bars_error: Exception | None = None,
    ) -> None:
        self._universe = universe or []
        self._quotes = quotes or {}
        self._bars = bars or {}
        self._xdxr = xdxr or {}
        self._quote_error = quote_error
        self._bars_error = bars_error
        self._names = {ts.to_tdx(s): f"名称{i}" for i, s in enumerate(self._universe)}
        self.calls: list[tuple] = []
        self.quote_batches: list[int] = []

    # provider 实际用到的接口
    def universe(self) -> list[str]:
        self.calls.append(("universe",))
        return list(self._universe)

    def quotes(self, pairs):
        self.calls.append(("quotes", len(pairs)))
        self.quote_batches.append(len(pairs))
        if self._quote_error is not None:
            raise self._quote_error
        rows = []
        for market, code in pairs:
            row = self._quotes.get(ts.to_tsp(market, code))
            if row is not None:
                rows.append(row)
        return rows, {"requested": len(pairs), "skipped": 0, "failed_batches": 0}

    def bars(self, market, code, category, count, *, index=False, fq=0):
        symbol = ts.to_tsp(market, code)
        self.calls.append(("bars", symbol, category, count, index, fq))
        if self._bars_error is not None:
            raise self._bars_error
        return list(self._bars.get(symbol, []))

    def xdxr(self, market, code):
        symbol = ts.to_tsp(market, code)
        self.calls.append(("xdxr", symbol))
        return list(self._xdxr.get(symbol, []))

    def is_connected(self) -> bool:
        return True

    def last_ok_age_s(self) -> float:
        return 0.0

    def pool_stats(self) -> dict:
        return {"size": 1}

    def close(self) -> None:  # 注入的 client 不由 provider 关闭, 但保持接口一致
        self.calls.append(("close",))


def _quote_row(symbol: str, *, price=1457.7, prev=1453.9, vol=35116.0,
               amount=5094594560.0, servertime="15:15:4431", bid=1457.6, ask=1457.8) -> dict:
    market, code = ts.to_tdx(symbol)
    return {
        "market": market, "code": code, "price": price, "last_close": prev,
        "open": 1450.0, "high": 1461.6, "low": 1437.71,
        "vol": vol, "cur_vol": 1713.0, "amount": amount, "servertime": servertime,
        **{f"bid{i}": bid - (i - 1) * 0.1 for i in range(1, 6)},
        **{f"ask{i}": ask + (i - 1) * 0.1 for i in range(1, 6)},
        **{f"bid_vol{i}": i for i in range(1, 6)},
        **{f"ask_vol{i}": i for i in range(1, 6)},
    }


def _bar(dt_text: str, *, close=1457.7, vol=3511645.0, amount=5094594560.0,
         open_=1450.0, high=1461.6, low=1437.0) -> dict:
    """协议 bar: vol 为股, amount 为元。"""
    return {"datetime": dt_text, "open": open_, "high": high, "low": low,
            "close": close, "vol": vol, "amount": amount}


@pytest.fixture()
def no_sleep(monkeypatch):
    """剥掉节流, 免得测试被 20ms/标的拖慢。"""
    monkeypatch.setattr(tp.time, "sleep", lambda *_: None)


def _provider(client: FakeClient) -> tp.TdxRsProvider:
    return tp.TdxRsProvider(client=client)


# ---------------------------------------------------------------- 符号映射
@pytest.mark.parametrize(
    ("symbol", "market", "code"),
    [
        ("600519.SH", 1, "600519"),
        ("000001.SZ", 0, "000001"),
        ("300750.SZ", 0, "300750"),
        ("688111.SH", 1, "688111"),
        ("920002.BJ", 2, "920002"),
        ("000001.SH", 1, "000001"),  # 上证指数与平安银行同码, 靠后缀分开
        ("510300.SH", 1, "510300"),
    ],
)
def test_symbol_mapping_round_trip(symbol: str, market: int, code: str) -> None:
    assert ts.to_tdx(symbol) == (market, code)
    assert ts.to_tsp(market, code) == symbol


@pytest.mark.parametrize("symbol", ["600519", "600519.XX", "ABC.SH", ""])
def test_symbol_mapping_rejects_unknown(symbol: str) -> None:
    with pytest.raises(ts.SymbolMapError):
        ts.to_tdx(symbol)


def test_index_detection_uses_suffix_not_prefix() -> None:
    assert ts.is_index("000001.SH") is True
    assert ts.is_index("000001.SZ") is False  # 平安银行不是指数
    assert ts.is_index("000002.SH") is True  # 上证A股指数, 与万科A(000002.SZ)同码
    assert ts.is_index("000680.SH") is True  # 科创综指
    assert ts.is_index("399001.SZ") is True
    assert ts.is_index("899050.BJ") is True  # 北证50
    assert ts.is_index("920001.BJ") is False  # 北交所股票
    assert ts.is_index("600519.SH") is False
    assert ts.is_etf("510300.SH") is True
    assert ts.is_etf("159915.SZ") is True
    assert ts.is_etf("600519.SH") is False


# ---------------------------------------------------------------- realtime
def test_realtime_units_and_pct_are_decimal() -> None:
    client = FakeClient(universe=["600519.SH"], quotes={"600519.SH": _quote_row("600519.SH")})
    recs = _provider(client).get_realtime()
    assert len(recs) == 1
    rec = recs[0]
    assert rec["symbol"] == "600519.SH"
    assert rec["last_price"] == pytest.approx(1457.7)
    assert rec["prev_close"] == pytest.approx(1453.9)
    # 快照 vol 已是手: 不能像日K那样 ÷100
    assert rec["volume"] == 35116
    assert rec["amount"] == pytest.approx(5094594560.0)
    # 小数制涨跌幅: (1457.7-1453.9)/1453.9, 不乘 100
    assert rec["change_pct"] == pytest.approx((1457.7 - 1453.9) / 1453.9)
    assert rec["change_amount"] == pytest.approx(3.8)
    assert isinstance(rec["timestamp"], int) and rec["timestamp"] > 0
    assert isinstance(rec["name"], str)


def test_realtime_soft_fails_to_empty_on_error() -> None:
    client = FakeClient(universe=["600519.SH"], quote_error=TdxRsError("boom"))
    assert _provider(client).get_realtime() == []


def test_realtime_passes_universe_to_client_in_one_call() -> None:
    """60 只/批的分片是 client 的职责(协议硬上限), provider 不做二次分片。"""
    symbols = [f"{600000 + i}.SH" for i in range(130)]
    client = FakeClient(universe=symbols, quotes={s: _quote_row(s) for s in symbols})
    recs = _provider(client).get_realtime()
    assert len(recs) == 130
    assert client.quote_batches == [130]


def test_realtime_drops_swapped_code_instead_of_zero_price() -> None:
    """退市码会被服务器顶包(600355 → 600839): 回包代码不符就该剔除, 不能写 0。"""
    client = FakeClient(universe=["600355.SH"], quotes={})  # 请求 600355, 回包无命中
    assert _provider(client).get_realtime() == []


def test_realtime_symbols_and_indices_paths() -> None:
    client = FakeClient(
        universe=["600519.SH", "000001.SH"],
        quotes={"600519.SH": _quote_row("600519.SH"),
                "000001.SH": _quote_row("000001.SH", price=3584.58, prev=3602.57)},
    )
    p = _provider(client)
    one = p.get_realtime_symbols(["600519.SH"], asset_type="stock")
    assert one and one[0]["symbol"] == "600519.SH"
    idx = p.get_realtime_indices(["000001.SH"])
    assert idx and idx[0]["last_price"] == pytest.approx(3584.58)


def test_realtime_indices_returns_none_on_failure() -> None:
    client = FakeClient(quote_error=TdxRsError("down"))
    assert _provider(client).get_realtime_indices(["000001.SH"]) is None


# ---------------------------------------------------------------- depth5
def test_depth5_maps_book_and_skips_index() -> None:
    client = FakeClient(
        universe=["600519.SH", "000001.SH"],
        quotes={"600519.SH": _quote_row("600519.SH"),
                # 指数盘口位置是无效字节(实测 bid1=376173.22), 必须跳过
                "000001.SH": _quote_row("000001.SH", bid=376173.22)},
    )
    depth = _provider(client).get_depth_batch(["600519.SH", "000001.SH"])
    assert list(depth) == ["600519.SH"]
    assert depth["600519.SH"]["bid_prices"][0] == pytest.approx(1457.6)
    assert depth["600519.SH"]["ask_volumes"][0] == 1


def test_depth5_drops_zero_book() -> None:
    """停牌/无盘口时五档全 0: 不落库, 避免下游把 0 当成交价。"""
    row = _quote_row("600519.SH", bid=0.0, ask=0.0)
    for i in range(1, 6):
        row[f"bid{i}"] = 0.0
        row[f"ask{i}"] = 0.0
    client = FakeClient(quotes={"600519.SH": row})
    assert _provider(client).get_depth_batch(["600519.SH"]) == {}


# ---------------------------------------------------------------- daily
def test_daily_units_date_filter_and_ordering(no_sleep) -> None:
    rows = [
        _bar("2026-11-17", vol=3511645.0),
        _bar("2026-11-14", vol=2000000.0),
        _bar("2026-10-01", close=0.0),          # 零价(脏数据)应剔除
        _bar("2026-01-05"),                     # 早于窗口, 应剔除
    ]
    client = FakeClient(bars={"600519.SH": rows})
    df = _provider(client).get_daily(
        ["600519.SH"], datetime(2026, 10, 10), datetime(2026, 11, 17)
    )
    assert df.columns == list(tp._DAILY_SCHEMA)
    assert df["date"].to_list() == [date(2026, 11, 14), date(2026, 11, 17)]
    # 日K vol 是股 → 手
    assert df["volume"].to_list() == [20000, 35116]
    assert df["close"].to_list() == [1457.7, 1457.7]


def test_daily_index_uses_index_endpoint(no_sleep) -> None:
    client = FakeClient(bars={"000001.SH": [_bar("2026-11-17", close=3584.58)]})
    _provider(client).get_daily(["000001.SH"], None, datetime(2026, 11, 17))
    assert ("bars", "000001.SH", 4, tp._daily_count(None, date(2026, 11, 17)), True, 0) in client.calls


def test_daily_consecutive_failures_raise(no_sleep) -> None:
    client = FakeClient(universe=[f"{600000 + i}.SH" for i in range(30)],
                        bars_error=TdxRsError("server down"))
    p = _provider(client)
    with pytest.raises(ProviderRequestError):
        p.get_daily([f"{600000 + i}.SH" for i in range(30)], None, datetime(2026, 11, 17))


def test_daily_single_failure_is_tolerated(no_sleep) -> None:
    """单只坏票不能拖垮全市场: 抛错阈值是连续 _HIST_FAIL_BREAK 只。"""
    client = FakeClient(bars={"600519.SH": [_bar("2026-11-17")]})
    client._bars = {"600519.SH": [_bar("2026-11-17")]}  # 000001.SZ 无数据 → 空帧
    df = _provider(client).get_daily(
        ["000001.SZ", "600519.SH"], None, datetime(2026, 11, 17)
    )
    assert df.height == 1 and df["symbol"].to_list() == ["600519.SH"]


# ---------------------------------------------------------------- minute
def test_minute_units_and_naive_datetime(no_sleep) -> None:
    rows = [
        _bar("2026-11-17 09:31", close=1450.0, vol=171300.0, amount=24800000.0),
        _bar("2026-11-17 15:00", close=1457.7, vol=171300.0),
        _bar("2026-11-16 09:31", close=1440.0),  # 窗口外
    ]
    client = FakeClient(bars={"600519.SH": rows})
    df = _provider(client).get_minute(
        ["600519.SH"], datetime(2026, 11, 17, 9, 0), datetime(2026, 11, 17, 15, 30)
    )
    assert df.columns == list(tp._MINUTE_SCHEMA)
    assert df["datetime"].to_list() == [datetime(2026, 11, 17, 9, 31), datetime(2026, 11, 17, 15, 0)]
    assert df["datetime"].dtype == pl.Datetime("us")
    assert df["volume"].to_list() == [1713.0, 1713.0]  # 股 → 手
    assert df["volume"].dtype == pl.Float64


def test_minute_uses_exhq_1min_category(no_sleep) -> None:
    """category 必须是 7(EXHQ_1MIN); 8 会返回 23-24 根同时间戳的假数据。"""
    client = FakeClient(bars={"600519.SH": [_bar("2026-11-17 09:31")]})
    _provider(client).get_minute(["600519.SH"], None, datetime(2026, 11, 17, 15, 30))
    assert any(c[0] == "bars" and c[2] == 7 for c in client.calls)


def test_minute_other_freq_yields_nothing(no_sleep) -> None:
    client = FakeClient(bars={"600519.SH": [_bar("2026-11-17 09:31")]})
    df = _provider(client).get_minute(["600519.SH"], None, datetime(2026, 11, 17), freq="5m")
    assert df.is_empty()
    assert client.calls == []


def test_minute_index_asset_yields_nothing(no_sleep) -> None:
    client = FakeClient(bars={"000001.SH": [_bar("2026-11-17 09:31")]})
    df = _provider(client).get_minute(
        ["000001.SH"], None, datetime(2026, 11, 17), asset_type="index"
    )
    assert df.is_empty()


def test_minute_failure_raises(no_sleep) -> None:
    client = FakeClient(bars_error=TdxRsError("down"))
    with pytest.raises(ProviderRequestError):
        _provider(client).get_minute(["600519.SH"], None, datetime(2026, 11, 17))


# ---------------------------------------------------------------- adj_factor
def _event(day: date, *, fenhong: float = 0.0, songzhuangu: float = 0.0,
           peigu: float = 0.0, peigujia: float = 0.0, category: int = 1) -> dict:
    return {"year": day.year, "month": day.month, "day": day.day,
            "category": category, "fenhong": fenhong, "songzhuangu": songzhuangu,
            "peigu": peigu, "peigujia": peigujia, "suogu": None}


def test_adj_factor_uses_exchange_formula(no_sleep) -> None:
    """ex_factor = 前收盘/参考价(>1 方向), 每股分红 = 每10股 ÷10。"""
    exd = date(2026, 6, 26)
    client = FakeClient(
        bars={"600519.SH": [_bar("2026-06-25", close=1212.1), _bar("2026-06-26", close=1195.0)]},
        xdxr={"600519.SH": [_event(exd, fenhong=280.2423)]},  # 10派280.2423元
    )
    df = _provider(client).get_adj_factors(["600519.SH"], None, datetime(2026, 11, 17))
    assert df.height == 1
    row = df.to_dicts()[0]
    assert row["trade_date"] == exd
    assert row["dividend"] == pytest.approx(28.02423)       # 每股
    assert row["prev_close"] == pytest.approx(1212.1)
    ref = round((1212.1 - 28.02423) / 1.0, 2)               # 交易所公式, half-up 2 位
    assert row["ex_factor"] == pytest.approx(1212.1 / ref, rel=1e-6)
    assert row["ex_factor"] > 1.0                            # 方向: 分红 → 因子 > 1


def test_adj_factor_handles_bonus_issue(no_sleep) -> None:
    """10送10: ref=(P-D)/(1+S), 因子应接近 2.0。"""
    exd = date(2026, 5, 20)
    client = FakeClient(
        bars={"600519.SH": [_bar("2026-05-19", close=100.0), _bar("2026-05-20", close=49.8)]},
        xdxr={"600519.SH": [_event(exd, songzhuangu=10.0)]},
    )
    row = _provider(client).get_adj_factors(["600519.SH"], None, datetime(2026, 11, 17)).to_dicts()[0]
    assert row["bonus"] == pytest.approx(1.0)   # 每股送 1 股
    assert row["ex_factor"] == pytest.approx(2.0)


def test_adj_factor_self_check_drops_broken_factor(no_sleep) -> None:
    """因子错会让复权后除权日涨跌幅越过板限, 自检必须剔除。"""
    exd = date(2026, 6, 26)
    client = FakeClient(
        bars={"600519.SH": [_bar("2026-06-25", close=100.0), _bar("2026-06-26", close=50.0)]},
        xdxr={"600519.SH": [_event(exd, fenhong=1.0)]},  # 因子应≈1.01, 却跌了 50%
    )
    assert _provider(client).get_adj_factors(["600519.SH"], None, datetime(2026, 11, 17)).is_empty()


def test_adj_factor_window_filters_events(no_sleep) -> None:
    client = FakeClient(
        bars={"600519.SH": [_bar("2026-06-25", close=1212.1)]},
        xdxr={"600519.SH": [_event(date(2026, 6, 26), fenhong=280.2423)]},
    )
    df = _provider(client).get_adj_factors(
        ["600519.SH"], datetime(2026, 9, 1), datetime(2026, 11, 17)
    )
    assert df.is_empty()


def test_adj_factor_skips_index_and_unmapped() -> None:
    client = FakeClient(universe=["000001.SH"])
    assert _provider(client).get_adj_factors(["000001.SH"], None, datetime(2026, 11, 17)).is_empty()
    assert ("xdxr", "000001.SH") not in client.calls


# ---------------------------------------------------------------- 能力声明 / test_dataset
def test_provider_declares_only_implemented_datasets() -> None:
    cfg = tp.TdxRsProvider().config
    assert set(cfg.datasets) == {"realtime", "depth5", "daily", "adj_factor", "minute"}
    # 未声明 → provider_has_dataset 返回 False → 自动回退 TickFlow
    assert "full_minute" not in cfg.datasets
    assert "financial" not in cfg.datasets
    assert tp.TdxRsProvider.builtin is True
    assert tp.TdxRsProvider.minute_history_days == 90


def test_test_dataset_reports_unimplemented(no_sleep) -> None:
    out = tp.TdxRsProvider(client=FakeClient()).test_dataset("financial")
    assert out["rows"] == 0 and "financial" in out["error"]


def test_test_dataset_realtime_preview_is_json_safe(no_sleep) -> None:
    client = FakeClient(universe=["600519.SH"], quotes={"600519.SH": _quote_row("600519.SH")})
    out = tp.TdxRsProvider(client=client).test_dataset("realtime", ["600519.SH"])
    assert out["rows"] == 1
    assert isinstance(out["preview"][0]["timestamp"], int)


# ---------------------------------------------------------------- 扩展接口
def test_tx_kline_maps_vol_and_period(no_sleep) -> None:
    client = FakeClient(bars={"600519.SH": [_bar("2026-11-17", vol=3511645.0)]})
    df = tp.TdxRsProvider(client=client).tx_kline("600519.SH", period="day", count=5)
    assert df["volume"].to_list() == [35116]
    assert any(c[0] == "bars" and c[2] == 4 and c[3] == 5 for c in client.calls)


def test_tx_kline_rejects_unknown_period() -> None:
    with pytest.raises(ProviderRequestError):
        tp.TdxRsProvider(client=FakeClient()).tx_kline("600519.SH", period="3m")


def test_tx_index_kline_requires_index_symbol() -> None:
    with pytest.raises(ProviderRequestError):
        tp.TdxRsProvider(client=FakeClient()).tx_index_kline("600519.SH")


def test_tx_transactions_drops_fill_rows() -> None:
    class _Client(FakeClient):
        def transactions(self, market, code, start=0, count=2000, date=0):
            return [
                {"time": "15:00", "price": 1457.7, "vol": 10, "num": 2, "buyorsell": 0},
                {"time": "15:00", "price": 0.0, "vol": 0, "num": 0, "buyorsell": 8},  # 收盘填充行
            ]

    df = tp.TdxRsProvider(client=_Client()).tx_transactions("600519.SH")
    assert df.height == 1 and df["vol"].to_list() == [10]


def test_tx_xdxr_marks_category_name() -> None:
    client = FakeClient(xdxr={"600519.SH": [_event(date(2026, 6, 26), fenhong=280.2423)]})
    df = tp.TdxRsProvider(client=client).tx_xdxr("600519.SH")
    assert df["category_name"].to_list() == ["除权除息"]
    assert df["fenhong_per10"].to_list() == [pytest.approx(280.2423)]  # 保留每 10 股原值


def test_tx_server_status(no_sleep) -> None:
    client = FakeClient(universe=["600519.SH"])
    status = tp.TdxRsProvider(client=client).tx_server_status()
    assert status["connected"] is True
    assert status["universe"] == 1
    assert status["quote_batch"] == 60
    assert status["minute_history_days"] == 90


# ---------------------------------------------------------------- client 层
def test_client_chunks_quotes_and_flags_responses() -> None:
    """真实 client 的分片与回包校验(monkeypatch 掉 _call, 不碰网络)。"""
    calls: list[list] = []

    def fake_call(self, method, *args, **kwargs):
        if method == "get_security_quotes":
            batch = args[0]
            calls.append(batch)
            # 故意顶包: 最后一只回包换成别的代码
            rows = [{"market": m, "code": c, "price": 10.0} for m, c in batch[:-1]]
            rows.append({"market": 1, "code": "600839", "price": 0.0})
            return rows
        raise AssertionError(method)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(TdxRsClient, "_call", fake_call)
    try:
        pairs = [(1, f"{600000 + i}") for i in range(70)]
        rows, stats = TdxRsClient().quotes(pairs)
    finally:
        monkeypatch.undo()
    assert [len(b) for b in calls] == [60, 10]
    assert len(rows) == 68              # 两批各丢 1 只顶包码
    assert stats["skipped"] == 4        # 每批 1 只顶包 + 1 只未命中


def test_client_reports_missing_dependency(monkeypatch) -> None:
    """依赖缺失: 探活给可读原因, 取数软失败为空(不能让轮询线程炸掉)。"""
    monkeypatch.setattr(tp.tdx_client, "module_available", lambda: False)
    ok, reason = tp.tdx_client.probe_connection(timeout=0.1)
    assert ok is False and "未安装" in reason
    rows, stats = TdxRsClient().quotes([(1, "600519")])
    assert rows == [] and stats["failed_batches"] == 1


def test_servertime_out_of_range_falls_back_to_now() -> None:
    """ETF 的 servertime 实测是垃圾("152:99:6950"), 必须回退到本地时间。"""
    fallback = 1_700_000_000_000
    assert tp._server_ms("152:99:6950", fallback) == fallback
    assert tp._server_ms("15:15:4431", fallback) != fallback


# ---------------------------------------------------------------- 插件清单
def test_plugin_manifest_matches_provider() -> None:
    import pathlib

    import yaml

    yaml_path = pathlib.Path(tp.__file__).with_name("plugin.yaml")
    manifest = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    assert manifest["name"] == tp.TdxRsProvider.name
    assert manifest["entry"] == "app.plugins.tdxrs.provider:TdxRsProvider"
    assert manifest["check"] == "app.plugins.tdxrs.provider:availability"
    assert set(manifest["datasets"]) == set(tp._DATASETS)
    assert manifest["runtime"] == "python"
    assert manifest.get("homepage")


def test_provider_registers_in_loader() -> None:
    """entry 可导入 + 类具备 loader 要求的属性(不触发 availability 的网络探活)。"""
    from app.data_providers.custom import loader

    provider = loader._load_entry("app.plugins.tdxrs.provider:TdxRsProvider")()
    assert provider.name == "tdxrs"
    assert getattr(provider, "builtin", False) is True
    assert set(provider.config.datasets) == set(tp._DATASETS)
    provider.close()


def test_close_is_idempotent() -> None:
    client = FakeClient()
    p = _provider(client)
    p.close()
    p.close()  # 注入的 client 不由 provider 关闭, 但仍不应抛
    assert client.calls.count(("close",)) == 0


def test_bj_unavailable_retries_after_cooldown(monkeypatch) -> None:
    """北交所代码表瞬断不能变成进程级永久跳过。"""
    client = TdxRsClient()
    calls = {"bj": 0}

    def fake_security_list(market):
        if market == 1:
            return [{"code": "600519", "name": "贵州茅台"}]
        if market == 0:
            return [{"code": "000001", "name": "平安银行"}]
        calls["bj"] += 1
        if calls["bj"] == 1:
            raise TdxRsError("market=2 unavailable")
        return [{"code": "920001", "name": "北交所样本"}]

    monkeypatch.setattr(client, "security_list", fake_security_list)
    first = client.universe()
    assert client._bj_unsupported is True
    assert not any(s.endswith(".BJ") for s in first)

    # 强制越过冷却点, 下一次 universe() 应重新尝试 market=2。
    client._bj_retry_at = 0.0
    client._universe_at = 0.0
    second = client.universe()
    assert client._bj_unsupported is False
    assert any(s.endswith(".BJ") for s in second)
