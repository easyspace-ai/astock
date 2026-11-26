"""系统健康 — readiness、磁盘水位、日志/调度器/数据完整性状态。

只读设计:
  - `/ready` 只做轻量检查 (目录写探针、磁盘、SQLite 读、调度器心跳)。
  - `/api/system/health` 额外读 source_health/daily_integrity/alerts, 供管理页排障。
  - 不把外部数据源可用性作为 readiness 失败条件: 上游抖动不应导致容器反复重启。
"""
from __future__ import annotations

import logging
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

from app.config import settings
from app.market_time import cn_now

logger = logging.getLogger(__name__)


def data_dir_of(app_state: Any | None = None) -> Path:
    repo = getattr(app_state, "repo", None) if app_state is not None else None
    store = getattr(repo, "store", None) if repo is not None else None
    return Path(getattr(store, "data_dir", settings.data_dir))


def _check_data_dir(data_dir: Path) -> dict:
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".ready-", dir=data_dir)
        os.close(fd)
        Path(name).unlink(missing_ok=True)
        return {"ok": True, "detail": "可写"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "detail": f"不可写: {type(exc).__name__}"}


def disk_status(data_dir: Path) -> dict:
    try:
        usage = shutil.disk_usage(data_dir)
    except OSError as exc:
        return {
            "ok": False,
            "detail": f"无法读取磁盘: {type(exc).__name__}",
            "total_gb": None,
            "free_gb": None,
            "used_pct": None,
            "warn_gb": settings.ops_disk_warn_gb,
            "critical_gb": settings.ops_disk_critical_gb,
        }
    free_gb = usage.free / (1024 ** 3)
    total_gb = usage.total / (1024 ** 3)
    used_pct = (usage.used / usage.total * 100.0) if usage.total else 0.0
    return {
        "ok": free_gb >= settings.ops_disk_critical_gb,
        "detail": "正常" if free_gb >= settings.ops_disk_warn_gb else "低于预警水位",
        "total_gb": round(total_gb, 2),
        "free_gb": round(free_gb, 2),
        "used_pct": round(used_pct, 1),
        "warn_gb": settings.ops_disk_warn_gb,
        "critical_gb": settings.ops_disk_critical_gb,
    }


def _check_user_db() -> dict:
    try:
        from app.services import user_store

        user_store.health_check()
        return {"ok": True, "detail": "可读"}
    except Exception as exc:  # noqa: BLE001 — 不暴露 SQL/路径细节
        return {"ok": False, "detail": f"不可读: {type(exc).__name__}"}


def scheduler_status(app_state: Any | None) -> dict:
    scheduler = getattr(app_state, "scheduler", None) if app_state is not None else None
    heartbeat = getattr(app_state, "system_heartbeat_at", None) if app_state is not None else None
    if scheduler is None:
        return {"ok": False, "running": False, "last_heartbeat_age_s": None,
                "detail": "调度器未启动"}
    try:
        running = bool(scheduler.running)
    except Exception:  # noqa: BLE001
        running = False
    age = None if not heartbeat else round(max(0.0, time.time() - float(heartbeat)), 1)
    stale = age is None or age > 90.0
    return {
        "ok": running and not stale,
        "running": running,
        "last_heartbeat_age_s": age,
        "detail": "正常" if running and not stale else (
            "调度器未运行" if not running else "心跳超时 (>90s)"
        ),
    }


def file_log_status(data_dir: Path) -> dict:
    from app.logfile import file_logging_disabled

    def info(name: str) -> dict:
        path = data_dir / "logs" / name
        try:
            stat = path.stat()
            return {
                "exists": True,
                "size_bytes": stat.st_size,
                "mtime": stat.st_mtime,
            }
        except FileNotFoundError:
            return {"exists": False, "size_bytes": 0, "mtime": None}
        except OSError:
            return {"exists": False, "size_bytes": 0, "mtime": None}

    return {
        "ok": not file_logging_disabled(),
        "file_logging_disabled": file_logging_disabled(),
        "backend_log": info("backend.log"),
        "error_log": info("error.log"),
    }


def integrity_status(data_dir: Path) -> dict:
    try:
        from app.services import daily_health

        state = daily_health.snapshot(data_dir)
        entries = [e for e in state.get("entries", []) if isinstance(e, dict)]
        days = sorted({str(e.get("day")) for e in entries if e.get("day")})
        assets: dict[str, int] = {}
        kinds: dict[str, int] = {}
        for entry in entries:
            assets[str(entry.get("asset_type") or "unknown")] = (
                assets.get(str(entry.get("asset_type") or "unknown"), 0) + 1
            )
            kinds[str(entry.get("kind") or "unknown")] = (
                kinds.get(str(entry.get("kind") or "unknown"), 0) + 1
            )
        return {
            "status": "blocked" if entries else "clean",
            "pending_count": len(entries),
            "days": days[-5:],
            "assets": assets,
            "kinds": kinds,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "unavailable",
            "pending_count": None,
            "detail": f"{type(exc).__name__}",
            "days": [],
            "assets": {},
            "kinds": {},
        }


def source_status(data_dir: Path) -> dict:
    try:
        from app.services import source_health

        snap = source_health.snapshot(data_dir)
        targets = snap.get("targets") or []
        counts: dict[str, int] = {}
        for target in targets:
            state = str(target.get("state") or "unknown")
            counts[state] = counts.get(state, 0) + 1
        return {
            "updated_at": snap.get("updated_at"),
            "switches": snap.get("switches") or [],
            "active": snap.get("active") or {},
            "counts": counts,
            "targets": targets,
        }
    except Exception as exc:  # noqa: BLE001
        return {"updated_at": None, "switches": [], "active": {}, "counts": {},
                "targets": [], "detail": f"{type(exc).__name__}"}


def readiness_report(app_state: Any | None = None) -> dict:
    data_dir = data_dir_of(app_state)
    checks = {
        "data_dir": _check_data_dir(data_dir),
        "disk": disk_status(data_dir),
        "user_db": _check_user_db(),
        "scheduler": scheduler_status(app_state),
    }
    ready = all(bool(check.get("ok")) for check in checks.values())
    return {
        "status": "ready" if ready else "degraded",
        "checked_at": cn_now().replace(microsecond=0).isoformat(),
        "checks": checks,
    }


def system_status(app_state: Any | None = None) -> dict:
    data_dir = data_dir_of(app_state)
    logs = file_log_status(data_dir)
    return {
        "checked_at": cn_now().replace(microsecond=0).isoformat(),
        "readiness": readiness_report(app_state),
        "disk": disk_status(data_dir),
        "logs": logs,
        "scheduler": scheduler_status(app_state),
        "sources": source_status(data_dir),
        "integrity": integrity_status(data_dir),
    }
