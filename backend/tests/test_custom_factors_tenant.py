"""自定义/复合因子按账号隔离 — 账号可写、共享只读、id 全局唯一。"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.api import factors as factors_api
from app.factors import store
from app.services import tenant


def _definition(factor_id: str, label: str = "租户因子") -> dict:
    return {
        "id": factor_id,
        "kind": "custom",
        "version": 1,
        "label": label,
        "group": "自定义",
        "formula": "close - open",
        "description": "",
        "direction": "positive",
        "status": "draft",
        "archived": False,
    }


def test_factor_is_per_account(tmp_path):
    """A 建的因子只在 A 的目录; B 的界面看不到, 但全站并集仍含它(共享表要算列)。"""
    with tenant.user_scope(1):
        store.save_one(tmp_path, _definition("uf_a_only"))
        a_visible = [d["id"] for _owner, d in store.visible_owned(tmp_path)]

    with tenant.user_scope(2):
        b_visible = [d["id"] for _owner, d in store.visible_owned(tmp_path)]
        union = [d["id"] for d in store.load_all(tmp_path)]

    assert a_visible == ["uf_a_only"]
    assert b_visible == []
    assert union == ["uf_a_only"]
    assert (tmp_path / "user_data/users/1/custom_factors/uf_a_only.json").is_file()
    assert not (tmp_path / "user_data/users/2/custom_factors/uf_a_only.json").exists()


def test_other_account_factor_is_not_writable(tmp_path):
    """B 不能改/删 A 的因子(409), 也不能占用同一 id。"""
    with tenant.user_scope(1):
        store.save_one(tmp_path, _definition("uf_shared_name"))

    with tenant.user_scope(2):
        with pytest.raises(HTTPException) as create_err:
            factors_api._ensure_writable(tmp_path, "uf_shared_name", action="占用")
        assert create_err.value.status_code == 409
        assert "其他账号" in create_err.value.detail
        assert store.delete_one(tmp_path, "uf_shared_name") is False
        assert "uf_shared_name" not in [d["id"] for _owner, d in store.visible_owned(tmp_path)]

    # A 的文件没被别人的删除动作动过
    assert (tmp_path / "user_data/users/1/custom_factors/uf_shared_name.json").is_file()


def test_shared_factor_is_read_only_for_account(tmp_path):
    """共享层(内置/旧全局文件): 账号改不动、删不掉, 但照样参与全站编译。"""
    store.save_one(tmp_path, _definition("uf_shared_seed"))  # 无账号上下文 → 共享层
    assert (tmp_path / "user_data/custom_factors/uf_shared_seed.json").is_file()

    with tenant.user_scope(1):
        for action in ("修改", "删除", "占用"):
            with pytest.raises(HTTPException) as err:
                factors_api._ensure_writable(tmp_path, "uf_shared_seed", action=action)
            assert err.value.status_code == 403
            assert "内置/共享" in err.value.detail
        assert store.delete_one(tmp_path, "uf_shared_seed") is False
        assert "uf_shared_seed" in [d["id"] for d in store.load_all(tmp_path)]

    assert (tmp_path / "user_data/custom_factors/uf_shared_seed.json").is_file()


def test_global_mode_keeps_shared_writable(tmp_path):
    """无账号上下文(单用户部署/全局密码模式)保持旧行为: 直接写共享层。"""
    assert tenant.current_user_id() is None
    factors_api._ensure_writable(tmp_path, "uf_any", action="修改")
    store.save_one(tmp_path, _definition("uf_any"))
    assert (tmp_path / "user_data/custom_factors/uf_any.json").is_file()
    assert store.delete_one(tmp_path, "uf_any") is True


def test_other_account_factor_not_listed(tmp_path):
    """因子库只能列出本人+共享: 他人账号的定义(含 formula)不出现。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from types import SimpleNamespace

    from app.api.factors import router
    from app.factors.registry import unregister_factor

    app = FastAPI()
    app.include_router(router)
    app.state.repo = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path))
    client = TestClient(app)

    with tenant.user_scope(1):
        store.register_definition(_definition("uf_listed", "别人的因子"))
        store.save_one(tmp_path, _definition("uf_listed", "别人的因子"))
    try:
        with tenant.user_scope(2):
            ids = {f["id"] for f in client.get("/api/factors").json()["factors"]}
            assert "uf_listed" not in ids
            assert "uf_listed" in store.hidden_ids(tmp_path)
        with tenant.user_scope(1):
            rows = {f["id"]: f for f in client.get("/api/factors").json()["factors"]}
            assert rows["uf_listed"]["formula"] == "close - open"
    finally:
        unregister_factor("uf_listed")


def test_shared_factor_is_read_only_for_every_account(tmp_path):
    """共享层自定义因子对账号是只读 (owner=None, read_only=True), 本人因子可写。"""
    from types import SimpleNamespace

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.factors import router
    from app.factors.registry import unregister_factor

    app = FastAPI()
    app.include_router(router)
    app.state.repo = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path))
    client = TestClient(app)

    with tenant.user_scope(None):
        store.save_one(tmp_path, _definition("uf_shared", "共享因子"))
        store.register_definition(_definition("uf_shared", "共享因子"))
    try:
        with tenant.user_scope(1):
            rows = {f["id"]: f for f in client.get("/api/factors").json()["factors"]}
            shared = rows["uf_shared"]
            assert shared["owner"] is None and shared["read_only"] is True
            # 内置因子不在盘上, 不带 owner/read_only 语义
            assert rows["rsi_14"]["read_only"] is False and rows["rsi_14"]["owner"] is None

            store.save_one(tmp_path, _definition("uf_mine", "我的因子"))
            store.register_definition(_definition("uf_mine", "我的因子"))
            mine = {f["id"]: f for f in client.get("/api/factors").json()["factors"]}["uf_mine"]
            assert mine["owner"] == 1 and mine["read_only"] is False
    finally:
        unregister_factor("uf_shared")
        unregister_factor("uf_mine")
