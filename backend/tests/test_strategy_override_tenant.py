"""策略参数覆盖 (strategy_overrides) 按账号隔离 + 后台任务的账号上下文。

覆盖三条链路:
  1. 存储层: A 存参数不影响 B, 各自落在 users/<uid>/strategy_overrides/
  2. 回测 worker: make_worker_task 把 owner 随任务带进子进程
  3. 挖掘/守护: 后台线程显式进入账号上下文后再建任务 (threading 不继承 ContextVar)
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest

from app.services import mining_manager as mining_manager_module
from app.services import tenant
from app.services.heavy_job_limiter import HeavyJobLimiter
from app.services.mining_manager import MiningJobManager
from app.strategy import config as strategy_config


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    from app import config as app_config

    monkeypatch.setattr(app_config.settings, "data_dir", tmp_path)
    return tmp_path


def _clear_override_cache() -> None:
    strategy_config._override_cache.clear()
    strategy_config._override_cache_sig.clear()


def test_override_storage_is_per_account(tmp_path):
    with tenant.user_scope(1):
        strategy_config.save_override(tmp_path, "custom_demo", {"params": {"ma": 20}})
        assert strategy_config.load_override(tmp_path, "custom_demo")["params"] == {"ma": 20}
        assert strategy_config.list_overrides(tmp_path) == {"custom_demo": {"params": {"ma": 20}}}
    _clear_override_cache()
    with tenant.user_scope(2):
        assert strategy_config.load_override(tmp_path, "custom_demo") == {}
        assert strategy_config.list_overrides(tmp_path) == {}
    _clear_override_cache()
    # 无账号(全局模式)读不到账号目录里的覆盖
    assert strategy_config.load_override(tmp_path, "custom_demo") == {}
    assert (tmp_path / "user_data/users/1/strategy_overrides/custom_demo.json").is_file()


def test_override_delete_only_affects_own_account(tmp_path):
    with tenant.user_scope(1):
        strategy_config.save_override(tmp_path, "custom_demo", {"params": {"ma": 20}})
    _clear_override_cache()
    with tenant.user_scope(2):
        strategy_config.save_override(tmp_path, "custom_demo", {"params": {"ma": 10}})
    _clear_override_cache()
    with tenant.user_scope(2):
        strategy_config.delete_override(tmp_path, "custom_demo")
    _clear_override_cache()
    with tenant.user_scope(1):
        assert strategy_config.load_override(tmp_path, "custom_demo")["params"] == {"ma": 20}


def test_make_worker_task_carries_owner(tmp_path):
    from app.backtest.worker import make_worker_task

    cfg: dict[str, Any] = {"strategy_ids": ["custom_demo"], "start": "2026-01-01", "end": "2026-06-30"}
    with tenant.user_scope(1):
        task = make_worker_task("mining", tmp_path, cfg)
        assert task["owner"] == 1
    # 调度线程无上下文 → 系统任务(owner=None), 不会误读某个人的参数
    assert make_worker_task("mining", tmp_path, cfg)["owner"] is None


def test_mining_thread_enters_owner_scope(tmp_path):
    """后台线程里 task_factory 能看到提交时的账号。"""
    seen: dict[str, Any] = {}
    done = threading.Event()

    def factory(kind: str, data_dir: Path, payload: dict[str, Any]) -> dict[str, Any]:
        seen["owner"] = tenant.current_user_id()
        return {"kind": kind, "data_dir": str(data_dir), "config": payload}

    def runner(task: dict[str, Any], _progress, _cancel) -> dict[str, Any]:
        seen["runner_owner"] = tenant.current_user_id()
        done.set()
        return {"ok": True}

    manager = MiningJobManager(tmp_path, worker_runner=runner, task_factory=factory)
    request = {"strategies": ["custom_demo"]}
    fingerprint = {"schema": "test"}
    with tenant.user_scope(7):
        manager.start(request, fingerprint, force=True)
    assert done.wait(timeout=5.0)
    assert seen["owner"] == 7
    assert seen["runner_owner"] == 7


def test_mining_thread_without_owner_stays_global(tmp_path, monkeypatch):
    """系统级调度(无账号上下文)提交的任务保持 owner=None。"""
    seen: dict[str, Any] = {}
    done = threading.Event()

    def factory(kind: str, data_dir: Path, payload: dict[str, Any]) -> dict[str, Any]:
        seen["owner"] = tenant.current_user_id()
        return {"kind": kind, "data_dir": str(data_dir), "config": payload}

    def runner(task: dict[str, Any], _progress, _cancel) -> dict[str, Any]:
        done.set()
        return {"ok": True}

    monkeypatch.setattr(mining_manager_module, "shared_heavy_job_limiter", HeavyJobLimiter())
    manager = MiningJobManager(tmp_path, worker_runner=runner, task_factory=factory)
    manager.start({"strategies": ["custom_demo"]}, {"schema": "test"}, force=True)
    assert done.wait(timeout=5.0)
    assert seen["owner"] is None
