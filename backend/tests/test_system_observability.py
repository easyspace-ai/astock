"""可观测性 P0: 日志脱敏、系统告警去重/恢复、readiness 心跳判定。"""
from __future__ import annotations

import time
from types import SimpleNamespace

from app.config import settings
from app.log_safety import redact_text
from app.services import system_alert, system_health


def test_redact_authorization_header() -> None:
    text = "Authorization: Bearer sk-secret-123 api_key=abcd1234"
    out = redact_text(text)
    assert "sk-secret-123" not in out
    assert "abcd1234" not in out
    assert "Authorization: ***" in out


def test_system_alert_dedup_and_recovery(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "ops_alert_enabled", True)
    monkeypatch.setattr(settings, "ops_alert_cooldown_s", 1800)
    key = "source:realtime.tdxrs"

    assert system_alert.emit(
        "source_down", "critical", "数据源不可用", "timeout",
        dedupe_key=key, data_dir=tmp_path, dispatch_webhooks=False,
    )
    # 冷却窗口内的同严重度故障不得重复写
    assert not system_alert.emit(
        "source_down", "critical", "数据源不可用", "timeout",
        dedupe_key=key, data_dir=tmp_path, dispatch_webhooks=False,
    )
    # 恢复只发一次, 并清掉 active
    assert system_alert.emit(
        "source_recovered", "info", "数据源恢复", "ok",
        dedupe_key=key, data_dir=tmp_path, dispatch_webhooks=False,
    )
    assert not system_alert.emit(
        "source_recovered", "info", "数据源恢复", "ok",
        dedupe_key=key, data_dir=tmp_path, dispatch_webhooks=False,
    )


def test_readiness_requires_fresh_scheduler_heartbeat(tmp_path) -> None:
    class Scheduler:
        running = True

    state = SimpleNamespace(
        repo=SimpleNamespace(store=SimpleNamespace(data_dir=str(tmp_path))),
        scheduler=Scheduler(),
        system_heartbeat_at=time.time(),
    )
    report = system_health.readiness_report(state)
    assert report["status"] == "ready"
    assert all(check["ok"] for check in report["checks"].values())

    state.system_heartbeat_at = time.time() - 1000
    report = system_health.readiness_report(state)
    assert report["status"] == "degraded"
    assert report["checks"]["scheduler"]["ok"] is False


def test_ops_monitor_writes_alerts_to_actual_data_dir(tmp_path, monkeypatch) -> None:
    from app.services.ops_monitor import OpsMonitor

    data_dir = tmp_path / "app"
    other_dir = tmp_path / "other"
    monkeypatch.setattr(settings, "data_dir", other_dir)
    monkeypatch.setattr(settings, "ops_alert_enabled", True)

    state = SimpleNamespace(
        repo=SimpleNamespace(store=SimpleNamespace(data_dir=str(data_dir))),
        scheduler=None,
        system_heartbeat_at=None,
    )
    OpsMonitor(state).check_once()

    alert_file = data_dir / "user_data" / "alerts.jsonl"
    assert alert_file.exists()
    assert "scheduler_unhealthy" in alert_file.read_text(encoding="utf-8")
    assert not (other_dir / "user_data" / "alerts.jsonl").exists()
