"""权限目录与权限组默认值 — RBAC 的单一事实源。

设计:
  - 只做「纯数据 + 纯函数」, 不碰数据库 (存储层在 services/user_store.py)。
  - 权限码格式 `域.对象.动作`: page.* 控制页面可见性与路由, feature.* 控制功能写操作,
    admin.* 控制管理后台能力。前端拿到的是这份目录, 不允许前端另写一份清单。
  - page.* 落到具体菜单叶子页面; 纯分组节点只提供层级与全选入口, 不保存权限码。
  - 分析中心与运行时扩展页的叶子权限由 services/menu_registry.py 动态合并。
"""
from __future__ import annotations

import re
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
    parent: str | None = None
    order: int = 0
    kind: str = "permission"  # permission | group
    route: str | None = None
    source: str = "core"


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
PAGE_DEV = "page.dev"

# 扩展页面权限统一按路由生成, 保证新增/删除扩展页时权限码稳定且可读。
PAGE_EXT_PREFIX = "page.ext."
PAGE_ANALYSIS_PREFIX = "page.analysis."
# 旧版把全部分析中心/扩展页合并为一个权限码; 仅用于迁移与兼容读取。
PAGE_ANALYSIS = "page.analysis"
PAGE_ANALYSIS_WILDCARD = "page.analysis.*"
PAGE_EXT_WILDCARD = "page.ext.*"


def extension_page_key(route: str) -> str:
    """扩展页路由 -> 稳定权限码。路径段用双下划线连接, 避免 /info/news 与 /info-news 撞码。"""
    parts = [re.sub(r"[^a-zA-Z0-9]+", "_", seg).strip("_") for seg in route.strip("/").split("/") if seg]
    return f"{PAGE_EXT_PREFIX}route.{'__'.join(parts)}"


PAGE_EXT_LIMIT_LADDER = extension_page_key("/limit-ladder")
PAGE_EXT_CONCEPT_ANALYSIS = extension_page_key("/concept-analysis")
PAGE_EXT_INDUSTRY_ANALYSIS = extension_page_key("/industry-analysis")
PAGE_EXT_FINANCIALS = extension_page_key("/financials")
PAGE_EXT_MONITOR = extension_page_key("/monitor")
PAGE_EXT_ABNORMAL = extension_page_key("/abnormal")
PAGE_EXT_LOTS = extension_page_key("/lots")
PAGE_EXT_PAPER = extension_page_key("/paper")
PAGE_EXT_SIGNALS = extension_page_key("/signals")
PAGE_EXT_REVIEW = extension_page_key("/review")
PAGE_EXT_ASSISTANT = extension_page_key("/assistant")
PAGE_EXT_INFO_ANNOUNCEMENTS = extension_page_key("/info/announcements")
PAGE_EXT_INFO_NEWS = extension_page_key("/info/news")
PAGE_EXT_INFO_FLASH = extension_page_key("/info/flash")
PAGE_EXT_COMPANY_GRAPH = extension_page_key("/company/graph")

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

# 菜单分组节点 (不是权限码, 只用于权限树与前端导航的层级/全选)
MENU_MARKET = "menu.market"
MENU_STRATEGY = "menu.strategy"
MENU_STOCK = "menu.stock"
MENU_MARKET_ANALYSIS = "menu.market-analysis"
MENU_INFO = "menu.info"
MENU_TRADING = "menu.trading"
MENU_ANALYSIS = "menu.analysis"
MENU_EXTENSIONS = "menu.extensions"
MENU_SYSTEM = "menu.system"
MENU_ADMIN = "menu.admin"
GROUP_ADMIN_PERMISSIONS = "group.admin-permissions"
MENU_FEATURES = "menu.features"


def _group(key: str, label: str, group: str, order: int, parent: str | None = None) -> Permission:
    return Permission(key, label, group, kind="group", parent=parent, order=order)


def _page(
    key: str,
    label: str,
    parent: str | None,
    order: int,
    route: str,
    source: str = "extension",
    description: str = "",
) -> Permission:
    return Permission(
        key, label, GROUP_PAGE, description=description, parent=parent,
        order=order, kind="permission", route=route, source=source,
    )


MENU_GROUPS: tuple[Permission, ...] = (
    _group(MENU_MARKET, "行情看板", GROUP_PAGE, 10),
    _group(MENU_STRATEGY, "策略研究", GROUP_PAGE, 20),
    _group(MENU_STOCK, "个股研究", GROUP_PAGE, 30),
    _group(MENU_MARKET_ANALYSIS, "市场分析", GROUP_PAGE, 40),
    _group(MENU_INFO, "资讯与监控", GROUP_PAGE, 50),
    _group(MENU_TRADING, "交易与复盘", GROUP_PAGE, 60),
    _group(MENU_ANALYSIS, "分析中心", GROUP_PAGE, 70),
    _group(MENU_EXTENSIONS, "扩展页", GROUP_PAGE, 80),
    _group(MENU_SYSTEM, "系统", GROUP_PAGE, 90),
    _group(MENU_ADMIN, "管理后台", GROUP_ADMIN, 100),
    _group(GROUP_ADMIN_PERMISSIONS, "用户与权限", GROUP_ADMIN, 140, MENU_ADMIN),
    _group(MENU_FEATURES, "功能操作", GROUP_FEATURE, 200),
)

PERMISSIONS: tuple[Permission, ...] = (
    # 看板固定为顶层第一个页面, 其余行情页归入分组。
    _page(PAGE_DASHBOARD, "看板", None, 5, "/", source="core"),
    _page(PAGE_WATCHLIST, "自选", MENU_MARKET, 11, "/watchlist", source="core"),
    _page(PAGE_INDICES, "指数", MENU_MARKET, 12, "/indices", source="core"),
    _page(PAGE_REGIME, "市场环境", MENU_MARKET, 13, "/regime", source="core"),
    _page(PAGE_SCREENER, "策略", MENU_STRATEGY, 21, "/screener", source="core"),
    _page(PAGE_FACTORS, "因子", MENU_STRATEGY, 22, "/factors", source="core"),
    _page(PAGE_GROWTH, "三年五倍", MENU_STRATEGY, 23, "/growth", source="core"),
    _page(PAGE_BACKTEST, "回测", MENU_STRATEGY, 24, "/backtest", source="core"),
    _page(PAGE_STOCK_ANALYSIS, "个股分析", MENU_STOCK, 31, "/stock-analysis", source="core"),
    _page(PAGE_EXT_FINANCIALS, "财务概况", MENU_STOCK, 32, "/financials"),
    _page(PAGE_EXT_COMPANY_GRAPH, "公司图谱", MENU_STOCK, 33, "/company/graph"),
    _page(PAGE_EXT_LIMIT_LADDER, "连板梯队", MENU_MARKET_ANALYSIS, 41, "/limit-ladder"),
    _page(PAGE_EXT_CONCEPT_ANALYSIS, "概念板块", MENU_MARKET_ANALYSIS, 42, "/concept-analysis"),
    _page(PAGE_EXT_INDUSTRY_ANALYSIS, "行业板块", MENU_MARKET_ANALYSIS, 43, "/industry-analysis"),
    _page(PAGE_EXT_ABNORMAL, "异动监控", MENU_MARKET_ANALYSIS, 44, "/abnormal"),
    _page(PAGE_EXT_INFO_ANNOUNCEMENTS, "公告中心", MENU_INFO, 51, "/info/announcements"),
    _page(PAGE_EXT_INFO_NEWS, "新闻中心", MENU_INFO, 52, "/info/news"),
    _page(PAGE_EXT_INFO_FLASH, "快讯", MENU_INFO, 53, "/info/flash"),
    _page(PAGE_EXT_MONITOR, "监控中心", MENU_INFO, 54, "/monitor"),
    _page(PAGE_EXT_SIGNALS, "信号库", MENU_INFO, 55, "/signals"),
    _page(PAGE_EXT_LOTS, "持仓提醒", MENU_INFO, 56, "/lots"),
    _page(PAGE_EXT_PAPER, "模拟盘", MENU_TRADING, 61, "/paper"),
    _page(PAGE_EXT_REVIEW, "复盘", MENU_TRADING, 62, "/review"),
    _page(PAGE_EXT_ASSISTANT, "AI 助手", MENU_TRADING, 63, "/assistant"),
    _page(PAGE_DEV, "开发调试", MENU_SYSTEM, 91, "/dev", source="core"),

    # 功能权限
    Permission(FEATURE_STRATEGY_EDIT, "新建/修改/删除策略", GROUP_FEATURE, parent=MENU_FEATURES, order=201, kind="permission"),
    Permission(FEATURE_BACKTEST_RUN, "运行回测/优化", GROUP_FEATURE, parent=MENU_FEATURES, order=202, kind="permission"),
    Permission(FEATURE_AI_USE, "AI 助手/AI 分析", GROUP_FEATURE, parent=MENU_FEATURES, order=203, kind="permission"),
    Permission(FEATURE_DATA_EXPORT, "导出数据/配置", GROUP_FEATURE, parent=MENU_FEATURES, order=204, kind="permission"),

    # 管理权限
    Permission(ADMIN_ACCESS, "进入管理后台", GROUP_ADMIN, parent=MENU_ADMIN, order=101, kind="permission"),
    Permission(ADMIN_SETTINGS_WRITE, "系统配置", GROUP_ADMIN, parent=MENU_ADMIN, order=102, kind="permission", route="/admin"),
    Permission(ADMIN_DATA_MANAGE, "数据管理", GROUP_ADMIN, parent=MENU_ADMIN, order=103, kind="permission", route="/admin/data"),
    Permission(ADMIN_PROMPTS_MANAGE, "提示词管理", GROUP_ADMIN, parent=MENU_ADMIN, order=104, kind="permission", route="/admin/prompts"),
    Permission(ADMIN_PAGES_MANAGE, "页面插件管理", GROUP_ADMIN, parent=MENU_ADMIN, order=105, kind="permission", route="/admin?tab=pages"),
    Permission(ADMIN_USERS_MANAGE, "用户账号", GROUP_ADMIN, parent=GROUP_ADMIN_PERMISSIONS, order=141, kind="permission", route="/admin/permissions?tab=users"),
    Permission(ADMIN_ROLES_MANAGE, "权限组", GROUP_ADMIN, parent=GROUP_ADMIN_PERMISSIONS, order=142, kind="permission", route="/admin/permissions?tab=groups"),
    Permission(ADMIN_AUDIT_VIEW, "变更记录", GROUP_ADMIN, parent=GROUP_ADMIN_PERMISSIONS, order=143, kind="permission", route="/admin/permissions?tab=audit"),
)

PERMISSION_KEYS: frozenset[str] = frozenset(p.key for p in PERMISSIONS)
PAGE_KEYS: tuple[str, ...] = tuple(p.key for p in PERMISSIONS if p.group == GROUP_PAGE)
FEATURE_KEYS: tuple[str, ...] = tuple(p.key for p in PERMISSIONS if p.group == GROUP_FEATURE)
ADMIN_KEYS: tuple[str, ...] = tuple(p.key for p in PERMISSIONS if p.group == GROUP_ADMIN)

# 超级管理员组必须保留的权限: 少了任意一条都会让管理员把自己锁在后台外。
SUPER_ADMIN_REQUIRED = (ADMIN_ACCESS, ADMIN_ROLES_MANAGE, ADMIN_USERS_MANAGE)

_DYNAMIC_KEY_RE = re.compile(r"^page\.(analysis|ext)\.[a-zA-Z0-9_.-]+$")


def is_valid_permission(key: str) -> bool:
    """静态叶子权限或合法的动态页面权限码。分组节点/旧 page.analysis 不是权限码。"""
    return key in PERMISSION_KEYS or bool(_DYNAMIC_KEY_RE.match(key))


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

    保持目录顺序返回, 动态页面权限按字典序追加, 便于前端回显与 diff 稳定。
    """
    requested = set(codes)
    unknown = {c for c in requested if not is_valid_permission(c)}
    if unknown:
        raise ValueError(f"未注册的权限码: {', '.join(sorted(unknown))}")
    ordered = [p.key for p in PERMISSIONS if p.key in requested]
    ordered.extend(sorted(c for c in requested if c not in PERMISSION_KEYS))
    return ordered


def menu_nodes() -> list[dict[str, object]]:
    """静态菜单树节点 (分组 + 叶子)。动态节点由 menu_registry 合并。"""
    nodes = [
        {
            "key": p.key, "label": p.label, "group": p.group, "description": p.description,
            "parent": p.parent, "order": p.order, "kind": p.kind, "route": p.route,
            "source": p.source, "visible": True,
        }
        for p in (*MENU_GROUPS, *PERMISSIONS)
    ]
    return sorted(nodes, key=lambda n: (int(n["order"]), str(n["key"])))


def catalog() -> list[dict[str, object]]:
    """叶子权限目录 (不含纯分组节点), 供旧接口/测试与前端权限组使用。"""
    return [
        {
            "key": p.key, "label": p.label, "group": p.group, "description": p.description,
            "parent": p.parent or "", "order": p.order, "kind": p.kind,
            "route": p.route or "", "source": p.source,
        }
        for p in PERMISSIONS
    ]
