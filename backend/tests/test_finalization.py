"""日K定版状态与实时写盘守卫的单元测试。"""
from __future__ import annotations

from datetime import date, datetime, time

import polars as pl

from app.market_time import CN_TZ, cn_today
from app.services import daily_catchup, finalization
from app.tickflow.repository import DataStore, KlineRepository

TODAY = date(2026, 11, 23)


def _write_daily(root, table: str, day: date, rows: list[tuple[str, float, int | None]]) -> None:
    part = root / table / f"date={day.isoformat()}"
    part.mkdir(parents=True, exist_ok=True)
    pl.DataFrame({
        "symbol": [r[0] for r in rows],
        "date": [day] * len(rows),
        "open": [r[1] for r in rows],
        "high": [r[1] + 0.1 for r in rows],
        "low": [r[1] - 0.1 for r in rows],
        "close": [r[1] for r in rows],
        "volume": [100.0] * len(rows),
        "amount": [1000.0] * len(rows),
        "quote_ts": [r[2] for r in rows],
    }).write_parquet(part / "part.parquet")


def test_mark_and_live_guard_blocks_finalized(tmp_path):
    _write_daily(tmp_path, "kline_daily", TODAY, [("600001.SH", 10.0, None)])
    finalization.mark_finalized(
        tmp_path, "kline_daily", TODAY, source="official_daily_batch",
        rows=1, expected=1,
    )
    assert finalization.is_finalized(tmp_path, "kline_daily", TODAY)
    assert "已由盘后管道定版" in (
        finalization.live_write_block_reason(
            tmp_path, "kline_daily", TODAY,
            now=datetime.combine(TODAY, time(18, 57), tzinfo=CN_TZ),
        ) or ""
    )


def test_live_guard_blocks_historical_partition_without_marker(tmp_path):
    _write_daily(tmp_path, "kline_daily", date(2026, 11, 20), [("600001.SH", 10.0, 100)])
    reason = finalization.live_write_block_reason(
        tmp_path, "kline_daily", date(2026, 11, 20),
        now=datetime.combine(TODAY, time(10, 0), tzinfo=CN_TZ),
    )
    assert reason == "历史分区禁止实时覆写"


def test_live_guard_blocks_after_1535_even_without_marker(tmp_path):
    day = cn_today()
    reason = finalization.live_write_block_reason(
        tmp_path, "kline_daily", day,
        now=datetime.combine(day, time(18, 57), tzinfo=CN_TZ),
    )
    assert reason is not None and "15:35" in reason


def test_finalize_window_only_opens_after_1535():
    assert not finalization.finalize_window_open(
        datetime.combine(TODAY, time(10, 46), tzinfo=CN_TZ)
    )
    assert not finalization.finalize_window_open(
        datetime.combine(TODAY, time(15, 34), tzinfo=CN_TZ)
    )
    assert finalization.finalize_window_open(
        datetime.combine(TODAY, time(15, 35), tzinfo=CN_TZ)
    )


def test_repository_flush_refuses_to_overwrite_finalized(tmp_path):
    _write_daily(tmp_path, "kline_daily", TODAY, [("600001.SH", 10.0, None)])
    finalization.mark_finalized(
        tmp_path, "kline_daily", TODAY, source="official_daily_batch",
        rows=1, expected=1,
    )
    repo = KlineRepository(DataStore(tmp_path))
    snap = pl.DataFrame({
        "symbol": ["600001.SH"],
        "date": [TODAY],
        "open": [99.0], "high": [99.0], "low": [99.0], "close": [99.0],
        "volume": [1.0], "amount": [99.0],
        "quote_ts": [int(datetime.combine(TODAY, time(15, 15), tzinfo=CN_TZ).timestamp() * 1000)],
    })
    repo.flush_live_daily(snap)
    part = tmp_path / "kline_daily" / f"date={TODAY.isoformat()}" / "part.parquet"
    assert pl.read_parquet(part)["close"].to_list() == [10.0]


def test_inspect_official_day_requires_coverage(tmp_path):
    _write_daily(
        tmp_path, "kline_daily", TODAY,
        [("600001.SH", 10.0, None), ("600002.SH", 11.0, None)],
    )
    ok, rows, expected, reason = finalization.inspect_official_day(
        tmp_path, "kline_daily", TODAY,
        expected_symbols=["600001.SH", "600002.SH", "600003.SH"],
    )
    assert not ok
    assert rows == 2 and expected == 3
    assert "覆盖不足" in reason

    ok, rows, expected, _ = finalization.inspect_official_day(
        tmp_path, "kline_daily", TODAY,
        expected_symbols=["600001.SH", "600002.SH"],
    )
    assert ok and rows == 2 and expected == 2


def test_drop_unbackfilled_live_rows_keeps_official_and_removes_live(tmp_path):
    quote_ts = int(datetime.combine(TODAY, time(15, 15), tzinfo=CN_TZ).timestamp() * 1000)
    _write_daily(
        tmp_path, "kline_daily", TODAY,
        [("600001.SH", 10.0, None), ("600002.SH", 11.0, quote_ts)],
    )
    repo = KlineRepository(DataStore(tmp_path))
    removed = repo.drop_unbackfilled_live_rows(
        "kline_daily", TODAY, ["600001.SH", "600002.SH"],
    )
    assert removed == 1
    frame = pl.read_parquet(
        tmp_path / "kline_daily" / f"date={TODAY.isoformat()}" / "part.parquet"
    )
    assert frame["symbol"].to_list() == ["600001.SH"]


def test_catchup_only_after_1640_on_trading_day(monkeypatch, tmp_path):
    from app.services import preferences, trading_day

    monkeypatch.setattr(preferences, "get_pipeline_pull_a_share", lambda: True)
    monkeypatch.setattr(trading_day, "is_trading_day", lambda now=None: True)
    monkeypatch.setattr(finalization, "is_finalized", lambda *args, **kwargs: False)

    before, reason = daily_catchup.should_catch_up(
        tmp_path, now=datetime.combine(TODAY, time(16, 35), tzinfo=CN_TZ),
    )
    assert not before and "16:40" in reason

    after, reason = daily_catchup.should_catch_up(
        tmp_path, now=datetime.combine(TODAY, time(18, 57), tzinfo=CN_TZ),
        allow_late=True,
    )
    assert after and "未定版" in reason

    late, late_reason = daily_catchup.should_catch_up(
        tmp_path, now=datetime.combine(TODAY, time(18, 57), tzinfo=CN_TZ),
    )
    assert not late and "17:40" in late_reason


def test_catchup_skips_when_today_finalized_or_not_trading(monkeypatch, tmp_path):
    from app.services import preferences, trading_day

    monkeypatch.setattr(preferences, "get_pipeline_pull_a_share", lambda: True)
    monkeypatch.setattr(trading_day, "is_trading_day", lambda now=None: True)
    monkeypatch.setattr(finalization, "is_finalized", lambda *args, **kwargs: True)
    assert daily_catchup.should_catch_up(
        tmp_path, now=datetime.combine(TODAY, time(18, 57), tzinfo=CN_TZ),
    ) == (False, "当日已定版")

    monkeypatch.setattr(finalization, "is_finalized", lambda *args, **kwargs: False)
    monkeypatch.setattr(trading_day, "is_trading_day", lambda now=None: False)
    assert daily_catchup.should_catch_up(
        tmp_path, now=datetime.combine(TODAY, time(18, 57), tzinfo=CN_TZ),
    ) == (False, "非交易日")
