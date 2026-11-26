"""权限体系测试 — 权限组、权限判定、管理接口与守卫。

覆盖:
  - 权限组播种 / 建表迁移 / 历史数据回填
  - 权限判定: 三组、未知组 fail-closed、无账号会话全权限
  - 管理接口: 越权 403、自改 409、最后一个超管保护、非法权限码 400
  - 守卫契约: settings 个人偏好白名单、管理接口全路由挂载权限依赖
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.requests import Request as StarletteRequest

from app.api import accounts as accounts_api
from app.api import admin_users as admin_users_api
from app.api import navigation as navigation_api
from app.api import permissions as permissions_api
from app.services import menu_registry, rbac, user_store


@pytest.fixture()
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from app.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    accounts_api._fail_counter.clear()
    accounts_api._code_send_times.clear()
    return tmp_path


def _make_client(user: dict | None) -> TestClient:
    """挂管理路由, 并注入 request.state.user（模拟中间件行为）。"""
    app = FastAPI()
    app.include_router(admin_users_api.router)

    @app.middleware("http")
    async def inject_user(request: Request, call_next):
        request.state.user = user
        return await call_next(request)

    return TestClient(app, client=("127.0.0.1", 12345))


def _super() -> dict:
    u = user_store.create_user("root@example.com", "secret123")
    assert u["perm_role"] == rbac.SUPER_ADMIN
    return u


def _member(perm_role: str, email: str) -> dict:
    """建一个指定权限组的账号；确保系统里已有一个超管，避免首个用户被降级。"""
    if not user_store.has_users():
        _super()
    u = user_store.create_user(email, "secret123")
    return user_store.apply_user_admin_update(u["id"], perm_role=perm_role, actor=None)


# ================================================================
# 模型层
# ================================================================
def test_groups_seeded_with_defaults(data_dir):
    groups = {g["key"]: g for g in user_store.list_role_groups()}
    assert set(groups) == set(rbac.PERM_ROLES)
    assert rbac.ADMIN_USERS_MANAGE in groups[rbac.SUPER_ADMIN]["permissions"]
    assert rbac.ADMIN_USERS_MANAGE not in groups[rbac.MEMBER_ADMIN]["permissions"]
    assert rbac.ADMIN_ACCESS not in groups[rbac.EDITOR]["permissions"]
    assert rbac.PAGE_WATCHLIST in groups[rbac.EDITOR]["permissions"]
    assert rbac.PAGE_DEV not in groups[rbac.EDITOR]["permissions"]


def test_first_user_is_super_admin(data_dir):
    u = user_store.create_user("boss@example.com", "secret123")
    assert u["perm_role"] == rbac.SUPER_ADMIN
    assert rbac.ADMIN_ACCESS in user_store.user_permissions(u)


def test_default_account_role_mapping(data_dir):
    admin = user_store.create_user("a@example.com", "secret123")
    free = user_store.create_user("b@example.com", "secret123")
    assert admin["perm_role"] == rbac.SUPER_ADMIN
    assert free["perm_role"] == rbac.EDITOR
    assert free["role"] == user_store.ROLE_FREE  # 账号等级/配额未受影响


def test_no_account_session_has_all_permissions(data_dir):
    assert user_store.user_permissions(None) == rbac.all_permissions()


def test_unknown_group_fails_closed(data_dir):
    u = user_store.create_user("x@example.com", "secret123")
    with user_store._connect() as conn:
        conn.execute("UPDATE users SET perm_role = 'ghost' WHERE id = ?", (u["id"],))
    ghost = user_store.get_user(u["id"])
    assert ghost is not None
    # 未知组名不在 PERM_ROLES: 解析为空权限集 (fail-closed), 不静默当管理员
    assert user_store.effective_perm_role(ghost) == "ghost"
    assert user_store.user_permissions(ghost) == frozenset()
    assert user_store.permissions_for_group("ghost") == frozenset()


def test_migration_backfills_and_is_idempotent(data_dir):
    u = user_store.create_user("old@example.com", "secret123")
    with user_store._connect() as conn:
        conn.execute("UPDATE users SET role = ?, perm_role = NULL WHERE id = ?", (user_store.ROLE_ADMIN, u["id"]))
    user_store.run_schema_migrations()
    user_store.run_schema_migrations()  # 重复启动不产生副作用
    u2 = user_store.get_user(u["id"])
    assert u2 is not None
    assert u2["perm_role"] == rbac.SUPER_ADMIN


def test_editor_cannot_access_admin(data_dir):
    editor = _member(rbac.EDITOR, "e@example.com")
    client = _make_client(editor)
    assert client.get("/api/admin/users").status_code == 403
    assert client.get("/api/admin/permission-catalog").status_code == 403


def test_editor_has_page_permissions(data_dir):
    editor = _member(rbac.EDITOR, "e2@example.com")
    perms = user_store.user_permissions(editor)
    assert rbac.PAGE_WATCHLIST in perms
    assert rbac.ADMIN_ACCESS not in perms
    assert rbac.FEATURE_BACKTEST_RUN in perms


# ================================================================
# 管理接口
# ================================================================
def test_user_list_requires_users_manage(data_dir):
    super_user = _super()
    member = _member(rbac.MEMBER_ADMIN, "m@example.com")
    # 默认矩阵: 普通管理员没有用户管理权, 列表不可见
    assert _make_client(member).get("/api/admin/users").status_code == 403

    # 超管给「普通管理员」组开用户管理权后, 同一账号下一次请求即可见(实时查库)
    group = user_store.get_role_group(rbac.MEMBER_ADMIN)
    assert group is not None
    user_store.update_role_group(
        rbac.MEMBER_ADMIN,
        permissions=[*group["permissions"], rbac.ADMIN_USERS_MANAGE],
        actor=super_user,
    )
    res = _make_client(member).get("/api/admin/users")
    assert res.status_code == 200
    body = res.json()
    assert body["total"] >= 2
    assert "password_hash" not in body["items"][0]


def test_member_admin_cannot_manage_groups(data_dir):
    _super()
    member = _member(rbac.MEMBER_ADMIN, "m2@example.com")
    client = _make_client(member)
    res = client.put("/api/admin/role-groups/editor", json={"permissions": [rbac.PAGE_WATCHLIST]})
    assert res.status_code == 403


def test_cannot_change_self(data_dir):
    super_user = _super()
    res = _make_client(super_user).patch(
        f"/api/admin/users/{super_user['id']}", json={"perm_role": rbac.EDITOR}
    )
    assert res.status_code == 409


def test_member_admin_cannot_assign_super_admin(data_dir):
    _super()
    member = _member(rbac.MEMBER_ADMIN, "m3@example.com")
    editor = _member(rbac.EDITOR, "e3@example.com")
    res = _make_client(member).patch(
        f"/api/admin/users/{editor['id']}", json={"perm_role": rbac.SUPER_ADMIN}
    )
    assert res.status_code == 403


def test_cannot_demote_last_super_admin(data_dir):
    s1 = _super()
    s2 = _member(rbac.SUPER_ADMIN, "s2@example.com")
    # 两个超管时可以降一个
    user_store.apply_user_admin_update(s1["id"], perm_role=rbac.EDITOR, actor=s2)
    assert user_store.count_super_admins() == 1
    # 只剩一个时禁止再降
    with pytest.raises(ValueError, match="至少一个"):
        user_store.apply_user_admin_update(s2["id"], perm_role=rbac.EDITOR, actor=None)


def test_super_admin_can_change_other_user(data_dir):
    super_user = _super()
    editor = _member(rbac.EDITOR, "e4@example.com")
    res = _make_client(super_user).patch(
        f"/api/admin/users/{editor['id']}", json={"perm_role": rbac.MEMBER_ADMIN}
    )
    assert res.status_code == 200
    assert res.json()["user"]["perm_role"] == rbac.MEMBER_ADMIN
    audit = user_store.list_audit(limit=1)
    assert audit and audit[0]["action"] == "user.admin_update"


def test_update_group_rejects_unknown_permission(data_dir):
    super_user = _super()
    res = _make_client(super_user).put(
        "/api/admin/role-groups/editor", json={"permissions": [rbac.PAGE_WATCHLIST, "page.unknown"]}
    )
    assert res.status_code == 400


def test_super_group_cannot_lose_critical_permissions(data_dir):
    super_user = _super()
    res = _make_client(super_user).put(
        "/api/admin/role-groups/super_admin",
        json={"permissions": [rbac.PAGE_WATCHLIST]},
    )
    assert res.status_code == 400


def test_update_group_persists_and_recorded_in_audit(data_dir):
    super_user = _super()
    client = _make_client(super_user)
    res = client.put(
        "/api/admin/role-groups/editor",
        json={"permissions": [rbac.PAGE_WATCHLIST, rbac.FEATURE_AI_USE]},
    )
    assert res.status_code == 200
    assert set(res.json()["group"]["permissions"]) == {rbac.PAGE_WATCHLIST, rbac.FEATURE_AI_USE}
    editor = _member(rbac.EDITOR, "e5@example.com")
    assert rbac.ADMIN_ACCESS not in user_store.user_permissions(editor)


def test_every_admin_route_has_permission_dependency(data_dir):
    """漏挂权限依赖的管理路由 = 潜在越权面。用结构断言兜住。"""
    routes = [r for r in admin_users_api.router.routes if getattr(r, "methods", None)]
    assert routes
    for route in routes:
        deps = getattr(route.dependant, "dependencies", [])
        assert deps, f"{route.path} 未挂权限依赖"
        assert any(getattr(d.call, "__name__", "").startswith("require_") for d in deps), route.path


# ================================================================
# /api/accounts/me 下发权限
# ================================================================
def test_me_returns_permissions(data_dir):
    app = FastAPI()
    app.include_router(accounts_api.router)

    @app.middleware("http")
    async def inject_user(request: Request, call_next):
        token = request.cookies.get(accounts_api.COOKIE_NAME)
        request.state.user = user_store.get_user_by_token(token) if token else None
        return await call_next(request)

    client = TestClient(app, client=("127.0.0.1", 12345))
    editor = _member(rbac.EDITOR, "me@example.com")
    token = user_store.create_session(editor["id"])
    client.cookies.set(accounts_api.COOKIE_NAME, token)
    body = client.get("/api/accounts/me").json()["user"]
    assert body["perm_role"] == rbac.EDITOR
    assert body["perm_role_name"] == "编辑者"
    assert rbac.PAGE_WATCHLIST in body["permissions"]
    assert body["is_admin"] is False


# ================================================================
# 守卫契约（不启动整个 app, 直接构造请求）
# ================================================================
def _request(method: str, path: str, user: dict | None) -> StarletteRequest:
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [],
        "scheme": "http",
        "server": ("testserver", 80),
        "client": ("127.0.0.1", 12345),
    }
    req = StarletteRequest(scope)
    req.state.user = user
    return req


def test_settings_guard_editor_denied_write(data_dir):
    editor = _member(rbac.EDITOR, "g@example.com")
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        permissions_api.guard_settings_access(_request("POST", "/api/settings/tickflow-key", editor))
    assert exc.value.status_code == 403
    # 读与个人偏好写仍放行
    permissions_api.guard_settings_access(_request("GET", "/api/settings", editor))
    permissions_api.guard_settings_access(
        _request("POST", "/api/settings/preferences/nav-layout", editor)
    )


def test_settings_guard_member_admin_allowed_write(data_dir):
    _super()
    member = _member(rbac.MEMBER_ADMIN, "g2@example.com")
    permissions_api.guard_settings_access(_request("POST", "/api/settings/tickflow-key", member))


def test_ext_pages_bundle_allowed_for_page_permission(data_dir):
    editor = _member(rbac.EDITOR, "g3@example.com")
    permissions_api.guard_ext_pages_access(
        _request("GET", "/api/ext-pages/foo/bundle.js", editor)
    )
    # 运行时页面插件清单是前端启动注册所需，持有扩展页权限即可读
    permissions_api.guard_ext_pages_access(_request("GET", "/api/ext-pages", editor))
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        permissions_api.guard_ext_pages_access(
            _request("POST", "/api/ext-pages/foo/install", editor)
        )


# ================================================================
# 统一菜单树 / 叶子权限目录
# ================================================================
def test_menu_tree_has_more_than_twenty_page_leaves():
    menus = rbac.menu_nodes()
    page_leaves = [m for m in menus if m["kind"] == "permission" and m["group"] == rbac.GROUP_PAGE]
    assert len(page_leaves) == len(rbac.PAGE_KEYS) >= 24
    # 分组节点不保存权限码
    for node in menus:
        if node["kind"] == "group":
            assert node["key"].startswith("menu.") or node["key"].startswith("group.")


def test_catalog_only_returns_leaf_permissions():
    catalog = rbac.catalog()
    keys = {p["key"] for p in catalog}
    assert all(not p["key"].startswith("menu.") for p in catalog)
    assert all(not p["key"].startswith("group.") for p in catalog)
    assert rbac.PAGE_WATCHLIST in keys
    assert rbac.PAGE_ANALYSIS not in keys


def test_dynamic_analysis_and_runtime_page_auto_enter_catalog(data_dir):
    analysis_dir = data_dir / "analysis_menus"
    analysis_dir.mkdir(parents=True, exist_ok=True)
    (analysis_dir / "news_flow.json").write_text(
        '{"id":"news_flow","label":"新闻流","order":1}', encoding="utf-8"
    )
    pack_dir = data_dir / "ext_pages" / "custom_page"
    pack_dir.mkdir(parents=True, exist_ok=True)
    (pack_dir / "page.json").write_text(
        '{"id":"custom_page","enabled":true,"label":"自定义页","path":"/custom-page","uploaded_at":10}',
        encoding="utf-8",
    )

    menus = menu_registry.menu_nodes(data_dir)
    keys = {m["key"] for m in menus}
    assert "page.analysis.news_flow" in keys
    assert rbac.extension_page_key("/custom-page") in keys
    assert "page.analysis.news_flow" in {p["key"] for p in menu_registry.permission_catalog(data_dir)}

    # 动态页面默认不授权
    assert "page.analysis.news_flow" not in user_store.permissions_for_group(rbac.EDITOR)
    assert rbac.extension_page_key("/custom-page") not in user_store.permissions_for_group(rbac.EDITOR)


def test_source_page_manifest_auto_adds_new_page(data_dir, monkeypatch, tmp_path):
    manifest = tmp_path / "navigation-manifest.json"
    manifest.write_text(
        '{"version":1,"pages":['
        '{"path":"/financials","label":"财务概况","order":100},'
        '{"path":"/future-page","label":"未来页面","order":120}'
        ']}',
        encoding="utf-8",
    )
    nodes = menu_registry.source_page_nodes(manifest)
    keys = {node["key"] for node in nodes}
    # 静态清单已有的官方扩展页不重复注册，新页面自动成为叶子权限。
    assert rbac.PAGE_EXT_FINANCIALS not in keys
    assert rbac.extension_page_key("/future-page") in keys
    assert all(node["parent"] == rbac.MENU_EXTENSIONS for node in nodes)

    monkeypatch.setenv("TSP_FRONTEND_DIST", str(tmp_path))
    menus = menu_registry.menu_nodes(data_dir)
    assert rbac.extension_page_key("/future-page") in {node["key"] for node in menus}
    assert rbac.extension_page_key("/future-page") in {
        node["key"] for node in menu_registry.permission_catalog(data_dir)
    }


def test_saved_layout_moves_page_and_group(data_dir):
    menus = menu_registry.save_layout(data_dir, [
        {"type": "page", "route": "/"},
        {"type": "group", "key": "group:ai", "label": "AI 工作台", "children": [
            {"type": "page", "route": "/assistant"},
        ]},
        {"type": "group", "key": rbac.MENU_TRADING, "label": "交易", "children": [
            {"type": "page", "route": "/paper"},
        ]},
    ])
    by_key = {node["key"]: node for node in menus}
    assert by_key[rbac.PAGE_EXT_ASSISTANT]["parent"] == "menu.custom.ai"
    assert by_key[rbac.PAGE_EXT_ASSISTANT]["order"] == 0
    assert by_key["menu.custom.ai"]["label"] == "AI 工作台"
    assert by_key[rbac.MENU_TRADING]["label"] == "交易"
    assert by_key[rbac.PAGE_EXT_PAPER]["parent"] == rbac.MENU_TRADING

    catalog = {node["key"]: node for node in menu_registry.permission_catalog(data_dir)}
    assert catalog[rbac.PAGE_EXT_ASSISTANT]["parent"] == "menu.custom.ai"

    # 再改成顶层页面：父级为空，权限目录同步跟随。
    menus = menu_registry.save_layout(data_dir, [
        {"type": "page", "route": "/"},
        {"type": "page", "route": "/assistant"},
    ])
    by_key = {node["key"]: node for node in menus}
    assert by_key[rbac.PAGE_EXT_ASSISTANT]["parent"] == ""


def test_legacy_page_analysis_is_migrated_to_current_leaves(data_dir):
    _super()
    with user_store._connect() as conn:
        conn.execute(
            "UPDATE role_groups SET permissions = ? WHERE key = ?",
            ('["page.watchlist", "page.analysis"]', rbac.EDITOR),
        )
    # 直接写库后再跑迁移，路径与线上启动一致
    user_store.run_schema_migrations()
    group = user_store.get_role_group(rbac.EDITOR)
    assert group is not None
    assert rbac.PAGE_ANALYSIS not in group["permissions"]
    assert rbac.PAGE_WATCHLIST in group["permissions"]
    dynamic_keys = set(menu_registry.dynamic_page_permission_keys(data_dir))
    assert dynamic_keys
    assert dynamic_keys <= set(group["permissions"])

    # 幂等：再迁移一次不会重复/回退
    user_store.run_schema_migrations()
    assert set(user_store.get_role_group(rbac.EDITOR)["permissions"]) == set(group["permissions"])


def test_update_group_accepts_dynamic_page_permission(data_dir):
    super_user = _super()
    with user_store._connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO role_groups (key, name, description, permissions, is_builtin, updated_at)"
            " VALUES (?, ?, ?, ?, 1, ?)",
            (rbac.EDITOR, "编辑者", "", '["page.watchlist","page.analysis.news_flow"]', 0),
        )
    group = user_store.get_role_group(rbac.EDITOR)
    assert group is not None
    assert "page.analysis.news_flow" in group["permissions"]
    # 旧兼容码不再合法，保存时会被拒绝
    with pytest.raises(ValueError):
        user_store.update_role_group(rbac.EDITOR, permissions=[rbac.PAGE_ANALYSIS], actor=super_user)


def test_navigation_tree_endpoint_returns_shared_menu(data_dir):
    app = FastAPI()
    app.state.repo = SimpleNamespace(store=SimpleNamespace(data_dir=data_dir))
    app.include_router(navigation_api.router)
    res = TestClient(app, client=("127.0.0.1", 12345)).get("/api/navigation/tree")
    assert res.status_code == 200
    menus = res.json()["menus"]
    assert len(menus) == len(menu_registry.menu_nodes(data_dir))
    assert any(m["key"] == rbac.PAGE_WATCHLIST for m in menus)


def _nav_client(user: dict | None, data_dir: Path) -> TestClient:
    app = FastAPI()
    app.state.repo = SimpleNamespace(store=SimpleNamespace(data_dir=data_dir))
    app.include_router(navigation_api.router)

    @app.middleware("http")
    async def inject_user(request: Request, call_next):
        request.state.user = user
        return await call_next(request)

    return TestClient(app, client=("127.0.0.1", 12345))


def test_navigation_layout_endpoint_persists_and_guards(data_dir):
    super_user = _super()
    client = _nav_client(super_user, data_dir)
    res = client.put("/api/navigation/layout", json={"items": [
        {"type": "page", "route": "/"},
        {"type": "group", "key": "group:ai", "label": "AI 工作台", "children": [
            {"type": "page", "route": "/assistant"},
        ]},
    ]})
    assert res.status_code == 200
    menus = {m["key"]: m for m in res.json()["menus"]}
    assert menus[rbac.PAGE_EXT_ASSISTANT]["parent"] == "menu.custom.ai"

    reader = _member(rbac.EDITOR, "reader@example.com")
    denied = _nav_client(reader, data_dir).put(
        "/api/navigation/layout", json={"items": [{"type": "page", "route": "/"}]}
    )
    assert denied.status_code == 403


def test_permission_catalog_endpoint_returns_menus(data_dir):
    super_user = _super()
    res = _make_client(super_user).get("/api/admin/permission-catalog")
    assert res.status_code == 200
    body = res.json()
    assert "menus" in body and len(body["menus"]) > 20
    assert "permissions" in body
    assert all(not p["key"].startswith("menu.") for p in body["permissions"])
