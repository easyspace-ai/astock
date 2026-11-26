"""监控规则 / 告警的按账号隔离 (多租户)。

钉死三条不变量:
  1. 规则文件落在 user_data/users/<uid>/monitor_rules, 账号之间互不可见;
  2. 引擎装载全体账号时 id 带 u<uid>:: 命名空间, 同名 id 不会互相覆盖;
  3. 事件出引擎后剥掉命名空间并写 owner_id, 告警按 owner 分文件落盘。
"""
from __future__ import annotations

from app.services import alert_store, tenant
from app.strategy import monitor_rules as mr
from app.strategy.monitor import MonitorRuleEngine

RULE = {
    "id": "mr_same_id",
    "name": "同名规则",
    "type": "price",
    "enabled": True,
    "scope": "symbols",
    "symbols": ["600519.SH"],
    "conditions": [{"field": "change_pct", "op": ">", "value": 3}],
}


def test_rules_are_stored_per_account(tmp_path):
    with tenant.user_scope(1):
        mr.save_one(tmp_path, dict(RULE))
    with tenant.user_scope(2):
        assert mr.load_all(tmp_path) == []

    with tenant.user_scope(1):
        assert [r["id"] for r in mr.load_all(tmp_path)] == ["mr_same_id"]

    # 落盘位置: 账号目录内, 不是全局 user_data
    assert (tmp_path / "user_data" / "users" / "1" / "monitor_rules" / "mr_same_id.json").exists()
    assert not (tmp_path / "user_data" / "monitor_rules" / "mr_same_id.json").exists()


def test_same_id_in_two_accounts_keeps_both_in_engine(tmp_path):
    for uid in (1, 2):
        with tenant.user_scope(uid):
            mr.save_one(tmp_path, dict(RULE))

    rules = mr.load_owned(tmp_path)
    assert len(rules) == 2
    assert {r["id"] for r in rules} == {"u1::mr_same_id", "u2::mr_same_id"}
    assert {r["_owner"] for r in rules} == {1, 2}

    engine = MonitorRuleEngine()
    engine.set_rules(rules)
    # 引擎按 id 做键: 若不命名空间, 这里会是 1 (后者覆盖前者)
    assert engine.rule_count == 2


def test_assign_owners_strips_namespace_and_sets_owner(tmp_path):
    events = [
        {"rule_id": "u2::mr_x", "source": "price", "message": "a"},
        {"rule_id": "mr_legacy", "source": "signal", "message": "b"},
    ]
    out = mr.assign_owners(events)
    assert out[0]["rule_id"] == "mr_x" and out[0]["owner_id"] == 2
    # 共享旧规则: 不改 id 也不加 owner (向后兼容, 全员可见)
    assert out[1]["rule_id"] == "mr_legacy" and "owner_id" not in out[1]


def test_group_by_owner_splits_events():
    events = [{"owner_id": 1}, {"owner_id": 2}, {"owner_id": 1}, {"rule_id": "x"}]
    groups = mr.group_by_owner(events)
    assert len(groups[1]) == 2
    assert len(groups[2]) == 1
    assert len(groups[None]) == 1


def test_legacy_global_rules_remain_shared(tmp_path):
    """账号系统之前的旧规则 (全局目录) 继续参与评估, id 不加前缀、owner 为空。"""
    (tmp_path / "user_data").mkdir(parents=True, exist_ok=True)
    with tenant.user_scope(None):
        mr.save_one(tmp_path, dict(RULE, id="mr_legacy"))

    rules = mr.load_owned(tmp_path)
    legacy = [r for r in rules if r["id"] == "mr_legacy"]
    assert len(legacy) == 1 and legacy[0].get("_owner") is None


def test_load_owned_ignores_non_numeric_user_dirs(tmp_path):
    (tmp_path / "user_data" / "users" / "1").mkdir(parents=True)
    (tmp_path / "user_data" / "users" / "cache").mkdir(parents=True)
    with tenant.user_scope(1):
        mr.save_one(tmp_path, dict(RULE))

    rules = mr.load_owned(tmp_path)
    assert [r["_owner"] for r in rules] == [1]


def test_append_owned_writes_into_each_account_file(tmp_path):
    counts = alert_store.append_owned(tmp_path, [
        {"ts": 1, "message": "u1", "owner_id": 1},
        {"ts": 2, "message": "u2", "owner_id": 2},
        {"ts": 3, "message": "u2", "owner_id": 2},
    ])
    assert counts == {1: 1, 2: 2}
    assert alert_store.count(tmp_path, owner=1) == 1
    assert alert_store.count(tmp_path, owner=2) == 2
    assert alert_store.count(tmp_path, owner=None) == 0
