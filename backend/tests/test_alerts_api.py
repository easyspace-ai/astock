"""告警 API 的读写边界 (按账号隔离)。

2026-11-26 起告警按账号分文件 (user_data/users/<uid>/alerts.jsonl):
  - 读/清空默认只作用于自己账号的文件, 不再需要管理权限;
  - 旧全局 alerts.jsonl 视为共享历史, 只有管理员能额外读/清;
  - 「造演示数据」仍是开发页能力 (会广播 SSE 给在线客户端), 但只写自己账号。
"""
from __future__ import annotations

import time
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import alerts as alerts_api
from app.services import alert_store, rbac, tenant


def _client(tmp_path, *, perms: list[str], uid: int | None = 3, user: dict | None = None):
    app = FastAPI()
    app.state.repo = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path))
    allowed = frozenset(perms)

    @app.middleware("http")
    async def _fake_account(request, call_next):
        account = user or {
            "id": uid, "email": f"u{uid}@example.com", "is_active": True, "perm_role": "editor",
        }
        tenant.set_current_user(account.get("id"))
        request.state.user = account
        request.state.permissions = allowed
        try:
            return await call_next(request)
        finally:
            tenant.set_current_user(None)

    app.include_router(alerts_api.router)
    return TestClient(app)


def test_clear_only_affects_own_records(tmp_path):
    """普通账号清空 = 只清自己的文件; 别人的记录和旧全局历史都不能被动到。"""
    now_ms = int(time.time() * 1000)
    alert_store.append_many(tmp_path, [{"ts": now_ms - 2000, "source": "signal", "message": "mine"}], owner=3)
    alert_store.append_many(tmp_path, [{"ts": now_ms - 1000, "source": "signal", "message": "theirs"}], owner=4)
    alert_store.append_many(tmp_path, [{"ts": now_ms, "source": "signal", "message": "legacy"}], owner=None)

    client = _client(tmp_path, perms=[rbac.PAGE_WATCHLIST], uid=3)
    resp = client.delete("/api/alerts")
    assert resp.status_code == 200, resp.text
    assert resp.json()["cleared"] == 1

    assert alert_store.count(tmp_path, owner=3) == 0
    assert alert_store.count(tmp_path, owner=4) == 1
    assert alert_store.count(tmp_path, owner=None) == 1


def test_clear_admin_also_clears_legacy_history(tmp_path):
    now_ms = int(time.time() * 1000)
    alert_store.append_many(tmp_path, [{"ts": now_ms - 1000, "source": "signal", "message": "mine"}], owner=1)
    alert_store.append_many(tmp_path, [{"ts": now_ms, "source": "signal", "message": "legacy"}], owner=None)
    client = _client(tmp_path, perms=[rbac.ADMIN_DATA_MANAGE], uid=1)
    resp = client.delete("/api/alerts")
    assert resp.status_code == 200, resp.text
    assert resp.json()["cleared"] == 2
    assert alert_store.count(tmp_path, owner=1) == 0
    assert alert_store.count(tmp_path, owner=None) == 0


def test_read_is_isolated_between_accounts(tmp_path):
    """A 账号读不到 B 账号的告警 — 这是本次多租户改造的核心断言。"""
    now_ms = int(time.time() * 1000)
    alert_store.append_many(tmp_path, [{"ts": now_ms - 1000, "source": "signal", "message": "u3"}], owner=3)
    alert_store.append_many(tmp_path, [{"ts": now_ms, "source": "signal", "message": "u4"}], owner=4)

    resp = _client(tmp_path, perms=[], uid=3).get("/api/alerts", params={"days": 7})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [a["message"] for a in body["alerts"]] == ["u3"]
    assert body["total"] == 1


def test_admin_reads_legacy_shared_history(tmp_path):
    """旧全局文件只对管理员额外可见; 普通账号看不到 (不继承历史归属)。"""
    now_ms = int(time.time() * 1000)
    alert_store.append_many(tmp_path, [{"ts": now_ms - 1000, "source": "signal", "message": "mine"}], owner=1)
    alert_store.append_many(tmp_path, [{"ts": now_ms, "source": "signal", "message": "legacy"}], owner=None)

    admin = _client(tmp_path, perms=[rbac.ADMIN_DATA_MANAGE], uid=1).get("/api/alerts", params={"days": 7})
    assert {a["message"] for a in admin.json()["alerts"]} == {"mine", "legacy"}

    plain = _client(tmp_path, perms=[], uid=2).get("/api/alerts", params={"days": 7})
    assert plain.json()["alerts"] == []


def test_seed_requires_dev_page(tmp_path):
    client = _client(tmp_path, perms=[rbac.PAGE_WATCHLIST], uid=3)
    resp = client.post("/api/alerts/seed", params={"count": 3})
    assert resp.status_code == 403, resp.text
    assert alert_store.count(tmp_path, owner=3) == 0


def test_seed_writes_only_to_own_file(tmp_path):
    client = _client(tmp_path, perms=[rbac.PAGE_DEV], uid=3)
    resp = client.post("/api/alerts/seed", params={"count": 3})
    assert resp.status_code == 200, resp.text
    assert alert_store.count(tmp_path, owner=3) == 3
    # 记录自带 owner_id, SSE 推给其他账号时会被前端过滤掉
    assert alert_store.count(tmp_path, owner=4) == 0
    assert alert_store.count(tmp_path, owner=None) == 0


def test_read_stays_open_for_restricted_accounts(tmp_path):
    """告警 toast 在每个页面都要能读到, 不能因为页面权限被挡。"""
    alert_store.append_many(tmp_path, [{"ts": 1, "source": "signal", "message": "x"}], owner=3)
    client = _client(tmp_path, perms=[], uid=3)
    resp = client.get("/api/alerts", params={"days": 7})
    assert resp.status_code == 200, resp.text
    assert resp.json()["total"] == 1
