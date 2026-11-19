"""角色权限守卫 — 管理后台 / 普通用户功能边界。

设计约定:
  - 访问密码模式(无邮箱账号会话) = 单用户部署, 视为管理员, 保持原有行为。
  - 邮箱账号模式按 effective_role 判定: 仅 admin 可改系统配置。
  - 前端隐藏菜单只是体验, 真正的边界在这里。

本模块只依赖 request.state.user（由 main.py 认证中间件注入）。
"""
from __future__ import annotations

from fastapi import HTTPException, Request

from app.services import user_store

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


def current_user(request: Request) -> dict | None:
    return getattr(request.state, "user", None)


def is_admin(request: Request) -> bool:
    """当前请求是否为管理员（访问密码模式视为管理员）。"""
    me = current_user(request)
    if me is None:
        return True
    return user_store.effective_role(me) == user_store.ROLE_ADMIN


def require_admin(request: Request) -> None:
    """依赖: 非管理员 → 403。"""
    if not is_admin(request):
        raise HTTPException(status_code=403, detail="仅管理员可访问该功能")


# 只读但含凭据/密钥清单的接口, 普通用户也不下发。
ADMIN_ONLY_GET_PREFIXES = ("/api/settings/api-tokens",)


def guard_settings_access(request: Request) -> None:
    """设置路由守卫。

    允许: 管理员全部；所有人只读 GET；普通用户写个人偏好白名单。
    其余写操作(数据源/AI/SMTP/定时任务/批量参数/备份/开放接口) → 403。
    """
    if is_admin(request):
        return
    if request.method in ("GET", "HEAD", "OPTIONS"):
        if any(request.url.path.startswith(p) for p in ADMIN_ONLY_GET_PREFIXES):
            raise HTTPException(status_code=403, detail="仅管理员可查看该配置")
        return
    if request.url.path in PERSONAL_SETTINGS_PATHS:
        return
    raise HTTPException(status_code=403, detail="仅管理员可修改系统配置")


def guard_ext_pages_access(request: Request) -> None:
    """页面插件: 普通用户可加载 bundle.js（扩展页运行需要），管理操作仅管理员。"""
    if is_admin(request):
        return
    if request.method == "GET" and request.url.path.endswith("/bundle.js"):
        return
    raise HTTPException(status_code=403, detail="仅管理员可管理页面插件")


def guard_ext_data_access(request: Request) -> None:
    """扩展数据: 普通用户可读数据(扩展页展示需要)，配置与写入仅管理员。"""
    if is_admin(request):
        return
    path = request.url.path
    if request.method == "GET" and not path.endswith("/api-key") and not path.endswith("/config/export"):
        return
    raise HTTPException(status_code=403, detail="仅管理员可修改扩展数据配置")
