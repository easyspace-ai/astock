"""租户隔离测试: 用户私有数据按 user_id 分区, 互不可见。

覆盖: tenant 上下文切换、AI 报告隔离、偏好设置隔离。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.services import preferences, tenant
from app.services.json_report_store import JsonReportStore


@pytest.fixture(autouse=True)
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """隔离真实数据目录, 避免测试写到用户 data/。"""
    from app.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    tenant.set_current_user(None)
    yield tmp_path
    tenant.set_current_user(None)


def test_no_tenant_uses_global_dir(data_dir):
    base = tenant.priv_base()
    assert base == data_dir / "user_data"
    assert tenant.user_priv_dir() is None
    assert tenant.is_tenant() is False


def test_tenant_dir_scoped_by_user(data_dir):
    tenant.set_current_user(42)
    d = tenant.user_priv_dir()
    assert d is not None and d.name == "42"
    assert tenant.is_tenant() is True
    tenant.set_current_user(99)
    d2 = tenant.user_priv_dir()
    assert d2 is not None and d2.name == "99"
    assert d != d2


def test_json_report_store_isolated(data_dir):
    tenant.set_current_user(7)
    store_a = JsonReportStore("tenant_test_reports.json", 20, id_prefix="t")
    store_a.save_report({"symbol": "AAA", "uid": 7})
    pa = tenant.priv_base() / "tenant_test_reports.json"
    assert pa.exists()

    tenant.set_current_user(8)
    store_b = JsonReportStore("tenant_test_reports.json", 20, id_prefix="t")
    assert store_b.list_reports() == []
    pb = tenant.priv_base() / "tenant_test_reports.json"
    assert pb != pa and not pb.exists()

    tenant.set_current_user(None)
    store_g = JsonReportStore("tenant_test_reports.json", 20, id_prefix="t")
    assert store_g.list_reports() == []


def test_ai_reports_module_isolated_between_accounts(data_dir):
    """财务分析报告的真实读写在请求线程内完成, 必须落到当前账号目录。

    存储层隔离由 JsonReportStore 提供, 这里验证 ai_reports 这个共享单例确实
    按调用时的租户上下文解析路径 (而不是固化了某个账号的目录)。
    """
    from app.services import ai_reports

    tenant.set_current_user(7)
    saved = ai_reports.save_report({"symbol": "600519.SH", "name": "贵州茅台", "content": "A"})
    assert saved["id"]
    assert (data_dir / "user_data" / "users" / "7" / "ai_reports.json").exists()

    tenant.set_current_user(8)
    assert ai_reports.list_reports() == []
    assert not (data_dir / "user_data" / "users" / "8" / "ai_reports.json").exists()

    tenant.set_current_user(7)
    assert [r["id"] for r in ai_reports.list_reports()] == [saved["id"]]
    assert ai_reports.delete_report(saved["id"]) is True
    assert ai_reports.list_reports() == []


def test_ai_reports_legacy_global_file_not_visible_to_accounts(data_dir):
    """升级前的全局文件不会被任意账号"继承" —— 谁都不该看到上一任的数据。"""
    from app.services import ai_reports

    legacy = data_dir / "user_data"
    legacy.mkdir(parents=True, exist_ok=True)
    (legacy / "ai_reports.json").write_text(
        '[{"id": "rpt_1", "symbol": "000001.SZ", "content": "legacy", "created_at": "2026-01-01T00:00:00"}]',
        encoding="utf-8",
    )
    for uid in (7, 8):
        tenant.set_current_user(uid)
        assert ai_reports.list_reports() == []


def test_preferences_remain_global(data_dir):
    """偏好设置是系统级配置(定时任务/推送/SMTP), 不随账号隔离。"""
    tenant.set_current_user(7)
    preferences.save({"system_level_marker": 1})
    p = data_dir / "user_data" / "preferences.json"
    assert p.exists()
    assert not (tenant.priv_base() / "preferences.json").exists()

    tenant.set_current_user(8)
    assert preferences.load().get("system_level_marker") == 1
