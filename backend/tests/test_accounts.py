"""多用户账号体系（邮箱注册 + 登录 + 邮箱验证/找回密码）测试。

覆盖：
  - user_store 域：注册/去重/密码/角色/订阅/配额/会话
  - accounts API：注册/登录/me/登出/改密/邮箱验证/重置密码
  - 登录失败限流
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.api import accounts as accounts_api
from app.services import user_store


@pytest.fixture()
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """把账号库/偏好指向临时数据目录, 避免污染真实数据。"""
    from app.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    # 登录限流/发码限流是模块级全局, 测试间需隔离
    accounts_api._fail_counter.clear()
    accounts_api._code_send_times.clear()
    return tmp_path


def _make_client() -> TestClient:
    """挂 accounts 路由, 并注入 request.state.user(模拟中间件行为)。"""
    app = FastAPI()
    app.include_router(accounts_api.router)

    @app.middleware("http")
    async def inject_user(request: Request, call_next):
        token = request.cookies.get(accounts_api.COOKIE_NAME)
        request.state.user = user_store.get_user_by_token(token) if token else None
        return await call_next(request)

    return TestClient(app, client=("127.0.0.1", 12345))


# ================================================================
# user_store 域
# ================================================================
def test_first_user_is_admin(data_dir):
    u = user_store.create_user("boss@example.com", "secret123")
    assert u["role"] == user_store.ROLE_ADMIN
    assert user_store.has_users()


def test_duplicate_email_rejected(data_dir):
    user_store.create_user("a@example.com", "secret123")
    with pytest.raises(ValueError, match="已注册"):
        user_store.create_user("A@example.com", "secret456")  # 大小写归一


def test_password_verify_and_short_reject(data_dir):
    user_store.create_user("a@example.com", "secret123")
    u = user_store.get_user_by_email("a@example.com")
    assert user_store.verify_password("secret123", u["password_salt"], u["password_hash"])
    assert not user_store.verify_password("wrong", u["password_salt"], u["password_hash"])
    with pytest.raises(ValueError):
        user_store.create_user("b@example.com", "12345")


def test_grant_pro_and_expiry_downgrade(data_dir):
    user_store.create_user("admin@example.com", "secret123")  # 首个用户是 admin
    u2 = user_store.create_user("free@example.com", "secret123")
    assert u2["role"] == user_store.ROLE_FREE
    assert user_store.effective_role(u2) == user_store.ROLE_FREE
    pro = user_store.grant_pro(u2["id"], 30)
    assert pro["role"] == user_store.ROLE_PRO
    assert user_store.effective_role(pro) == user_store.ROLE_PRO
    # 到期时间拨到过去 → 自动降级为 free
    expired = user_store.update_user(u2["id"], subscription_expires_at=time.time() - 10)
    assert user_store.effective_role(expired) == user_store.ROLE_FREE


def test_quota_consume_reset_limit(data_dir):
    user_store.create_user("admin@example.com", "secret123")
    u = user_store.create_user("free@example.com", "secret123")
    assert u["role"] == user_store.ROLE_FREE
    for _ in range(10):
        ok, reason = user_store.consume_quota(u, "ai_daily")
        assert ok, reason
    ok, reason = user_store.consume_quota(u, "ai_daily")
    assert not ok and "配额" in reason
    admin = user_store.update_user(u["id"], role=user_store.ROLE_ADMIN)
    assert user_store.consume_quota(admin, "ai_daily")[0] is True


def test_session_lifecycle(data_dir):
    u = user_store.create_user("a@example.com", "secret123")
    token = user_store.create_session(u["id"])
    got = user_store.get_user_by_token(token)
    assert got is not None and got["id"] == u["id"]
    user_store.revoke_session(token)
    assert user_store.get_user_by_token(token) is None


# ================================================================
# accounts API
# ================================================================
def test_register_login_me_logout(data_dir):
    client = _make_client()
    r = client.post("/api/accounts/register", json={"email": "boss@example.com", "password": "secret123"})
    assert r.status_code == 201
    assert r.json()["user"]["role"] == "admin"
    assert "id" in r.json()["user"]
    assert "password" not in r.json()["user"]
    # 重复注册 409
    assert client.post(
        "/api/accounts/register", json={"email": "boss@example.com", "password": "secret456"},
    ).status_code == 409
    # me(带 cookie)
    r3 = client.get("/api/accounts/me")
    assert r3.status_code == 200 and r3.json()["user"]["email"] == "boss@example.com"
    # 登出后 me → 401
    client.post("/api/accounts/logout")
    assert client.get("/api/accounts/me").status_code == 401
    # 邮箱登录
    r4 = client.post("/api/accounts/login", json={"email": "boss@example.com", "password": "secret123"})
    assert r4.status_code == 200
    assert client.get("/api/accounts/me").status_code == 200


def test_login_wrong_password_and_rate_limit(data_dir):
    client = _make_client()
    client.post("/api/accounts/register", json={"email": "a@example.com", "password": "secret123"})
    for _ in range(5):
        r = client.post("/api/accounts/login", json={"email": "a@example.com", "password": "bad"})
        assert r.status_code == 401
    r = client.post("/api/accounts/login", json={"email": "a@example.com", "password": "secret123"})
    assert r.status_code == 429


def test_change_password(data_dir):
    client = _make_client()
    client.post("/api/accounts/register", json={"email": "a@example.com", "password": "secret123"})
    r = client.post(
        "/api/accounts/change-password",
        json={"old_password": "wrong", "new_password": "newsecret456"},
    )
    assert r.status_code == 400
    r = client.post(
        "/api/accounts/change-password",
        json={"old_password": "secret123", "new_password": "newsecret456"},
    )
    assert r.status_code == 200
    # 改密后旧会话吊销 → 需重新登录
    assert client.get("/api/accounts/me").status_code == 401


# ================================================================
# 邮箱验证 / 找回密码
# ================================================================
def test_email_verify_flow(data_dir):
    client = _make_client()
    r = client.post("/api/accounts/register", json={"email": "v1@example.com", "password": "secret123"})
    assert r.status_code == 201
    assert r.json()["user"]["email_verified"] is False

    # 未配置 SMTP → 开发模式返回 dev_code
    r = client.post("/api/accounts/send-verify")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] and body["sent"] is False and len(body["dev_code"]) == 6

    assert client.post("/api/accounts/verify-email", json={"code": "000000"}).status_code == 400
    r = client.post("/api/accounts/verify-email", json={"code": body["dev_code"]})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert client.get("/api/accounts/me").json()["user"]["email_verified"] is True
    assert client.post("/api/accounts/send-verify").json().get("already_verified") is True


def test_forgot_and_reset_password(data_dir):
    client = _make_client()
    user = user_store.create_user("reset@example.com", "secret123")
    # 未验证邮箱时 forgot-password 不发码（防枚举）
    r = client.post("/api/accounts/forgot-password", json={"email": "reset@example.com"})
    assert r.status_code == 200 and r.json()["dev_code"] is None
    # 不存在的邮箱同样返回 ok
    r = client.post("/api/accounts/forgot-password", json={"email": "nobody@example.com"})
    assert r.status_code == 200 and r.json()["dev_code"] is None

    user_store.set_email_verified(user["id"])
    r = client.post("/api/accounts/forgot-password", json={"email": "reset@example.com"})
    assert r.status_code == 200
    code = r.json()["dev_code"]
    assert code and len(code) == 6

    r = client.post("/api/accounts/reset-password", json={
        "email": "reset@example.com", "code": code, "new_password": "newpass123",
    })
    assert r.status_code == 200 and r.json()["ok"] is True
    assert client.post("/api/accounts/login", json={
        "email": "reset@example.com", "password": "secret123",
    }).status_code == 401
    assert client.post("/api/accounts/login", json={
        "email": "reset@example.com", "password": "newpass123",
    }).status_code == 200

    # 验证码单次使用
    accounts_api._code_send_times.clear()
    user_store.set_email_verified(user["id"])
    code = client.post("/api/accounts/forgot-password", json={"email": "reset@example.com"}).json()["dev_code"]
    assert client.post("/api/accounts/reset-password", json={
        "email": "reset@example.com", "code": code, "new_password": "newpass456",
    }).status_code == 200
    assert client.post("/api/accounts/reset-password", json={
        "email": "reset@example.com", "code": code, "new_password": "newpass789",
    }).status_code == 400
