"""运维监控线程 — 磁盘、日志落盘、调度器、数据完整性的周期巡检。"""
from __future__ import annotations

import logging
import threading
from typing import Any

from app.config import settings
from app.services import system_health
from app.services.system_alert import emit

logger = logging.getLogger(__name__)


class OpsMonitor:
    def __init__(self, app_state: Any) -> None:
        self._app_state = app_state
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="ops-monitor", daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3.0)
            self._thread = None

    def _loop(self) -> None:
        # 启动即巡检一次: 上次进程留下的磁盘/完整性/日志异常不能被 5 分钟延迟。
        while not self._stop.is_set():
            try:
                self.check_once()
            except Exception:
                logger.exception("ops monitor check failed")
            self._stop.wait(max(15.0, float(settings.ops_monitor_interval_s)))

    def check_once(self) -> None:
        data_dir = system_health.data_dir_of(self._app_state)
        self._check_disk(data_dir)
        self._check_logs(data_dir)
        self._check_integrity(data_dir)
        self._check_scheduler(data_dir)
        self._check_daily_finalization(data_dir)

    def _check_daily_finalization(self, data_dir) -> None:
        """盘后定版守卫: 过点未定版立即告警并尝试官方日K补跑。"""
        from app.market_time import cn_now, cn_today
        from app.services import daily_catchup, finalization

        try:
            need, reason = daily_catchup.should_catch_up(data_dir, allow_late=True)
        except Exception as e:  # noqa: BLE001
            logger.warning("盘后定版巡检失败: %s", e)
            return
        if need:
            try:
                from app.services.pipeline_jobs import job_store
                active_id = job_store.active_id()
            except Exception:  # noqa: BLE001
                active_id = None
            if active_id:
                # 16:40 时官方日K batch 可能仍在跑 (全市场分块), 不能当成
                # "未定版"发 critical; 等任务结束下一轮巡检再判断。
                logger.info("盘后定版巡检: 管道任务 %s 仍在运行, 暂不告警", active_id)
                return
            if cn_now().time() > daily_catchup.CATCHUP_POLL_UNTIL:
                emit(
                    "daily_not_finalized", "critical", "盘后日K仍未定版",
                    f"{reason}；已过补跑轮询窗口，不再反复跑全量管道，请检查官方源",
                    dedupe_key="daily_finalize",
                    data_dir=data_dir,
                )
                return
            # 16:40-17:40 是补跑窗口: 只记录并重试, 不提前发 critical。
            # 17:40 仍未定版才升级告警, 避免数据源正常延迟发布时误报。
            logger.warning("盘后日K尚未定版: %s; 启动官方日K补跑", reason)
            daily_catchup.launch_daily_catchup(self._app_state)
            return
        if finalization.is_finalized(data_dir, "kline_daily", cn_today()):
            emit(
                "daily_finalize_recovered", "info", "盘后日K已定版",
                "当日日K已由官方批次写入并打定版标记",
                dedupe_key="daily_finalize",
                data_dir=data_dir,
            )

    def _check_disk(self, data_dir) -> None:
        disk = system_health.disk_status(data_dir)
        free = disk.get("free_gb")
        if free is None:
            return
        if free < settings.ops_disk_critical_gb:
            emit(
                "disk_low", "critical", "磁盘空间严重不足",
                f"数据目录剩余 {free:.1f} GB，低于 {settings.ops_disk_critical_gb:.1f} GB；"
                "日志/数据写入可能失败，请立即清理或扩容",
                dedupe_key="disk",
                data={"free_gb": free, "used_pct": disk.get("used_pct")},
                data_dir=data_dir,
            )
        elif free < settings.ops_disk_warn_gb:
            emit(
                "disk_low", "warn", "磁盘空间低于预警水位",
                f"数据目录剩余 {free:.1f} GB，低于 {settings.ops_disk_warn_gb:.1f} GB",
                dedupe_key="disk",
                data={"free_gb": free, "used_pct": disk.get("used_pct")},
                data_dir=data_dir,
            )
        else:
            emit(
                "disk_recovered", "info", "磁盘空间恢复正常",
                f"数据目录剩余 {free:.1f} GB",
                dedupe_key="disk",
                data={"free_gb": free, "used_pct": disk.get("used_pct")},
                data_dir=data_dir,
            )

    def _check_logs(self, data_dir) -> None:
        logs = system_health.file_log_status(data_dir)
        if logs.get("file_logging_disabled"):
            emit(
                "log_file_disabled", "critical", "文件日志已停用",
                "文件日志 handler 因磁盘空间不足或文件占用停用；终端仍有日志，"
                "但重启前落盘证据会缺失。请检查磁盘与 logs 目录权限。",
                dedupe_key="log_file",
                data={"backend_log": logs.get("backend_log"), "error_log": logs.get("error_log")},
                data_dir=data_dir,
            )
        else:
            emit(
                "log_file_recovered", "info", "文件日志恢复正常",
                "文件日志 handler 正常写入",
                dedupe_key="log_file",
                data_dir=data_dir,
            )

    def _check_integrity(self, data_dir) -> None:
        integrity = system_health.integrity_status(data_dir)
        pending = integrity.get("pending_count")
        if pending is None:
            emit(
                "integrity_unavailable", "warn", "数据完整性状态不可读",
                f"无法读取完整性状态: {integrity.get('detail') or 'unknown'}",
                dedupe_key="integrity",
                data=integrity,
                data_dir=data_dir,
            )
            return
        if pending:
            emit(
                "integrity_blocked", "critical", "数据完整性阻塞",
                f"有 {pending} 条日K待修复记录，enriched/策略/监控可能被阻断；"
                "请查看数据页自检与修补，或等待自动修复重试",
                dedupe_key="integrity",
                data={
                    "pending_count": pending,
                    "days": integrity.get("days"),
                    "assets": integrity.get("assets"),
                    "kinds": integrity.get("kinds"),
                },
                data_dir=data_dir,
            )
        else:
            emit(
                "integrity_recovered", "info", "数据完整性恢复",
                "日K待修复记录已清空",
                dedupe_key="integrity",
                data_dir=data_dir,
            )

    def _check_scheduler(self, data_dir) -> None:
        state = system_health.scheduler_status(self._app_state)
        if state.get("ok"):
            emit(
                "scheduler_recovered", "info", "调度器恢复正常",
                "调度器运行中且心跳正常",
                dedupe_key="scheduler",
                data_dir=data_dir,
            )
        else:
            emit(
                "scheduler_unhealthy", "critical", "调度器不可用",
                f"调度器状态异常: {state.get('detail')}",
                dedupe_key="scheduler",
                data=state,
                data_dir=data_dir,
            )


def start_ops_monitor(app_state: Any) -> OpsMonitor:
    monitor = OpsMonitor(app_state)
    monitor.start()
    return monitor
