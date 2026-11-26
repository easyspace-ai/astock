"""策略池 / 策略库元数据按账号隔离 — 池、分组、归档、备份收集。"""
from __future__ import annotations

import json

import pytest

from app.services import config_backup, preferences, strategy_pool, tenant


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    from app import config as app_config

    monkeypatch.setattr(app_config.settings, "data_dir", tmp_path)
    preferences._invalidate_cache()
    return tmp_path


def test_pool_is_per_account(tmp_path):
    """A 收藏的策略 B 看不到; B 写入不改变 A 的池。"""
    with tenant.user_scope(1):
        strategy_pool.set_pool(["custom_a1", "custom_a2"])
        a = strategy_pool.load_snapshot()
    with tenant.user_scope(2):
        b = strategy_pool.load_snapshot()
        assert b["pool"] == []
        strategy_pool.add("custom_b1")
        b_after = strategy_pool.load_snapshot()
    with tenant.user_scope(1):
        a_after = strategy_pool.load_snapshot()

    assert a["pool"] == ["custom_a1", "custom_a2"]
    assert b_after["pool"] == ["custom_b1"]
    assert a_after["pool"] == ["custom_a1", "custom_a2"]
    # 落盘位置按账号分文件
    assert (tmp_path / "user_data/users/1/strategy_pool.json").is_file()
    assert (tmp_path / "user_data/users/2/strategy_pool.json").is_file()


def test_library_metadata_is_per_account(tmp_path):
    """库级分组/归档元数据只对本账号可见。"""
    with tenant.user_scope(1):
        strategy_pool.add("custom_x")
        _, group = strategy_pool.create_group("我的分组")
        strategy_pool.set_strategy_group("custom_x", group["id"])
        assert strategy_pool.library_metadata(tmp_path)["custom_x"]["group"] == group["id"]
    with tenant.user_scope(2):
        assert strategy_pool.library_metadata(tmp_path) == {}
    # 全局模式(无账号)读的是旧全局文件, 不会串到账号数据
    assert strategy_pool.library_metadata(tmp_path) == {}


def test_tenant_write_skips_shared_ui_prefs_shadow(tmp_path):
    """租户写池不碰全局 ui_prefs 影子, 避免多账号互相覆盖。"""
    preferences.set_ui_prefs({"strategy_pool": json.dumps(["legacy_keep"])})
    with tenant.user_scope(1):
        strategy_pool.set_pool(["custom_a1"])
    shadow = preferences.get_ui_prefs().get("strategy_pool")
    assert shadow == json.dumps(["legacy_keep"])
    # 全局模式仍维护影子 (旧版降级回滚依赖它)
    strategy_pool.set_pool(["global_1"])
    assert json.loads(preferences.get_ui_prefs()["strategy_pool"]) == ["global_1"]


def test_backup_collects_account_pool_under_tenant(tmp_path):
    """租户导出: 只带账号目录里的策略池, 不带全局旧文件。"""
    with tenant.user_scope(1):
        strategy_pool.set_pool(["custom_a1"])
    (tmp_path / "user_data/strategy_pool.json").write_text(
        json.dumps({"version": 2, "revision": 1, "groups": [], "pool": ["legacy"], "meta": {}}),
        encoding="utf-8",
    )
    with tenant.user_scope(1):
        files = config_backup._collect_backup_files(tmp_path)
    rel = {f.relative_to(tmp_path).as_posix() for f in files}
    assert "user_data/users/1/strategy_pool.json" in rel
    assert "user_data/strategy_pool.json" not in rel


def test_backup_classify_maps_legacy_pool_to_current_account(tmp_path):
    """旧备份的全局策略池条目, 租户模式下归类目标落回当前账号目录。"""
    from pathlib import PurePosixPath

    with tenant.user_scope(3):
        category, target = config_backup._classify(
            PurePosixPath("user_data/strategy_pool.json"), tmp_path
        )
    assert category == "strategy_pool"
    assert target == tmp_path / "user_data/users/3/strategy_pool.json"
