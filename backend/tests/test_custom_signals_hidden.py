"""账号级隐藏内置信号 — 只改自己的视图清单, 共享定义一字不动、照常编译。"""
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


def _seed_shared(tmp_path: Path) -> None:
    """无账号上下文写入 = 共享层 (等价内置种子)。"""
    with tenant.user_scope(None):
        custom_signals.save_one(tmp_path, {
            "id": "builtin_sig",
            "name": "内置信号",
            "kind": "entry",
            "conditions": [{"left": "rsi_14", "op": "<", "right": "30"}],
            "enabled": True,
        })


@pytest.fixture(autouse=True)
def _clear_caches():
    pipeline.invalidate_custom_signals()
    custom_signals.invalidate_intraday_cache()
    yield
    pipeline.invalidate_custom_signals()
    custom_signals.invalidate_intraday_cache()


def test_hide_is_per_account_and_keeps_shared_intact(tmp_path):
    _seed_shared(tmp_path)
    client = _client(tmp_path)
    shared_file = tmp_path / "user_data/custom_signals/builtin_sig.json"
    before = shared_file.read_bytes()

    with tenant.user_scope(1):
        assert client.post("/api/custom-signals/builtin_sig/hide").json()["hidden"] is True
        seen = [s["id"] for s in client.get("/api/custom-signals").json()["signals"]]
        full = client.get("/api/custom-signals", params={"include_hidden": "true"}).json()["signals"]
        hidden_path = tmp_path / "user_data/users/1/custom_signals_hidden.json"

    with tenant.user_scope(2):
        other = [s["id"] for s in client.get("/api/custom-signals").json()["signals"]]

    assert seen == []                       # 默认列表 (选择器) 不再提供
    assert [s["id"] for s in full] == ["builtin_sig"] and full[0]["hidden"] is True
    assert shared_file.read_bytes() == before   # 共享定义没被改
    assert hidden_path.is_file()
    assert other == ["builtin_sig"]          # 别人不受影响
    # 隐藏只影响展示: 编译并集里仍有它
    assert "csg_builtin_sig" in custom_signals.build_expressions(custom_signals.load_all(tmp_path))


def test_unhide_restores_and_is_idempotent(tmp_path):
    _seed_shared(tmp_path)
    client = _client(tmp_path)
    with tenant.user_scope(1):
        client.post("/api/custom-signals/builtin_sig/hide")
        assert client.post("/api/custom-signals/builtin_sig/unhide").json()["hidden"] is False
        assert client.post("/api/custom-signals/builtin_sig/unhide").status_code == 200
        assert [s["id"] for s in client.get("/api/custom-signals").json()["signals"]] == ["builtin_sig"]


def test_hide_rejects_own_and_missing(tmp_path):
    _seed_shared(tmp_path)
    client = _client(tmp_path)
    with tenant.user_scope(1):
        assert client.post("/api/custom-signals", json={
            "id": "mine",
            "name": "我的",
            "kind": "entry",
            "conditions": [{"left": "rsi_14", "op": "<", "right": "30"}],
            "enabled": True,
        }).status_code == 200
        own = client.post("/api/custom-signals/mine/hide")
        missing = client.post("/api/custom-signals/nope_sig/hide")
        bad_id = client.post("/api/custom-signals/BAD ID/hide")

    assert own.status_code == 400 and "归档" in own.json()["detail"]
    assert missing.status_code == 404
    assert bad_id.status_code == 400


def test_corrupt_hidden_file_does_not_break_list(tmp_path):
    _seed_shared(tmp_path)
    client = _client(tmp_path)
    hidden_path = tmp_path / "user_data/users/1"
    hidden_path.mkdir(parents=True, exist_ok=True)
    (hidden_path / "custom_signals_hidden.json").write_text("{not json", encoding="utf-8")

    with tenant.user_scope(1):
        resp = client.get("/api/custom-signals")
        assert custom_signals.load_hidden(tmp_path) == set()

    assert resp.status_code == 200
    assert [s["id"] for s in resp.json()["signals"]] == ["builtin_sig"]
