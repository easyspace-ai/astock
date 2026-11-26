"""业务接口页面权限测试 — 页面权限从「只藏菜单」升级为接口硬校验。"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

from app.api import data as data_api
from app.api import factors as factors_api
from app.api import permissions as permissions_api
from app.api import screener as screener_api
from app.api import watchlist as watchlist_api
from app.services import page_access, rbac, user_store


@pytest.fixture()
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from app.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    return tmp_path


def _client(user: dict | None, data_dir: Path | None = None) -> TestClient:
    """挂真实业务路由，按生产同一份 PAGE_ACCESS 映射加页面依赖。"""
    app = FastAPI()
    app.state.repo = SimpleNamespace(store=SimpleNamespace(data_dir=data_dir))
    for module, name in (
        (factors_api, "factors"),
        (screener_api, "screener"),
        (watchlist_api, "watchlist"),
        (data_api, "data"),
    ):
        app.include_router(
            module.router,
            dependencies=[
                Depends(permissions_api.require_page_access(*page_access.PAGE_ACCESS[name]))
            ],
        )

    @app.middleware("http")
    async def inject_user(request: Request, call_next):
        request.state.user = user
        return await call_next(request)

    return TestClient(app, client=("127.0.0.1", 12345))


def _user_with(pages: list[str], email: str) -> dict:
    """直接写权限组，模拟超管在后台给该账号勾选页面权限。"""
    if not user_store.has_users():
        user_store.create_user("root@example.com", "secret123")
    u = user_store.create_user(email, "secret123")
    key = f"test_{u['id']}"
    with user_store._connect() as conn:
        conn.execute(
            "INSERT INTO role_groups (key, name, description, permissions, is_builtin, updated_at)"
            " VALUES (?, ?, ?, ?, 0, ?)",
            (key, key, "", json.dumps(pages), 0),
        )
        # 权限组只允许内置三组通过管理接口分配; 测试直接改 users.perm_role 模拟
        # 未来「自定义权限组」的生效路径。
        conn.execute("UPDATE users SET perm_role = ? WHERE id = ?", (key, u["id"]))
    return user_store.get_user(u["id"])


def test_page_mapping_only_uses_known_permissions():
    catalog_keys = {p["key"] for p in rbac.catalog()}
    wildcard_keys = {rbac.PAGE_ANALYSIS_WILDCARD, rbac.PAGE_EXT_WILDCARD}
    for pages in page_access.PAGE_ACCESS.values():
        assert pages
        for key in pages:
            assert (
                key in catalog_keys
                or key in wildcard_keys
                or rbac.is_valid_permission(key)
            ), key


def test_factor_page_can_read_factors_but_not_screener_or_watchlist(data_dir):
    user = _user_with([rbac.PAGE_FACTORS], "factor@example.com")
    client = _client(user)
    assert client.get("/api/factors").status_code == 200
    assert client.get("/api/screener/cached-summary").status_code == 403
    assert client.get("/api/watchlist").status_code == 403


def test_shared_factor_route_allows_backtest_page(data_dir):
    user = _user_with([rbac.PAGE_BACKTEST], "bt@example.com")
    client = _client(user)
    assert client.get("/api/factors").status_code == 200
    assert client.get("/api/screener/cached-summary").status_code == 403


def test_user_without_any_page_is_fail_closed(data_dir):
    user = _user_with([], "none@example.com")
    client = _client(user)
    assert client.get("/api/factors").status_code == 403
    assert client.get("/api/screener/cached-summary").status_code == 403


def test_no_account_session_keeps_full_access(data_dir):
    client = _client(None, data_dir)
    assert client.get("/api/factors").status_code == 200


def test_data_manage_guard_blocks_destructive_routes(data_dir):
    """清库/自检修补属于系统数据管理, 页面权限不足以调用。"""
    viewer = _user_with([rbac.PAGE_DASHBOARD, rbac.ADMIN_ACCESS], "viewer@example.com")
    client = _client(viewer, data_dir)
    assert client.get("/api/data/integrity").status_code == 403
    assert client.post("/api/data/integrity/scan", json={}).status_code == 403
    assert client.post("/api/data/clear", json={}).status_code == 403

    manager = _user_with([rbac.ADMIN_DATA_MANAGE], "data-manager@example.com")
    manager_client = _client(manager, data_dir)
    assert manager_client.get("/api/data/integrity").status_code == 200
