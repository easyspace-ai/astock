"""权限守卫 — 管理后台 / 普通用户功能边界。

设计约定:
  - 判定一律走权限码 (`services/rbac.py`), 不再拿账号等级 `role == 'admin'` 当管理员。
  - 无账号会话(API Token 通道/内部调用)视为全权限, 由 Token scope 或调用方负责边界。
  - 前端隐藏菜单只是体验, 真正的边界在这里。
  - 未知权限组、脏权限数据 -> 空权限集 (fail-closed)。

本模块只依赖 request.state.user（由 main.py 认证中间件注入）。
"""
from __future__ import annotations

from fastapi import HTTPException, Request

from app.services import rbac, user_store

# 普通用户也可写的个人偏好接口 — 只影响自己的界面, 不碰系统配置。
PERSONAL_SETTINGS_PATHS = frozenset({
    "/api/settings/preferences/nav-layout",
    "/api/settings/preferences/nav-hidden",
    "/api/settings/preferences/nav-order",
    "/api/settings/preferences/dashboard-layout",
    "/api/settings/preferences/ui-prefs",
    "/api/settings/preferences/assistant-auto-approve",
    "/api/settings/preferences/assistant-mask",
    "/api/settings/preferences/watchlist-columns",
    "/api/settings/preferences/screener-result-columns",
    "/api/settings/preferences/watchlist-groups-in-nav",
})

# 只读但含凭据/密钥清单的接口, 普通用户也不下发。
ADMIN_ONLY_GET_PREFIXES = ("/api/settings/api-tokens",)


def current_user(request: Request) -> dict | None:
    return getattr(request.state, "user", None)


def _request_permissions(request: Request) -> frozenset[str]:
    """单次请求内解析一次权限集（页面 anyOf 会查多个权限码，避免重复查库）。"""
    cached = getattr(request.state, "permissions", None)
    if cached is None:
        cached = user_store.user_permissions(current_user(request))
        request.state.permissions = cached
    return cached


def _permission_matches(perms: frozenset[str], key: str) -> bool:
    """精确匹配或末段通配：`page.ext.*` 可匹配任意扩展页叶子权限。"""
    if key in perms:
        return True
    if key.endswith(".*"):
        prefix = key[:-1]
        return any(p.startswith(prefix) for p in perms)
    return False


def has_permission(request: Request, key: str) -> bool:
    """当前请求是否拥有权限码。无账号会话(API Token/内部调用)按全权限处理。"""
    user = current_user(request)
    if user is None:
        return True
    # 超级管理员始终拥有全部权限；动态新增页面不需要每次迁移权限组。
    if user_store.effective_perm_role(user) == rbac.SUPER_ADMIN:
        return True
    return _permission_matches(_request_permissions(request), key)


def is_admin(request: Request) -> bool:
    """是否拥有进入管理后台的权限（无账号会话恒为真）。"""
    return has_permission(request, rbac.ADMIN_ACCESS)


def require_permission(key: str):
    """依赖工厂: 无该权限码 -> 403。供各路由 `dependencies=[Depends(...)]` 使用。"""

    def _guard(request: Request) -> None:
        if not has_permission(request, key):
            raise HTTPException(status_code=403, detail="无权限执行该操作")

    _guard.__name__ = f"require_{key.replace('.', '_')}"
    return _guard


def require_page_access(*pages: str):
    """依赖工厂: 页面权限 anyOf。

    业务数据接口按「哪些页面会读它」登记（见 services/page_access.py）。持有其中
    任一页面权限即可访问；一个都没有 -> 403。无账号会话仍按全权限处理。
    """

    def _guard(request: Request) -> None:
        if not any(has_permission(request, key) for key in pages):
            raise HTTPException(status_code=403, detail="当前账号无该页面的数据访问权限")

    _guard.__name__ = "require_page_access"
    return _guard


def require_admin(request: Request) -> None:
    """依赖: 无后台访问权限 -> 403。"""
    if not is_admin(request):
        raise HTTPException(status_code=403, detail="仅管理员可访问该功能")


def guard_settings_access(request: Request) -> None:
    """设置路由守卫。

    允许: 有系统配置写权限的账号全部；所有人只读 GET；普通用户写个人偏好白名单。
    其余写操作(数据源/AI/SMTP/定时任务/批量参数/备份/开放接口) → 403。
    """
    if has_permission(request, rbac.ADMIN_SETTINGS_WRITE):
        return
    if request.method in ("GET", "HEAD", "OPTIONS"):
        if any(request.url.path.startswith(p) for p in ADMIN_ONLY_GET_PREFIXES):
            raise HTTPException(status_code=403, detail="仅管理员可查看该配置")
        return
    if request.url.path in PERSONAL_SETTINGS_PATHS:
        return
    raise HTTPException(status_code=403, detail="仅管理员可修改系统配置")


def guard_ext_pages_access(request: Request) -> None:
    """页面插件: 有页面权限的账号可加载 bundle.js（扩展页运行需要），管理操作仅管理员。"""
    if has_permission(request, rbac.ADMIN_PAGES_MANAGE):
        return
    if (
        request.method == "GET"
        and (
            request.url.path in ("/api/ext-pages", "/api/ext-pages/")
            or request.url.path.endswith("/bundle.js")
        )
        and (has_permission(request, rbac.PAGE_EXT_WILDCARD)
             or has_permission(request, rbac.PAGE_ANALYSIS_WILDCARD))
    ):
        return
    raise HTTPException(status_code=403, detail="仅管理员可管理页面插件")


def guard_ext_data_access(request: Request) -> None:
    """扩展数据: 普通用户可读数据(扩展页展示需要)，配置与写入仅管理员。"""
    if has_permission(request, rbac.ADMIN_DATA_MANAGE):
        return
    path = request.url.path
    if request.method == "GET" and not path.endswith("/api-key") and not path.endswith("/config/export"):
        return
    raise HTTPException(status_code=403, detail="仅管理员可修改扩展数据配置")
