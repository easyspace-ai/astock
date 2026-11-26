"""权限目录与权限组默认值 — RBAC 的单一事实源。

设计:
  - 只做「纯数据 + 纯函数」, 不碰数据库 (存储层在 services/user_store.py)。
  - 权限码格式 `域.对象.动作`: page.* 控制页面可见性与路由, feature.* 控制功能写操作,
    admin.* 控制管理后台能力。前端拿到的是这份目录, 不允许前端另写一份清单。
  - 权限组 v1 只有三个内置组; 组内权限可在管理后台调整, 但「谁能改权限组」由代码
    硬约束 (见 api/admin_users.py), 不能通过改矩阵自提权。
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

# ── 权限组 key ─────────────────────────────────────────────
SUPER_ADMIN = "super_admin"
MEMBER_ADMIN = "admin"
EDITOR = "editor"
PERM_ROLES = (SUPER_ADMIN, MEMBER_ADMIN, EDITOR)
PERM_ROLE_LABELS: dict[str, str] = {
    SUPER_ADMIN: "超级管理员",
    MEMBER_ADMIN: "普通管理员",
    EDITOR: "编辑者",
}
DEFAULT_PERM_ROLE = EDITOR


@dataclass(frozen=True)
class Permission:
    key: str
    label: str
    group: str
    description: str = ""


# ── 权限码 ──────────────────────────────────────────────────
# 页面
PAGE_DASHBOARD = "page.dashboard"
PAGE_WATCHLIST = "page.watchlist"
PAGE_SCREENER = "page.screener"
PAGE_FACTORS = "page.factors"
PAGE_GROWTH = "page.growth"
PAGE_STOCK_ANALYSIS = "page.stock_analysis"
PAGE_BACKTEST = "page.backtest"
PAGE_REGIME = "page.regime"
PAGE_INDICES = "page.indices"
PAGE_ANALYSIS = "page.analysis"
PAGE_DEV = "page.dev"
# 功能
FEATURE_STRATEGY_EDIT = "feature.strategy_edit"
FEATURE_BACKTEST_RUN = "feature.backtest_run"
FEATURE_AI_USE = "feature.ai_use"
FEATURE_DATA_EXPORT = "feature.data_export"
# 管理
ADMIN_ACCESS = "admin.access"
ADMIN_SETTINGS_WRITE = "admin.settings_write"
ADMIN_DATA_MANAGE = "admin.data_manage"
ADMIN_PROMPTS_MANAGE = "admin.prompts_manage"
ADMIN_PAGES_MANAGE = "admin.pages_manage"
ADMIN_USERS_MANAGE = "admin.users_manage"
ADMIN_ROLES_MANAGE = "admin.roles_manage"
ADMIN_AUDIT_VIEW = "admin.audit_view"

GROUP_PAGE = "page"
GROUP_FEATURE = "feature"
GROUP_ADMIN = "admin"
GROUP_LABELS = {GROUP_PAGE: "页面权限", GROUP_FEATURE: "功能权限", GROUP_ADMIN: "管理权限"}

PERMISSIONS: tuple[Permission, ...] = (
    Permission(PAGE_DASHBOARD, "看板", GROUP_PAGE),
    Permission(PAGE_WATCHLIST, "自选", GROUP_PAGE),
    Permission(PAGE_SCREENER, "策略", GROUP_PAGE),
    Permission(PAGE_FACTORS, "因子", GROUP_PAGE),
    Permission(PAGE_GROWTH, "三年五倍", GROUP_PAGE),
    Permission(PAGE_STOCK_ANALYSIS, "个股分析", GROUP_PAGE),
    Permission(PAGE_BACKTEST, "回测", GROUP_PAGE),
    Permission(PAGE_REGIME, "市场环境", GROUP_PAGE),
    Permission(PAGE_INDICES, "指数", GROUP_PAGE),
    Permission(PAGE_ANALYSIS, "分析中心/扩展页", GROUP_PAGE),
    Permission(PAGE_DEV, "开发调试", GROUP_PAGE),
    Permission(FEATURE_STRATEGY_EDIT, "新建/修改/删除策略", GROUP_FEATURE),
    Permission(FEATURE_BACKTEST_RUN, "运行回测/优化", GROUP_FEATURE),
    Permission(FEATURE_AI_USE, "AI 助手/AI 分析", GROUP_FEATURE),
    Permission(FEATURE_DATA_EXPORT, "导出数据/配置", GROUP_FEATURE),
    Permission(ADMIN_ACCESS, "进入管理后台", GROUP_ADMIN),
    Permission(ADMIN_SETTINGS_WRITE, "修改系统配置", GROUP_ADMIN),
    Permission(ADMIN_DATA_MANAGE, "数据管理", GROUP_ADMIN),
    Permission(ADMIN_PROMPTS_MANAGE, "提示词管理", GROUP_ADMIN),
    Permission(ADMIN_PAGES_MANAGE, "页面插件管理", GROUP_ADMIN),
    Permission(ADMIN_USERS_MANAGE, "用户账号管理", GROUP_ADMIN),
    Permission(ADMIN_ROLES_MANAGE, "配置权限组", GROUP_ADMIN),
    Permission(ADMIN_AUDIT_VIEW, "查看审计日志", GROUP_ADMIN),
)

PERMISSION_KEYS: frozenset[str] = frozenset(p.key for p in PERMISSIONS)
PAGE_KEYS: tuple[str, ...] = tuple(p.key for p in PERMISSIONS if p.group == GROUP_PAGE)
FEATURE_KEYS: tuple[str, ...] = tuple(p.key for p in PERMISSIONS if p.group == GROUP_FEATURE)
ADMIN_KEYS: tuple[str, ...] = tuple(p.key for p in PERMISSIONS if p.group == GROUP_ADMIN)

# 超级管理员组必须保留的权限: 少了任意一条都会让管理员把自己锁在后台外。
SUPER_ADMIN_REQUIRED = (ADMIN_ACCESS, ADMIN_ROLES_MANAGE, ADMIN_USERS_MANAGE)


def all_permissions() -> frozenset[str]:
    return PERMISSION_KEYS


def default_permissions_for(perm_role: str) -> list[str]:
    """内置组的默认权限矩阵 (未命中返回空集, fail-closed)。保持目录顺序。"""
    ordered = [p.key for p in PERMISSIONS]
    if perm_role == SUPER_ADMIN:
        return ordered
    if perm_role == MEMBER_ADMIN:
        return [k for k in ordered if k not in (ADMIN_USERS_MANAGE, ADMIN_ROLES_MANAGE)]
    if perm_role == EDITOR:
        return [k for k in ordered if k in set(PAGE_KEYS) - {PAGE_DEV} or k in FEATURE_KEYS]
    return []


DEFAULT_GROUPS: dict[str, dict[str, object]] = {
    SUPER_ADMIN: {
        "name": PERM_ROLE_LABELS[SUPER_ADMIN],
        "description": "全部权限, 可管理用户与权限组",
        "permissions": default_permissions_for(SUPER_ADMIN),
    },
    MEMBER_ADMIN: {
        "name": PERM_ROLE_LABELS[MEMBER_ADMIN],
        "description": "可进入后台并修改系统配置, 不管理账号与权限组",
        "permissions": default_permissions_for(MEMBER_ADMIN),
    },
    EDITOR: {
        "name": PERM_ROLE_LABELS[EDITOR],
        "description": "前台全部功能, 不进入管理后台",
        "permissions": default_permissions_for(EDITOR),
    },
}


def default_perm_role_for_account_role(account_role: str | None) -> str:
    """账号等级 -> 权限组 (仅历史数据与新建用户的兜底映射)。"""
    return SUPER_ADMIN if account_role == "admin" else DEFAULT_PERM_ROLE


def validate_permissions(codes: Iterable[str]) -> list[str]:
    """校验权限码集合; 含未注册权限码时整批拒绝 (不静默丢弃)。

    保持目录顺序返回, 便于前端回显与 diff 稳定。
    """
    requested = set(codes)
    unknown = requested - PERMISSION_KEYS
    if unknown:
        raise ValueError(f"未注册的权限码: {', '.join(sorted(unknown))}")
    return [p.key for p in PERMISSIONS if p.key in requested]


def catalog() -> list[dict[str, str]]:
    return [
        {"key": p.key, "label": p.label, "group": p.group, "description": p.description}
        for p in PERMISSIONS
    ]
