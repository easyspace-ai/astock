"""统一导航树 — 前端侧栏、页面设置与权限目录共用 services/menu_registry.py。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.permissions import require_permission
from app.config import settings
from app.services import menu_registry, rbac

router = APIRouter(prefix="/api", tags=["navigation"])


class MenuLayoutPayload(BaseModel):
    """页面设置提交的菜单布局；service 层负责路由/分组校验。"""
    items: list[dict[str, Any]] = Field(default_factory=list)


def _data_dir(request: Request):
    store = getattr(getattr(request.app.state, "repo", None), "store", None)
    return getattr(store, "data_dir", None) or settings.data_dir


@router.get("/navigation/tree")
def navigation_tree(request: Request) -> dict:
    """返回完整菜单树；页面叶子带权限码，前端按权限过滤后渲染。"""
    return {"menus": menu_registry.menu_nodes(_data_dir(request))}


@router.put(
    "/navigation/layout",
    dependencies=[Depends(require_permission(rbac.ADMIN_PAGES_MANAGE))],
)
def update_navigation_layout(payload: MenuLayoutPayload, request: Request) -> dict:
    """保存后端统一菜单树的分组/排序；权限目录与侧栏下次请求即读同一棵树。"""
    try:
        menus = menu_registry.save_layout(_data_dir(request), payload.items)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"menus": menus}
