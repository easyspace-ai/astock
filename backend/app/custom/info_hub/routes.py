"""资讯中心只读 API + 手动同步入口。"""
from __future__ import annotations

import threading
import time
from datetime import date as date_cls
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api.permissions import current_user, has_permission, require_page_access
from app.market_time import cn_now
from app.services import rbac

from . import config, models, service, store

router = APIRouter(
    prefix="/api/info",
    tags=["info"],
    dependencies=[Depends(require_page_access(
        rbac.PAGE_EXT_INFO_ANNOUNCEMENTS,
        rbac.PAGE_EXT_INFO_NEWS,
        rbac.PAGE_EXT_INFO_FLASH,
        rbac.PAGE_ANALYSIS_WILDCARD,
    ))],
)


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


# 手动抓取频率: 同账号短时间连点无意义 (单次抓取本身要几十秒), 且会重复打第三方源。
_MANUAL_COOLDOWN_S = 60.0
_ADMIN_COOLDOWN_S = 30.0
_NON_ADMIN_DATE_WINDOW_DAYS = 7
_LAST_TRIGGER: dict[str, float] = {}
_TRIGGER_LOCK = threading.Lock()


def _actor(request: Request) -> str:
    user = current_user(request)
    if user is None:
        return "token"
    return str(user.get("email") or user.get("id") or "account")


def _check_trigger_gap(actor: str, *, admin: bool) -> None:
    """同一账号的重复点击直接 429; 抓取是部署级共享资源, 不做排队。"""
    gap = _ADMIN_COOLDOWN_S if admin else _MANUAL_COOLDOWN_S
    now = time.monotonic()
    with _TRIGGER_LOCK:
        last = _LAST_TRIGGER.get(actor)
        if last is not None and now - last < gap:
            wait = int(gap - (now - last)) + 1
            raise HTTPException(
                429,
                f"抓取过于频繁，请 {wait} 秒后再试",
                headers={"Retry-After": str(wait)},
            )
        _LAST_TRIGGER[actor] = now


def _release_trigger(actor: str) -> None:
    """抓取没真正跑起来(409 已被别的任务占用)时退回冷却占位, 用户可立即重试。"""
    with _TRIGGER_LOCK:
        _LAST_TRIGGER.pop(actor, None)


def _check_manual_scope(date: str | None, *, admin: bool) -> None:
    """普通用户只能触发近期增量; 历史回补/强制重抓属于共享数据集的维护操作。"""
    if admin:
        return
    today = cn_now().date()
    if not date:
        return
    try:
        target = date_cls.fromisoformat(date)
    except ValueError as exc:
        raise HTTPException(400, "date 必须是 YYYY-MM-DD") from exc
    earliest = today - timedelta(days=_NON_ADMIN_DATE_WINDOW_DAYS - 1)
    if target > today or target < earliest:
        raise HTTPException(
            403,
            f"仅可手动补抓近 {_NON_ADMIN_DATE_WINDOW_DAYS} 天（{earliest} 至 {today}）；更早日期请由管理员操作",
        )


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
    admin = has_permission(request, rbac.ADMIN_DATA_MANAGE)
    if not admin:
        # 这两项是共享数据集的结构性维护, 与页面权限分开管。
        if legacy_root:
            raise HTTPException(403, "仅管理员可导入历史归档目录")
        if force:
            raise HTTPException(403, "仅管理员可强制重抓")
        if date:
            _check_manual_scope(date, admin=False)
    elif date:
        try:
            date_cls.fromisoformat(date)
        except ValueError as exc:
            raise HTTPException(400, "date 必须是 YYYY-MM-DD") from exc

    actor = _actor(request)
    _check_trigger_gap(actor, admin=admin)
    try:
        return _svc(request).sync(
            selected, legacy_root, force, live, date=date, wait=False, actor=actor,
        )
    except service.SyncBusyError as exc:
        _release_trigger(actor)
        raise HTTPException(409, str(exc)) from exc


@router.post("/reload")
def reload(request: Request) -> dict[str, Any]:
    _svc(request).invalidate()
    return {"ok": True, "at": store.now_text()}
