"""三年五倍选股服务与 API 测试。

覆盖：
- 谷底→峰值纯算法：命中、行数不足、未达标、窗口间隔限制
- 分组扫描 _scan_frame 与结果字段
- 查询过滤（年份/关键词）、排序、分页
- API 路径：空态列表、K 线窗口、重复扫描 409、years 参数校验
"""
from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

import polars as pl
import pytest
from fastapi import HTTPException

from app.services import stock_growth


def _series(n: int, low: float, high: float, *, start: date | None = None) -> tuple[list[date], list[float], list[float]]:
    start = start or date(2024, 1, 2)
    dates = [start + timedelta(days=i) for i in range(n)]
    lows = [low] * n
    highs = [high] * n
    return dates, lows, highs


# ── 纯算法 ──

def test_best_pair_hit_five_times() -> None:
    dates, lows, _ = _series(80, 1.0, 1.0)
    # 前 40 天高价 1，后 40 天高价 6，最优峰值唯一落在后期。
    highs = [1.0] * 40 + [6.0] * 40
    hit = stock_growth._best_trough_to_peak(dates, lows, highs)
    assert hit is not None
    assert hit["multiple"] == pytest.approx(6.0, rel=1e-6)
    assert hit["gain_pct"] == pytest.approx(500.0, rel=1e-6)
    assert hit["trough_date"] == dates[0].isoformat()
    # 峰值落在后期高价段（并列倍数保留最早达标日，不强制最后一天）。
    peak_idx = dates.index(date.fromisoformat(hit["peak_date"]))
    assert peak_idx >= 40


def test_best_pair_too_few_rows() -> None:
    dates, lows, highs = _series(30, 1.0, 6.0)
    assert stock_growth._best_trough_to_peak(dates, lows, highs) is None


def test_best_pair_below_threshold() -> None:
    dates, lows, highs = _series(80, 1.0, 2.0)
    assert stock_growth._best_trough_to_peak(dates, lows, highs) is None


def test_best_pair_respects_window() -> None:
    """谷底与峰值间隔超过 WINDOW_DAYS 个交易日时不应配对。"""
    n = stock_growth.WINDOW_DAYS + 50
    dates, lows, highs = _series(n, 1.0, 6.0)
    hit = stock_growth._best_trough_to_peak(dates, lows, highs)
    # 窗口内最早谷底只能回溯 756 根；峰值在最后一根时，谷底下标 ≥ 最后一根 - 756
    assert hit is not None
    trough_idx = dates.index(date.fromisoformat(hit["trough_date"]))
    peak_idx = dates.index(date.fromisoformat(hit["peak_date"]))
    assert peak_idx - trough_idx <= stock_growth.WINDOW_DAYS


def test_best_pair_ignores_nonpositive_price() -> None:
    dates, lows, highs = _series(80, 0.0, 6.0)
    assert stock_growth._best_trough_to_peak(dates, lows, highs) is None


# ── 分组扫描 ──

def _frame() -> pl.DataFrame:
    rows: list[dict] = []
    # 命中股：前 40 天低价 1，后 40 天高价 6
    for i in range(80):
        rows.append({
            "symbol": "HIT.SZ", "date": date(2024, 1, 2) + timedelta(days=i),
            "open": 1.0, "high": 6.0 if i >= 40 else 1.0,
            "low": 1.0, "close": 1.0, "volume": 1000.0,
        })
    # 未达标股：2 倍
    for i in range(80):
        rows.append({
            "symbol": "WEAK.SZ", "date": date(2024, 1, 2) + timedelta(days=i),
            "open": 1.0, "high": 2.0, "low": 1.0, "close": 1.0, "volume": 1000.0,
        })
    return pl.DataFrame(rows)


def test_scan_frame_filters_and_attaches_name() -> None:
    rows = stock_growth._scan_frame(_frame(), {"HIT.SZ": "命中股份"})
    assert len(rows) == 1
    hit = rows[0]
    assert hit["symbol"] == "HIT.SZ"
    assert hit["name"] == "命中股份"
    assert hit["multiple"] >= stock_growth.GAIN_THRESHOLD
    assert hit["peak_year"] == "2024"


def test_scan_frame_empty() -> None:
    assert stock_growth._scan_frame(pl.DataFrame(), {}) == []


# ── 查询 ──

def _rows() -> list[dict]:
    return [
        {"symbol": "A.SZ", "name": "甲", "multiple": 8.0, "gain_pct": 700.0,
         "peak_date": "2024-05-01", "peak_year": "2024"},
        {"symbol": "B.SZ", "name": "乙", "multiple": 6.0, "gain_pct": 500.0,
         "peak_date": "2026-03-01", "peak_year": "2026"},
        {"symbol": "C.SZ", "name": "丙", "multiple": 10.0, "gain_pct": 900.0,
         "peak_date": "2020-01-01", "peak_year": "2020"},
    ]


def test_query_sort_default_by_multiple_desc() -> None:
    out = stock_growth.query(_rows(), years=0, page=1, page_size=50)
    assert [r["symbol"] for r in out["rows"]] == ["C.SZ", "A.SZ", "B.SZ"]
    assert out["total"] == 3


def test_query_keyword_matches_symbol_or_name() -> None:
    out = stock_growth.query(_rows(), keyword="甲", page=1, page_size=50)
    assert [r["symbol"] for r in out["rows"]] == ["A.SZ"]
    out = stock_growth.query(_rows(), keyword="B.SZ", page=1, page_size=50)
    assert [r["symbol"] for r in out["rows"]] == ["B.SZ"]


def test_query_pagination() -> None:
    out = stock_growth.query(_rows(), page=1, page_size=2)
    assert len(out["rows"]) == 2 and out["total"] == 3
    out = stock_growth.query(_rows(), page=2, page_size=2)
    assert len(out["rows"]) == 1


def test_query_years_cutoff(monkeypatch) -> None:
    monkeypatch.setattr(stock_growth, "cn_today", lambda: date(2026, 11, 17))
    # years=3 → 只保留 2024 及以后峰值
    out = stock_growth.query(_rows(), years=3, page=1, page_size=50)
    assert {r["symbol"] for r in out["rows"]} == {"A.SZ", "B.SZ"}


# ── API ──

class _FakeRepo:
    def __init__(self, frame: pl.DataFrame | None = None) -> None:
        self.frame = frame if frame is not None else pl.DataFrame()
        self.index_set: set[str] = set()

    def get_instruments_asset(self, asset_type: str) -> pl.DataFrame:
        return pl.DataFrame({"symbol": ["HIT.SZ", "WEAK.SZ"], "name": ["命中股份", "弱势股份"]})

    def get_index_symbol_set(self) -> set[str]:
        return self.index_set

    def scan_history(self, symbols, start, end, columns=None) -> pl.DataFrame:
        return self.frame


def _request(repo: _FakeRepo) -> SimpleNamespace:
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(repo=repo)))


def test_api_list_empty_state() -> None:
    from app.api import stock_growth as stock_growth_api

    req = _request(_FakeRepo())
    out = stock_growth_api.list_growth(req, years=10, keyword="", sort="multiple", page=1, page_size=50)
    assert out["rows"] == [] and out["total"] == 0
    assert "status" in out


def test_api_list_invalid_sort() -> None:
    from app.api import stock_growth as stock_growth_api

    req = _request(_FakeRepo())
    with pytest.raises(HTTPException) as exc:
        stock_growth_api.list_growth(req, sort="bogus")
    assert exc.value.status_code == 400


def test_api_kline_window(monkeypatch) -> None:
    from app.api import stock_growth as stock_growth_api

    frame = pl.DataFrame({
        "symbol": ["HIT.SZ"] * 3,
        "date": [date(2024, 3, 1), date(2024, 3, 2), date(2024, 3, 3)],
        "open": [1.0, 1.1, 1.2], "high": [1.2, 1.3, 1.4],
        "low": [0.9, 1.0, 1.1], "close": [1.1, 1.2, 1.3], "volume": [1.0, 2.0, 3.0],
    })
    req = _request(_FakeRepo(frame))
    out = stock_growth_api.growth_kline(req, "HIT.SZ", "2024-03-01", "2024-03-03")
    assert len(out["rows"]) == 3
    assert out["rows"][0]["date"] == "2024-03-01"


def test_api_refresh_rejects_bad_years() -> None:
    from app.api import stock_growth as stock_growth_api

    req = _request(_FakeRepo())
    with pytest.raises(HTTPException) as exc:
        stock_growth_api.refresh_growth(req, {"years": 0})
    assert exc.value.status_code == 400


def test_api_refresh_conflict_when_running(monkeypatch) -> None:
    from app.api import stock_growth as stock_growth_api

    monkeypatch.setattr(stock_growth, "_status", {
        "running": True, "stage": "scan", "pct": 50, "message": "扫描中",
        "last_run_at": None, "last_count": 0, "error": None,
    })
    req = _request(_FakeRepo())
    with pytest.raises(HTTPException) as exc:
        stock_growth_api.refresh_growth(req, {"years": 10})
    assert exc.value.status_code == 409


def test_api_scan_end_to_end_with_fake_repo() -> None:
    """scan 经 Fake repo 产出命中行并落盘；状态与结果文件可读回。"""
    repo = _FakeRepo(_frame())
    rows = stock_growth.scan(repo, years=10)
    assert len(rows) == 1
    saved = stock_growth.load_saved_rows()
    assert saved[0]["symbol"] == "HIT.SZ"
    status = stock_growth.get_status()
    assert status["running"] is False and status["last_count"] == 1


def test_api_refresh_starts_background_task() -> None:
    from app.api import stock_growth as stock_growth_api

    req = _request(_FakeRepo())
    out = stock_growth_api.refresh_growth(req, {"years": 10})
    assert out["status"] == "started" and out["years"] == 10
