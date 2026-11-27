"""自定义信号按账号隔离 — 账号可写、共享(内置种子)只读、id 全局唯一。"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.signals import router
from app.indicators import pipeline
from app.services import tenant
from app.strategy import custom_signals


def _client(tmp_path: Path) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.state.repo = SimpleNamespace(store=SimpleNamespace(data_dir=Path(tmp_path)))
    return TestClient(app)


_SIGNAL = {
    "id": "acct_sig",
    "name": "账号信号",
    "kind": "entry",
    "conditions": [{"left": "rsi_14", "op": "<", "right": "30"}],
    "enabled": True,
}


@pytest.fixture(autouse=True)
def _clear_caches():
    pipeline.invalidate_custom_signals()
    custom_signals.invalidate_intraday_cache()
    yield
    pipeline.invalidate_custom_signals()
    custom_signals.invalidate_intraday_cache()


def test_signal_is_per_account(tmp_path):
    """A 写的信号只在 A 的目录, B 既看不到也不参与编译。"""
    client = _client(tmp_path)
    with tenant.user_scope(1):
        assert client.post("/api/custom-signals", json=_SIGNAL).status_code == 200
        a_view = [s["id"] for s in client.get("/api/custom-signals").json()["signals"]]
        a_exprs = custom_signals.build_expressions(custom_signals.load_all(tmp_path))

    with tenant.user_scope(2):
        b_view = [s["id"] for s in client.get("/api/custom-signals").json()["signals"]]
        b_visible = custom_signals.load_one(tmp_path, "acct_sig")

    assert a_view == ["acct_sig"]
    assert b_view == []
    assert b_visible is None
    assert "csg_acct_sig" in a_exprs
    assert (tmp_path / "user_data/users/1/custom_signals/acct_sig.json").is_file()
    assert not (tmp_path / "user_data/users/2/custom_signals/acct_sig.json").exists()


def test_list_marks_owner_and_shared_read_only(tmp_path):
    """列表返回 owner / read_only: 共享层只读, 自有可写。"""
    client = _client(tmp_path)
    seed = {**_SIGNAL, "id": "seed_sig", "name": "内置信号"}
    custom_signals.save_one(tmp_path, seed)  # 无账号上下文 → 共享层
    with tenant.user_scope(1):
        client.post("/api/custom-signals", json=_SIGNAL)
        rows = {s["id"]: s for s in client.get("/api/custom-signals").json()["signals"]}

    assert rows["seed_sig"]["owner"] is None
    assert rows["seed_sig"]["read_only"] is True
    assert rows["acct_sig"]["owner"] == 1
    assert rows["acct_sig"]["read_only"] is False


def test_shared_definition_is_read_only_for_account(tmp_path):
    """共享(内置)定义: 账号改不动、删不掉、归档不了, 但照样能读到并编译。"""
    client = _client(tmp_path)
    seed = {**_SIGNAL, "id": "seed_sig", "name": "内置信号"}
    custom_signals.save_one(tmp_path, seed)

    with tenant.user_scope(1):
        assert client.post("/api/custom-signals", json={**seed, "name": "想改"}).status_code == 403
        assert client.delete("/api/custom-signals/seed_sig").status_code == 403
        assert client.post("/api/custom-signals/seed_sig/archive").status_code == 403
        assert custom_signals.load_one(tmp_path, "seed_sig")["name"] == "内置信号"
        assert "csg_seed_sig" in custom_signals.build_expressions(custom_signals.load_all(tmp_path))

    # 文件仍在共享层, 账号目录下没有拷贝
    assert (tmp_path / "user_data/custom_signals/seed_sig.json").is_file()
    assert not (tmp_path / "user_data/users/1/custom_signals/seed_sig.json").exists()


def test_conflicting_id_returns_409(tmp_path):
    """id 全局唯一: 别人已用的标识不能占用; 自己的可以正常编辑。"""
    client = _client(tmp_path)
    with tenant.user_scope(1):
        assert client.post("/api/custom-signals", json=_SIGNAL).status_code == 200
    with tenant.user_scope(2):
        resp = client.post("/api/custom-signals", json=_SIGNAL)
        assert resp.status_code == 409
        assert "acct_sig" in resp.json()["detail"]
    with tenant.user_scope(1):
        edited = client.post("/api/custom-signals", json={**_SIGNAL, "name": "改个名"})
        assert edited.status_code == 200
        assert custom_signals.load_one(tmp_path, "acct_sig")["name"] == "改个名"


def test_global_mode_keeps_shared_writable(tmp_path):
    """无账号上下文(单用户部署/全局密码模式)仍能直接编辑共享层, 保持旧行为。"""
    client = _client(tmp_path)
    assert client.post("/api/custom-signals", json=_SIGNAL).status_code == 200
    on_disk = tmp_path / "user_data/custom_signals/acct_sig.json"
    assert on_disk.is_file()
    assert tenant.current_user_id() is None
    assert client.delete("/api/custom-signals/acct_sig").status_code == 200


def test_other_account_signal_not_enumerable(tmp_path):
    """他人账号的私有信号: 目录/按 id 复检都当不存在, 盘中回放也不放行。"""
    from app import definitions as defs

    client = _client(tmp_path)
    with tenant.user_scope(1):
        assert client.post("/api/custom-signals", json=_SIGNAL).status_code == 200

    with tenant.user_scope(2):
        assert defs.catalog(tmp_path) == []
        assert "acct_sig" in custom_signals.hidden_ids(tmp_path)
        try:
            defs.smoke_stored("signal", "acct_sig", tmp_path)
        except Exception as exc:  # HTTPException(404)
            assert getattr(exc, "status_code", None) == 404
        else:
            raise AssertionError("他人信号不应可按 id 复检")
        replay = client.post("/api/custom-signals/intraday/replay", json={
            "signal_id": "acct_sig", "symbols": ["600000.SH"],
            "start_date": "2026-11-01", "end_date": "2026-11-05",
        })
        assert replay.status_code == 404

    with tenant.user_scope(1):
        assert [i["id"] for i in defs.catalog(tmp_path)] == ["acct_sig"]
