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


def test_preferences_remain_global(data_dir):
    """偏好设置是系统级配置(定时任务/推送/SMTP), 不随账号隔离。"""
    tenant.set_current_user(7)
    preferences.save({"system_level_marker": 1})
    p = data_dir / "user_data" / "preferences.json"
    assert p.exists()
    assert not (tenant.priv_base() / "preferences.json").exists()

    tenant.set_current_user(8)
    assert preferences.load().get("system_level_marker") == 1
