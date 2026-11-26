"""扩展内自建 APScheduler：TSP 契约没有公开定时任务注册 API。"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from . import config, service, store

logger = logging.getLogger(__name__)
_LOCK = threading.Lock()
_SCHEDULER: BackgroundScheduler | None = None


def start(data_dir: str | Path) -> BackgroundScheduler | None:
    global _SCHEDULER
    with _LOCK:
        if _SCHEDULER is not None:
            return _SCHEDULER
        cfg = config.load_config(data_dir)
        schedule = cfg.get("schedule") or {}
        if not schedule.get("enabled", True):
            logger.info("info_hub scheduler disabled")
            return None
        scheduler = BackgroundScheduler(timezone="Asia/Shanghai", daemon=True)
        svc = service.service_for(data_dir)

        def run(kinds: list[str], label: str) -> None:
            try:
                result = svc.sync(kinds, live=True, actor="scheduler")
                store.record_run(data_dir, {"action": label, "ok": True, "summary": _brief(result)})
            except Exception as exc:
                logger.exception("info_hub %s failed", label)
                store.record_run(data_dir, {"action": label, "ok": False, "error": f"{type(exc).__name__}: {exc}"[:200]})

        minutes = max(int(schedule.get("flash_minutes", 30)), 5)
        scheduler.add_job(
            run, CronTrigger(minute=f"*/{minutes}", hour="7-23"),
            args=[["flash"], "scheduled_flash"], id="info_flash", replace_existing=True,
            max_instances=1, coalesce=True, misfire_grace_time=300,
        )
        news_minutes = max(int(schedule.get("news_minutes", 30)), 5)
        scheduler.add_job(
            run, CronTrigger(minute=f"*/{news_minutes}", hour="8-22"),
            args=[["news"], "scheduled_news"], id="info_news", replace_existing=True,
            max_instances=1, coalesce=True, misfire_grace_time=600,
        )
        for index, at in enumerate(schedule.get("announcement_times") or ["16:40", "20:30"]):
            hour, minute = _hm(at)
            scheduler.add_job(
                run, CronTrigger(hour=hour, minute=minute),
                args=[["announcements"], f"scheduled_announcements_{at}"], id=f"info_ann_{index}",
                replace_existing=True, max_instances=1, coalesce=True, misfire_grace_time=900,
            )
        hour, minute = _hm(schedule.get("repair_time") or "06:50")
        scheduler.add_job(
            run, CronTrigger(hour=hour, minute=minute),
            args=[["announcements", "news"], "scheduled_repair"], id="info_repair",
            replace_existing=True, max_instances=1, coalesce=True, misfire_grace_time=1800,
        )
        scheduler.start()
        _SCHEDULER = scheduler
        logger.info("info_hub scheduler started: %s jobs", len(scheduler.get_jobs()))
        return scheduler


def status() -> dict[str, Any]:
    if _SCHEDULER is None:
        return {"running": False, "jobs": []}
    return {
        "running": _SCHEDULER.running,
        "jobs": [
            {"id": job.id, "next_run": job.next_run_time.isoformat() if job.next_run_time else None}
            for job in _SCHEDULER.get_jobs()
        ],
    }


def _hm(text: str) -> tuple[int, int]:
    parts = str(text).split(":")
    return int(parts[0]), int(parts[1]) if len(parts) > 1 else 0


def _brief(result: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for key, value in result.items():
        if isinstance(value, dict):
            out[key] = {k: v for k, v in value.items() if k in ("rows", "cninfo", "em", "news", "flash", "errors", "degraded", "codes")}
        elif key != "at":
            out[key] = value
    return out
