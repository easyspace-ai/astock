"""私有资产按账号隔离: 批次 / 模拟盘账户。

自选股早已按账号 (user_data/users/<uid>/watchlist.parquet); 本轮把同样
未归属的「批次登记」和「模拟盘账户」搬到 <私有根> 下:

  1. lots 落在 user_data/users/<uid>/lots/lot_*.json, 账号间互不可见;
  2. 模拟盘账户落在 user_data/users/<uid>/paper/accounts/<acc>/;
  3. 后台撮合/结算线程没有租户上下文, 用 account_refs() 跨账号枚举再逐账户
     进入 user_scope — 否则看不到任何账号目录下的账户。
"""
from __future__ import annotations

from app.services import tenant
from app.strategy import lots, paper

LOT = {
    "id": "lot_1",
    "symbol": "600519.SH",
    "name": "贵州茅台",
    "qty": 100,
    "cost": 1500.0,
    "buy_date": "2026-11-01",
    "note": "",
}


def test_lots_are_stored_per_account(tmp_path):
    with tenant.user_scope(1):
        lots.save_one(tmp_path, dict(LOT))
        assert [r["id"] for r in lots.load_all(tmp_path)] == ["lot_1"]

    with tenant.user_scope(2):
        assert lots.load_all(tmp_path) == []

    assert (tmp_path / "user_data" / "users" / "1" / "lots" / "lot_1.json").exists()
    assert not (tmp_path / "user_data" / "lots" / "lot_1.json").exists()


def test_paper_accounts_are_stored_per_account(tmp_path):
    with tenant.user_scope(1):
        paper.create_account(tmp_path, 1_000_000, account_id="default")
        assert paper.list_account_ids(tmp_path) == ["default"]

    with tenant.user_scope(2):
        assert paper.list_account_ids(tmp_path) == []
        assert paper.get_account(tmp_path, "default") is None

    assert (tmp_path / "user_data" / "users" / "1" / "paper" / "accounts" / "default" / "account.json").exists()


def test_account_refs_enumerates_all_accounts(tmp_path):
    """后台遍历: 各账号账户 + 旧全局账户 (owner=None) 都要能被发现。"""
    with tenant.user_scope(1):
        paper.create_account(tmp_path, 100_000, account_id="default")
    with tenant.user_scope(2):
        paper.create_account(tmp_path, 200_000, account_id="default")
        paper.create_account(tmp_path, 200_000, account_id="acc_b")

    # 旧全局布局 (账号系统之前): 直接落在 data/paper/accounts
    legacy = tmp_path / "paper" / "accounts" / "old_acc"
    legacy.mkdir(parents=True)
    (legacy / "account.json").write_text(
        '{"id": "old_acc", "created_at": "2026-01-01T00:00:00"}', encoding="utf-8"
    )

    assert set(paper.account_refs(tmp_path)) == {
        (None, "old_acc"), (1, "default"), (2, "acc_b"), (2, "default"),
    }


def test_settle_runs_inside_account_scope(tmp_path):
    """结算必须在对应账号的上下文里跑, 否则读不到账户 (返回空摘要)。"""
    with tenant.user_scope(1):
        paper.create_account(tmp_path, 100_000, account_id="default")

    # 无租户上下文: 看不到账号目录下的账户
    assert paper.get_account(tmp_path, "default") is None
    assert paper.settle_day(tmp_path, "2026-11-26", account_id="default")["filled"] == 0

    with tenant.user_scope(1):
        summary = paper.settle_day(tmp_path, "2026-11-26", account_id="default")
    assert summary["account"] == "default"

    assert paper.account_refs(tmp_path) == [(1, "default")]
