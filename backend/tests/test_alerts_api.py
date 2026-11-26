"""告警 API 的写操作边界。

读接口(alerts toast 全局挂载)保持开放, 但「清空全部」「造演示数据」会影响其他
账号 —— 单文件存储没有 owner, 清空等于删掉所有人的记录, 演示数据会推给所有
在线客户端。这里钉死这两个端点的权限, 防止普通账号误伤。
"""
from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import alerts as alerts_api
from app.services import alert_store, rbac


def _client(tmp_path, *, perms: list[str], user: dict | None = None):
    app = FastAPI()
    app.state.repo = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path))
    allowed = frozenset(perms)

    @app.middleware("http")
    async def _fake_account(request, call_next):
        request.state.user = user or {"id": 3, "email": "u3@example.com", "is_active": True, "perm_role": "editor"}
        request.state.permissions = allowed
        return await call_next(request)

    app.include_router(alerts_api.router)
    return TestClient(app)


def test_clear_all_requires_data_manage(tmp_path):
    alert_store.append_many(tmp_path, [{"ts": 1, "source": "signal", "message": "x"}])
    client = _client(tmp_path, perms=[rbac.PAGE_WATCHLIST])
    resp = client.delete("/api/alerts")
    assert resp.status_code == 403, resp.text
    # 被拒后记录必须还在: 403 不能是"先删后校验"。
    assert alert_store.count(tmp_path) == 1


def test_clear_all_allowed_for_data_manage(tmp_path):
    alert_store.append_many(tmp_path, [{"ts": 1, "source": "signal", "message": "x"}])
    client = _client(tmp_path, perms=[rbac.ADMIN_DATA_MANAGE])
    resp = client.delete("/api/alerts")
    assert resp.status_code == 200, resp.text
    assert resp.json()["cleared"] == 1
    assert alert_store.count(tmp_path) == 0


def test_seed_requires_dev_page(tmp_path):
    client = _client(tmp_path, perms=[rbac.PAGE_WATCHLIST])
    resp = client.post("/api/alerts/seed", params={"count": 3})
    assert resp.status_code == 403, resp.text
    assert alert_store.count(tmp_path) == 0


def test_seed_allowed_for_dev_page(tmp_path):
    client = _client(tmp_path, perms=[rbac.PAGE_DEV])
    resp = client.post("/api/alerts/seed", params={"count": 3})
    assert resp.status_code == 200, resp.text
    assert alert_store.count(tmp_path) == 3


def test_read_stays_open_for_restricted_accounts(tmp_path):
    """告警 toast 在每个页面都要能读到, 不能因为页面权限被挡。"""
    alert_store.append_many(tmp_path, [{"ts": 1, "source": "signal", "message": "x"}])
    client = _client(tmp_path, perms=[])
    resp = client.get("/api/alerts", params={"days": 7})
    assert resp.status_code == 200, resp.text
    assert resp.json()["total"] == 1
