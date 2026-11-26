"""数据源健康中心: 状态归并、快照分类与主备路由。"""
from __future__ import annotations

from datetime import timedelta

import polars as pl
import pytest

from app.market_time import cn_now
from app.services import preferences as pref_module
from app.services import source_health


@pytest.fixture(autouse=True)
def _always_continuous_session(monkeypatch):
    """主备路由测试不依赖运行时点; 默认视为连续竞价时段。"""
    monkeypatch.setattr(source_health, "_realtime_switch_allowed", lambda now=None: True)


def _target(capability: str = "realtime", provider: str = "tickflow") -> dict:
    return {
        "id": f"{capability}.{provider}",
        "category": capability,
        "provider": provider,
        "display": provider,
        "kind": "market",
        "dataset": capability,
    }


def _outcome(*, ok: bool, lag_seconds: float | None = None, error: str = "") -> source_health.ProbeOutcome:
    now = cn_now()
    data_time = None if lag_seconds is None else now - timedelta(seconds=lag_seconds)
    return source_health.ProbeOutcome(
        ok=ok,
        data_time=data_time,
        rows=1 if ok else 0,
        detail="ok" if ok else "",
        error=error,
        latency_ms=12,
    )


@pytest.mark.parametrize(
    ("lag", "expected"),
    [(5, "ok"), (source_health.MAX_LAG["realtime"] + 1, "degraded")],
)
def test_apply_outcome_uses_data_lag(lag: float, expected: str) -> None:
    entry = source_health._apply_outcome(None, _target(), _outcome(ok=True, lag_seconds=lag))
    assert entry["state"] == expected
    assert entry["consecutive_fail"] == 0
    assert entry["lag_seconds"] == pytest.approx(lag, abs=1)
    assert len(entry["history"]) == 1


def test_apply_outcome_marks_down_after_three_failures() -> None:
    target = _target()
    entry = None
    for _ in range(source_health._FAIL_DOWN):
        entry = source_health._apply_outcome(entry, target, _outcome(ok=False, error="timeout"))
    assert entry is not None
    assert entry["state"] == "down"
    assert entry["consecutive_fail"] == source_health._FAIL_DOWN
    assert entry["cooldown_until"]

    entry = source_health._apply_outcome(entry, target, _outcome(ok=True, lag_seconds=1))
    assert entry["state"] == "ok"
    assert entry["consecutive_fail"] == 0
    assert entry["cooldown_until"] == ""


def test_apply_outcome_trims_history() -> None:
    target = _target()
    entry = None
    for _ in range(source_health.HISTORY_LIMIT + 5):
        entry = source_health._apply_outcome(entry, target, _outcome(ok=True, lag_seconds=1))
    assert len(entry["history"]) == source_health.HISTORY_LIMIT


def test_probe_targets_isolates_source_errors(tmp_path, monkeypatch) -> None:
    def fake_run(target, _data_dir):
        if target["id"] == "realtime.bad":
            raise RuntimeError("boom")
        return _outcome(ok=True, lag_seconds=1)

    monkeypatch.setattr(source_health, "_run_probe", fake_run)
    snap = source_health.probe_targets(tmp_path, [
        {**_target("realtime", "bad"), "kind": "market"},
        {**_target("realtime", "good"), "kind": "market"},
    ])
    states = {entry["id"]: entry["state"] for entry in snap["targets"]}
    assert states == {"realtime.bad": "degraded", "realtime.good": "ok"}
    assert "boom" in next(e for e in snap["targets"] if e["id"] == "realtime.bad")["error"]


def test_snapshot_groups_categories_and_worst_state(tmp_path) -> None:
    now = cn_now().replace(microsecond=0)
    state = source_health._empty_state()
    state["updated_at"] = now.isoformat()
    state["targets"] = {
        "realtime.tickflow": {
            **_target("realtime", "tickflow"),
            "state": "ok",
            "consecutive_fail": 0,
            "checked_at": now.isoformat(),
        },
        "realtime.fuyao": {
            **_target("realtime", "fuyao"),
            "state": "degraded",
            "consecutive_fail": 1,
            "checked_at": now.isoformat(),
        },
    }
    source_health._save_state(tmp_path, state)

    snap = source_health.snapshot(tmp_path)
    assert [c["id"] for c in snap["categories"]] == ["realtime"]
    category = snap["categories"][0]
    assert category["state"] == "degraded"
    assert len(category["sources"]) == 2
    assert snap["updated_at"] == now.isoformat()


def test_probe_scope_filters_by_capability(tmp_path, monkeypatch) -> None:
    probed: list[str] = []

    def fake_targets(_data_dir):
        return [
            {**_target("realtime", "tickflow"), "kind": "market"},
            {**_target("daily", "tickflow"), "kind": "market"},
            {
                "id": "news.ths", "category": "news", "provider": "ths",
                "display": "同花顺", "kind": "info", "dataset": "news",
            },
        ]

    def fake_run(target, _data_dir):
        probed.append(target["id"])
        return _outcome(ok=True, lag_seconds=1)

    monkeypatch.setattr(source_health, "targets", fake_targets)
    monkeypatch.setattr(source_health, "_run_probe", fake_run)

    source_health.probe_scope(tmp_path, "market")
    assert probed == ["realtime.tickflow"]

    probed.clear()
    source_health.probe_scope(tmp_path, "info")
    assert probed == ["news.ths"]

    probed.clear()
    source_health.probe_scope(tmp_path, "all")
    assert probed == ["realtime.tickflow", "daily.tickflow", "news.ths"]


def _write_state(tmp_path, targets: dict) -> None:
    state = source_health._empty_state()
    state["targets"] = targets
    source_health._save_state(tmp_path, state)


def test_resolve_provider_switches_to_healthy_backup(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(pref_module, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(
        pref_module, "get_provider_fallbacks",
        lambda capability, primary="": [primary, "fuyao"],
    )
    from app.data_providers import custom as custom_sources
    monkeypatch.setattr(custom_sources, "provider_has_dataset", lambda name, dataset: True)

    _write_state(tmp_path, {
        "realtime.tickflow": {**_target("realtime", "tickflow"), "state": "down", "consecutive_fail": 3},
        "realtime.fuyao": {**_target("realtime", "fuyao"), "state": "ok", "consecutive_fail": 0},
    })

    assert source_health.resolve_provider("realtime", "tickflow") == "fuyao"
    snap = source_health.snapshot(tmp_path)
    assert snap["active"]["realtime"] == "fuyao"
    assert snap["switches"][-1]["to"] == "fuyao"
    assert snap["switches"][-1]["reason"] == "primary_unhealthy"


def test_resolve_provider_skips_backup_without_dataset(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(pref_module, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(
        pref_module, "get_provider_fallbacks",
        lambda capability, primary="": [primary, "fuyao"],
    )
    from app.data_providers import custom as custom_sources
    monkeypatch.setattr(custom_sources, "provider_has_dataset", lambda name, dataset: False)

    _write_state(tmp_path, {
        "realtime.tickflow": {**_target("realtime", "tickflow"), "state": "down", "consecutive_fail": 3},
        "realtime.fuyao": {**_target("realtime", "fuyao"), "state": "ok", "consecutive_fail": 0},
    })

    assert source_health.resolve_provider("realtime", "tickflow") == "tickflow"


def test_resolve_provider_keeps_healthy_primary(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(pref_module, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(
        pref_module, "get_provider_fallbacks",
        lambda capability, primary="": [primary, "fuyao"],
    )

    _write_state(tmp_path, {
        "realtime.tickflow": {**_target("realtime", "tickflow"), "state": "ok", "consecutive_fail": 0},
        "realtime.fuyao": {**_target("realtime", "fuyao"), "state": "ok", "consecutive_fail": 0},
    })

    assert source_health.resolve_provider("realtime", "tickflow") == "tickflow"
    assert source_health.snapshot(tmp_path)["switches"] == []


def test_resolve_provider_ignores_non_fallback_capability(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(pref_module, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(
        pref_module, "get_provider_fallbacks",
        lambda capability, primary="": [primary, "fuyao"],
    )
    _write_state(tmp_path, {
        "daily.tickflow": {**_target("daily", "tickflow"), "state": "down", "consecutive_fail": 3},
        "daily.fuyao": {**_target("daily", "fuyao"), "state": "ok", "consecutive_fail": 0},
    })

    assert source_health.resolve_provider("daily", "tickflow") == "tickflow"
    assert source_health.load_state(tmp_path)["switches"] == []


def test_resolve_provider_keeps_primary_outside_session(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(pref_module, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(
        pref_module, "get_provider_fallbacks",
        lambda capability, primary="": [primary, "fuyao"],
    )
    from app.data_providers import custom as custom_sources
    monkeypatch.setattr(custom_sources, "provider_has_dataset", lambda name, dataset: True)
    monkeypatch.setattr(source_health, "_realtime_switch_allowed", lambda now=None: False)

    _write_state(tmp_path, {
        "realtime.tickflow": {**_target("realtime", "tickflow"), "state": "down", "consecutive_fail": 3},
        "realtime.fuyao": {**_target("realtime", "fuyao"), "state": "ok", "consecutive_fail": 0},
    })

    assert source_health.resolve_provider("realtime", "tickflow") == "tickflow"
    assert source_health.snapshot(tmp_path)["switches"] == []


def test_resolve_provider_without_backup_keeps_primary(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(pref_module, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(
        pref_module, "get_provider_fallbacks",
        lambda capability, primary="": [primary],
    )
    _write_state(tmp_path, {
        "realtime.tickflow": {**_target("realtime", "tickflow"), "state": "down", "consecutive_fail": 3},
    })

    assert source_health.resolve_provider("realtime", "tickflow") == "tickflow"
    assert source_health.load_state(tmp_path)["switches"] == []

def test_get_provider_fallbacks_puts_current_primary_first(monkeypatch) -> None:
    monkeypatch.setattr(
        pref_module, "load",
        lambda: {"provider_fallbacks": {"realtime": ["tickflow", "fuyao"]}},
    )
    assert pref_module.get_provider_fallbacks("realtime", "tdxrs") == ["tdxrs", "tickflow", "fuyao"]
    assert pref_module.get_provider_fallbacks("realtime", "fuyao") == ["fuyao", "tickflow"]

def test_probe_does_not_close_shared_custom_provider(monkeypatch) -> None:
    closed: list[bool] = []

    class FakeProvider:
        def get_realtime(self, symbols=None):
            return pl.DataFrame({"symbol": list(symbols or [])})

        def close(self):
            closed.append(True)

    monkeypatch.setattr(source_health, "_provider", lambda name: FakeProvider())
    monkeypatch.setattr(
        source_health, "_call_with_deadline",
        lambda fn, timeout_s=12.0: fn(),
    )
    target = {**_target("realtime", "fuyao"), "kind": "market"}

    outcome = source_health._probe_market(target)
    assert outcome.ok is True
    assert closed == []
