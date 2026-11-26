"""启动补跑 — 进程在盘后时点不在线时, 不能指望 cron 自动补。

APScheduler 的内存 job store 在进程退出后没有"这个任务错过了"的记录;
`misfire_grace_time` 只在调度线程卡住时有效。因此生产环境必须有一层
**基于数据状态的启动补偿**:

- 今天是交易日;
- 当前北京时间已过官方日K主跑时点 (默认 16:40);
- 今天的 `kline_daily` 还没有定版标记。

满足即启动一次官方日K补跑。补跑走 `run_now(override_start_date=today)`,
因此走官方 batch 而不是实时 quotes, 并在成功后落定版标记。

主跑 16:40: 供应商 T+0 官方日K在 15:35 尚未发布; 线上 2026-11-23/24
连续两天 15:35、15:40、16:10 都是 0 行, 16:40 才成功。15:40-16:39 只做
启动补偿判断, 不空跑全量管道; 16:40 后每 10 分钟检查一次, 直到定版。

只补今天, 不在这里补历史: 历史坏分区由 `boot_integrity_check` + 自动修复处理,
避免启动时意外触发大范围回补。
"""
from __future__ import annotations

import contextlib
import logging
import threading
from datetime import datetime
from datetime import time as dt_time
from pathlib import Path

from app.market_time import CN_TZ, cn_now, cn_today

logger = logging.getLogger(__name__)

# 官方日K主跑默认 16:40; 补跑最早从 16:40 开始, 与正常 cron 错开。
# 数据源当日批次在 16:40 左右才稳定; 16:40-17:40 每 10 分钟重试一次,
# 成功后当天不再跑。15:40-16:39 返回 need=False, 避免无意义空跑全市场。
CATCHUP_AFTER = dt_time(16, 40)
CATCHUP_POLL_UNTIL = dt_time(17, 40)
CATCHUP_RETRY_S = 10 * 60

_LAST_ATTEMPT: dict[str, float] = {}
_BACKGROUND_TASKS: set = set()


def should_catch_up(
    data_dir: Path, *, now: datetime | None = None, allow_late: bool = False,
) -> tuple[bool, str]:
    """判断是否需要启动补跑; 返回 (need, reason)。

    allow_late=True 用于进程启动补偿: 即使已过 17:40 轮询窗口, 进程刚起来
    且当天仍未定版时仍应补一次。正常巡检默认 False, 避免整晚反复全量重跑。
    """
    from app.services import finalization, preferences, trading_day

    now = now or cn_now()
    if not preferences.get_pipeline_pull_a_share():
        return False, "未启用 A 股日K拉取"
    if now.tzinfo is None:
        now = now.replace(tzinfo=CN_TZ)
    day = now.date()
    if now.time() < CATCHUP_AFTER:
        return False, f"未到补跑时点 {CATCHUP_AFTER.strftime('%H:%M')}"
    if day.weekday() >= 5:
        return False, "周末休市"
    try:
        trading = trading_day.is_trading_day(now)
    except Exception as e:  # noqa: BLE001 — 日历不可用时工作日近似, 不阻断补偿
        logger.warning("补跑交易日判定失败(按工作日近似): %s", e)
        trading = None
    if trading is False:
        return False, "非交易日"
    if finalization.is_finalized(Path(data_dir), "kline_daily", day):
        return False, "当日已定版"
    if not allow_late and now.time() > CATCHUP_POLL_UNTIL:
        return False, f"已过补跑轮询窗口 {CATCHUP_POLL_UNTIL.strftime('%H:%M')}"
    return True, "当日已过盘后时点但仍未定版"


def launch_daily_catchup(app_state, *, allow_late: bool = False) -> tuple[str | None, bool]:
    """按数据状态启动补跑 (幂等, 与正常管道/修复共用 run slot)。"""
    repo = getattr(app_state, "repo", None)
    capset = getattr(app_state, "capabilities", None)
    if repo is None or capset is None:
        return None, False
    need, reason = should_catch_up(repo.store.data_dir, allow_late=allow_late)
    if not need:
        logger.info("启动补跑跳过: %s", reason)
        return None, False

    import time as _time

    day_key = cn_today().isoformat()
    last = _LAST_ATTEMPT.get(day_key, 0.0)
    if last and _time.time() - last < CATCHUP_RETRY_S:
        logger.debug("启动补跑冷却中 (当日已尝试过), 跳过")
        return None, False

    from app.services.pipeline_jobs import (
        JobCancelledError,
        job_store,
        release_run_slot,
        run_with_capacity,
        try_acquire_run_slot,
    )

    job_id, is_new = job_store.create()
    _LAST_ATTEMPT[day_key] = _time.time()
    if not is_new:
        logger.info("启动补跑复用进行中的任务 job=%s", job_id)
        return job_id, False

    from app.jobs.daily_pipeline import run_now

    qs = getattr(app_state, "quote_service", None)

    def progress(stage: str, pct: int, msg: str,
                 stage_pct: int | None = None, skip_log: bool = False) -> None:
        job_store.progress(job_id, stage, pct, msg, stage_pct=stage_pct, skip_log=skip_log)

    def _run() -> dict:
        with qs.paused() if qs else contextlib.nullcontext():
            result = run_now(
                repo, capset, on_progress=progress,
                override_start_date=cn_today(),
            )
        repo.refresh_cache()
        return {
            "catchup": True,
            "reason": reason,
            "date": cn_today().isoformat(),
            "result": result,
        }

    def _execute() -> None:
        try:
            if not try_acquire_run_slot(job_id):
                job_store.fail(job_id, "已有数据任务在运行, 启动补跑跳过")
                return
            result = run_with_capacity(job_id, _run)
            if isinstance(result, dict) and "error" in result:
                job_store.fail(job_id, str(result["error"]))
                _alert("critical", "启动补跑失败", f"job_id={job_id}: {result['error']}")
            else:
                job_store.succeed(job_id, result)
                _alert("info", "启动补跑完成", f"job_id={job_id} 已补跑 {cn_today()} 日K")
        except JobCancelledError:
            pass
        except Exception as exc:
            logger.exception("启动补跑失败 job=%s", job_id)
            job_store.fail(job_id, str(exc))
            _alert("critical", "启动补跑异常", f"job_id={job_id}: {type(exc).__name__}: {exc}")
        finally:
            release_run_slot(job_id)

    try:
        import asyncio

        loop = asyncio.get_running_loop()

        async def task() -> None:
            await loop.run_in_executor(None, _execute)

        bg = asyncio.create_task(task())
        _BACKGROUND_TASKS.add(bg)
        bg.add_done_callback(_BACKGROUND_TASKS.discard)
    except RuntimeError:
        threading.Thread(
            target=_execute, daemon=True, name=f"daily-catchup-{job_id[:8]}",
        ).start()

    logger.warning(
        "启动补跑: 当日未定版, 已创建官方日K补跑任务 job=%s (%s)", job_id, reason,
    )
    return job_id, True


def _alert(severity: str, title: str, message: str) -> None:
    try:
        from app.services.system_alert import emit

        emit(
            "daily_finalize_catchup", severity, title, message,
            dedupe_key="daily_finalize_catchup",
        )
    except Exception:  # noqa: BLE001
        logger.debug("启动补跑告警发送失败", exc_info=True)


__all__ = [
    "CATCHUP_AFTER", "CATCHUP_POLL_UNTIL", "CATCHUP_RETRY_S",
    "launch_daily_catchup", "should_catch_up",
]
