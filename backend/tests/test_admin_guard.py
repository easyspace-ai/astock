"""管理员守卫测试 — 系统配置写接口只对 admin 开放, 个人偏好放行。

覆盖:
  - 普通用户: 改系统配置 403, 改个人偏好 200, GET 返回体裁剪
  - 管理员: 全部通过, GET 返回完整配置
  - 无账号会话(API Token/内部调用): 视为管理员
  - 其他后台路由(pages-admin)普通用户 403
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.api import accounts as accounts_api
from app.api import ext_pages, pages_admin, settings as settings_api
from app.services import user_store


@pytest.fixture()
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from app.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    accounts_api._fail_counter.clear()
    accounts_api._code_send_times.clear()
    return tmp_path


@pytest.fixture()
def admin_token(data_dir):
    u = user_store.create_user("admin@example.com", "secret123")
    return user_store.create_session(u["id"])


@pytest.fixture()
def user_token(data_dir):
    user_store.create_user("admin@example.com", "secret123")
    u = user_store.create_user("user@example.com", "secret123")
    return user_store.create_session(u["id"])


def _client(token: str | None) -> TestClient:
    app = FastAPI()
    app.include_router(settings_api.router)
    app.include_router(pages_admin.router)
    app.include_router(ext_pages.router)

    @app.middleware("http")
    async def inject_user(request: Request, call_next):
        request.state.user = user_store.get_user_by_token(token) if token else None
        return await call_next(request)

    client = TestClient(app, client=("127.0.0.1", 23456))
    if token:
        client.cookies.set(accounts_api.COOKIE_NAME, token)
    return client


def test_user_cannot_write_system_settings(user_token):
    r = _client(user_token).put("/api/settings/preferences/daily-batch-symbols", json={"size": 30})
    assert r.status_code == 403


def test_user_can_write_personal_preferences(user_token):
    r = _client(user_token).put("/api/settings/preferences/nav-hidden", json={"nav_hidden": []})
    assert r.status_code == 200


def test_user_get_settings_is_trimmed(user_token):
    r = _client(user_token).get("/api/settings")
    assert r.status_code == 200
    body = r.json()
    assert "secrets_path" not in body
    assert "tickflow_api_key_masked" not in body
    assert "ai_configured" in body


def test_user_preferences_hide_credentials(user_token):
    r = _client(user_token).get("/api/settings/preferences")
    assert r.status_code == 200
    body = r.json()
    assert "feishu_webhook_secret" not in body
    assert "email_smtp_config" not in body
    assert "nav_layout" in body  # 界面偏好仍下发


def test_user_cannot_read_admin_tokens(user_token):
    r = _client(user_token).get("/api/settings/api-tokens")
    assert r.status_code == 403


def test_admin_can_write_system_settings(admin_token):
    r = _client(admin_token).put("/api/settings/preferences/daily-batch-symbols", json={"size": 30})
    assert r.status_code == 200


def test_admin_get_settings_is_full(admin_token):
    r = _client(admin_token).get("/api/settings")
    assert r.status_code == 200
    assert "secrets_path" in r.json()


def test_password_mode_without_account_is_admin(data_dir):
    r = _client(None).put("/api/settings/preferences/daily-batch-symbols", json={"size": 30})
    assert r.status_code == 200


def test_user_cannot_call_other_admin_router(user_token):
    r = _client(user_token).delete("/api/pages-admin/source-extensions/demo")
    assert r.status_code == 403


def test_user_cannot_manage_page_packs(user_token):
    r = _client(user_token).get("/api/ext-pages")
    assert r.status_code == 403


def test_user_can_load_page_bundle(user_token, data_dir):
    # 扩展页运行需要 bundle.js: 守卫放行, 由业务层返回 404(文件不存在)
    client = _client(user_token)
    client.app.state.repo = SimpleNamespace(store=SimpleNamespace(data_dir=data_dir))
    r = client.get("/api/ext-pages/demo/bundle.js")
    assert r.status_code != 403
