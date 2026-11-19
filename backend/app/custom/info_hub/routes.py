"""资讯中心只读 API + 手动同步入口。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from . import config, models, service, store

router = APIRouter(prefix="/api/info", tags=["info"])


def _svc(request: Request) -> service.InfoService:
    data_dir = getattr(getattr(getattr(request.app.state, "repo", None), "store", None), "data_dir", None)
    if not data_dir:
        data_dir = getattr(request.app.state, "data_dir", None) or "/tmp/info_hub"
    return service.service_for(data_dir)


def _data_dir(request: Request) -> str:
    return _svc(request).data_dir


@router.get("/health")
def health(request: Request) -> dict[str, Any]:
    return _svc(request).health()


@router.get("/stats")
def stats(request: Request, date: str | None = None) -> dict[str, Any]:
    return _svc(request).stats(date)


@router.get("/watchlist")
def get_watchlist(request: Request) -> dict[str, Any]:
    return config.load_watchlist(_data_dir(request))


@router.put("/watchlist")
async def put_watchlist(request: Request) -> dict[str, Any]:
    payload = await request.json()
    data = config.save_watchlist(_data_dir(request), payload)
    _svc(request).invalidate()
    return data


@router.get("/announcements")
def announcements(
    request: Request,
    date: str | None = None,
    from_date: str | None = Query(None, alias="from"),
    to_date: str | None = Query(None, alias="to"),
    code: str | None = None,
    industry: str | None = None,
    type: str | None = None,
    source: str | None = None,
    important: str | None = None,
    q: str | None = None,
    page: int = 1,
    size: int = 20,
) -> dict[str, Any]:
    return _svc(request).announcements({
        "date": date, "from": from_date, "to": to_date, "code": code, "industry": industry,
        "type": type, "source": source, "important": important, "q": q, "page": page, "size": size,
    })


@router.get("/announcements/{event_id}")
def announcement_detail(request: Request, event_id: str) -> dict[str, Any]:
    row = _svc(request).announcement_detail(event_id)
    if not row:
        raise HTTPException(404, "公告不存在")
    return row


@router.get("/news")
def news(
    request: Request,
    code: str | None = None,
    sector: str | None = None,
    kind: str | None = None,
    source: str | None = None,
    date: str | None = None,
    from_date: str | None = Query(None, alias="from"),
    to_date: str | None = Query(None, alias="to"),
    q: str | None = None,
    page: int = 1,
    size: int = 20,
) -> dict[str, Any]:
    return _svc(request).news_rows({
        "code": code, "sector": sector, "source": source, "date": date,
        "from": from_date, "to": to_date, "q": q, "page": page, "size": size,
    }, kind or "all")


@router.get("/news/{event_id}")
def news_detail(request: Request, event_id: str) -> dict[str, Any]:
    row = _svc(request).news_detail(event_id)
    if not row:
        raise HTTPException(404, "新闻不存在")
    return row


@router.get("/flash")
def flash(
    request: Request,
    code: str | None = None,
    sector: str | None = None,
    date: str | None = None,
    hit: str | None = None,
    q: str | None = None,
    page: int = 1,
    size: int = 40,
) -> dict[str, Any]:
    return _svc(request).flash({
        "code": code, "sector": sector, "date": date, "hit": hit, "q": q, "page": page, "size": size,
    })


@router.get("/timeline")
def timeline(
    request: Request,
    code: str,
    days: int = 30,
    kinds: str | None = None,
) -> dict[str, Any]:
    try:
        models.normalize_symbol(code)
    except ValueError as exc:
        raise HTTPException(400, "代码格式错误") from exc
    selected = set(kinds.split(",")) if kinds else None
    try:
        return _svc(request).timeline(code, days, selected)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/sync")
def sync(
    request: Request,
    kinds: str | None = Query(None, description="announcements,news,flash"),
    legacy_root: str | None = Query(None, description="一次性导入时指向包含 cninfo-crawler/stock-news 的目录"),
    force: bool = False,
    live: bool = False,
    date: str | None = Query(None, description="公告同步日期 YYYY-MM-DD，默认今天"),
) -> dict[str, Any]:
    selected = [k.strip() for k in kinds.split(",") if k.strip()] if kinds else ["announcements", "news", "flash"]
    unknown = [k for k in selected if k not in {"announcements", "news", "flash"}]
    if unknown:
        raise HTTPException(400, f"未知模块: {unknown}")
    if date:
        from datetime import date as date_cls

        try:
            date_cls.fromisoformat(date)
        except ValueError as exc:
            raise HTTPException(400, "date 必须是 YYYY-MM-DD") from exc
    result = _svc(request).sync(selected, legacy_root, force, live, date=date)
    return result


@router.post("/reload")
def reload(request: Request) -> dict[str, Any]:
    _svc(request).invalidate()
    return {"ok": True, "at": store.now_text()}
