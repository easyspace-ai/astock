"""三年五倍选股 API。

路由前缀: /api/stock-growth

端点:
  GET  /          已落盘结果分页查询（年份 / 关键词 / 排序）
  GET  /status    扫描进度与最近一次结果摘要
  GET  /kline     单只标的谷底→峰值区间日 K（验证图）
  POST /refresh   后台触发全市场扫描（运行中重复触发返回 409）
"""
from __future__ import annotations

import logging
import threading

from fastapi import APIRouter, HTTPException, Query, Request

from app.services import stock_growth

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/stock-growth", tags=["stock-growth"])

_SORT_KEYS = {"multiple", "gain_pct", "peak_date"}


@router.get("")
def list_growth(
    request: Request,
    years: int = Query(stock_growth.DEFAULT_LOOKBACK_YEARS, ge=1, le=30),
    keyword: str = Query("", max_length=32),
    sort: str = Query("multiple"),
    page: int = Query(1, ge=1),
    page_size: int = Query(stock_growth.PAGE_SIZE, ge=1, le=200),
) -> dict:
    if sort not in _SORT_KEYS:
        raise HTTPException(status_code=400, detail=f"sort 必须是 {sorted(_SORT_KEYS)} 之一")

    rows = stock_growth.load_saved_rows()
    result = stock_growth.query(
        rows, years=years, keyword=keyword, sort=sort, page=page, page_size=page_size,
    )
    result["status"] = stock_growth.get_status()
    return result


@router.get("/status")
def growth_status(request: Request) -> dict:
    status = stock_growth.get_status()
    status["total"] = len(stock_growth.load_saved_rows())
    return status


@router.get("/kline")
def growth_kline(
    request: Request,
    symbol: str = Query(..., min_length=1, max_length=32),
    trough_date: str = Query(..., min_length=8, max_length=10),
    peak_date: str = Query(..., min_length=8, max_length=10),
) -> dict:
    repo = request.app.state.repo
    rows = stock_growth.symbol_window(repo, symbol.strip(), trough_date, peak_date)
    return {"symbol": symbol.strip(), "rows": rows}


@router.post("/refresh")
def refresh_growth(request: Request, body: dict | None = None) -> dict:
    status = stock_growth.get_status()
    if status["running"]:
        raise HTTPException(status_code=409, detail="已有扫描任务在运行，请等待完成")

    years = (body or {}).get("years", stock_growth.DEFAULT_LOOKBACK_YEARS)
    if isinstance(years, bool) or not isinstance(years, int) or not (1 <= years <= 30):
        raise HTTPException(status_code=400, detail="years 必须是 1 到 30 的整数")

    repo = request.app.state.repo

    def _progress(stage: str, pct: int, message: str) -> None:
        stock_growth._set_status(running=True, stage=stage, pct=pct, message=message)

    def _run() -> None:
        try:
            stock_growth.scan(repo, years=years, on_progress=_progress)
        except Exception as exc:
            logger.exception("三年五倍扫描失败")
            stock_growth._set_status(running=False, stage="idle", pct=100,
                                     message="扫描失败", error=str(exc))

    threading.Thread(target=_run, daemon=True, name="stock-growth-scan").start()
    return {"status": "started", "years": years}
