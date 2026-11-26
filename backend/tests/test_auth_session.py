"""认证中间件测试 — 邮箱账号会话是唯一通道, 旧 tf_session 已失效。"""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.api import accounts as accounts_api
from app.main import auth_middleware
from app.services import user_store


def _client(peer: str = "127.0.0.1") -> TestClient:
    app = FastAPI()
    app.middleware("http")(auth_middleware)

    @app.get("/api/ping")
    def ping(request: Request):
        user = getattr(request.state, "user", None)
        return {"user_id": user["id"] if user else None}

    return TestClient(app, client=(peer, 12345))


def test_uninitialized_local_allowed_but_public_rejected(monkeypatch):
    from app.api import accounts
    monkeypatch.setattr(user_store, "has_users", lambda: False)
    monkeypatch.setattr(accounts, "_is_local_network", lambda ip: ip in ("127.0.0.1", "::1"))

    assert _client("127.0.0.1").get("/api/ping").status_code == 200

    public = _client("203.0.113.9").get("/api/ping")
    assert public.status_code == 403
    assert public.json()["code"] == "NOT_INITIALIZED"


def test_initialized_requires_account_session_and_ignores_legacy_cookie(monkeypatch):
    monkeypatch.setattr(user_store, "has_users", lambda: True)
    monkeypatch.setattr(
        user_store, "get_user_by_token",
        lambda token: {"id": 7, "is_active": True} if token == "valid-token" else None,
    )

    anonymous = _client().get("/api/ping")
    assert anonymous.status_code == 401

    # 旧访问密码模式签发的 tf_session 不再被认作登录态
    legacy = _client()
    legacy.cookies.set("tf_session", "legacy-panel-session")
    assert legacy.get("/api/ping").status_code == 401

    valid = _client()
    valid.cookies.set(accounts_api.COOKIE_NAME, "valid-token")
    response = valid.get("/api/ping")
    assert response.status_code == 200
    assert response.json()["user_id"] == 7


def test_options_preflight_is_not_blocked(monkeypatch):
    monkeypatch.setattr(user_store, "has_users", lambda: True)
    assert _client().options("/api/ping").status_code != 401


def test_legacy_auth_routes_are_gone():
    from app.main import app

    paths = {route.path for route in app.routes if hasattr(route, "path")}
    assert not any(path.startswith("/api/auth/") for path in paths)
