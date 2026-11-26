"""FastAPI 入口。"""
from __future__ import annotations

import logging
import os
import time
import uuid
from urllib.parse import quote
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from app import __version__
from app.api import (
    abnormal,
    aistock_proxy,
    alerts,
    analysis,
    backtest,
    config_backup,
    data,
    definitions,
    events,
    ext_data,
    ext_pages,
    factors,
    financials,
    guardian,
    import_center,
    indices,
    intraday,
    kline,
    lab,
    lots,
    market_recap,
    mining,
    monitor_rules,
    overview,
    pages_admin,
    paper,
    pipeline,
    position_policies,
    regime,
    rps,
    screener,
    sector_rotation,
    signals,
    stock_analysis,
    stock_growth,
    strategy,
    strategy_pool,
    task_registry,
    watchlist,
)
from app.api import accounts as accounts_api
from app.api import admin_users as admin_users_api
from app.api import navigation as navigation_api
from app.api import system as system_api
from app.api import permissions as permissions_api
from app.api import settings as settings_api
from app.api import upgrade_guard as upgrade_guard_api
from app.api.routes import router as core_router
from app.config import settings
from app.data_providers.base import ProviderRequestError
from app.enriched_generation import EnrichedGenerationUnavailableError
from app.extensions.loader import (
    configure_backend_extensions,
    current_extension_context,
    start_backend_extensions,
)
from app.frontend_static import FrontendStaticFiles, frontend_file_response
from app.jobs import daily_pipeline
from app.services import page_access
from app.services.daily_health import DailyIntegrityError
from app.services.heavy_job_limiter import HeavyJobLimitTimeoutError
from app.services.matrix_prewarm_owner import MatrixCachePrewarmOwner
from app.services.mining_process_lock import MiningProcessLock, MiningProcessLockError
from app.services.quote_service import QuoteService
from app.services.shared_work import WorkBusyError
from app.startup_diagnostics import startup_phase
from app.tickflow import client as tf_client
from app.tickflow.policy import detect_capabilities
from app.tickflow.repository import DataStore, KlineRepository

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# 第三方 HTTP 客户端在 INFO 级别会逐请求打印完整 URL/状态, 盘中全量拉取会
# 把业务 ERROR 淹没。HTTP 访问日志由 request_context_middleware 统一记录。
for _noisy_logger in ("httpx", "httpcore", "urllib3", "apscheduler.executors.default"):
    logging.getLogger(_noisy_logger).setLevel(logging.WARNING)

# 追加文件日志: uvicorn (含 --reload 开发模式) 默认只有 StreamHandler, 同步/管道等
# 运行时日志仅出现在 dev 终端, 关掉或滚屏后即丢失, 排查「同步后日志没落」时无处可查。
# 落盘到 data/logs/backend.log 与桌面版 (desktop.py:_setup_logging → logs/desktop.log)
# 行为对齐, 事后可查。桌面版 (frozen) 已由 desktop.py 写 desktop.log, 此处跳过避免
# 重复落盘; 大小切割与旧位置迁移见 app/logfile.py。
# TSP_DISABLE_FILE_LOG=1 供 pytest 隔离: 测试导入 app.main 时不得写生产日志。
if not getattr(sys, "frozen", False) and os.environ.get("TSP_DISABLE_FILE_LOG") != "1":
    from app.logfile import attach_rotating_log
    from app.log_safety import install_redaction

    _backend_log_handler = attach_rotating_log(
        "backend.log",
        max_mb=settings.log_file_max_mb,
        backup_count=settings.log_file_backup_count,
    )
    _error_log_handler = attach_rotating_log(
        "error.log",
        max_mb=settings.error_log_max_mb,
        backup_count=settings.error_log_backup_count,
        level=logging.ERROR,
    )
    # uvicorn 的 access/error logger 默认不向 root 传播; 把同一文件 handler
    # 直接挂到它们上面, 500 traceback 和访问日志才会进入轮转文件。
    for _name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        _uvicorn_logger = logging.getLogger(_name)
        if _backend_log_handler is not None:
            _uvicorn_logger.addHandler(_backend_log_handler)
        if _error_log_handler is not None:
            _uvicorn_logger.addHandler(_error_log_handler)
        _uvicorn_logger.setLevel(logging.INFO)
        _uvicorn_logger.propagate = False
    install_redaction()


@asynccontextmanager
async def _application_lifespan(app: FastAPI):
    logger.info(
        "Tick Stock Panel v%s starting (mode=%s)",
        __version__, tf_client.current_mode(),
    )

    # 同步路由线程池容量: Starlette 默认 40, 本地多标签页/自动刷新与长计算
    # 路由并存时容易排队。调大只影响空闲线程上限 (按需创建), 无常驻代价。
    try:
        import anyio.to_thread

        anyio.to_thread.current_default_thread_limiter().total_tokens = (
            settings.api_sync_thread_pool
        )
        logger.info("sync route thread pool: %d", settings.api_sync_thread_pool)
    except Exception as e:  # noqa: BLE001
        logger.warning("thread pool resize failed (ignored): %s", e)

    # 多用户账号体系: 建表/迁移 + 可选环境变量初始化管理员
    # (ADMIN_EMAIL/ADMIN_PASSWORD 仅在用户表为空时生效)。
    try:
        with startup_phase(logger, "accounts bootstrap"):
            from app.services import user_store
            user_store.run_schema_migrations()
            admin_email = (settings.admin_email or "").strip().lower()
            admin_pwd = (settings.admin_password or "").strip()
            if admin_email and admin_pwd and not user_store.has_users():
                if "@" not in admin_email:
                    logger.warning("ADMIN_EMAIL 格式非法, 跳过管理员初始化")
                else:
                    try:
                        u = user_store.create_user(admin_email, admin_pwd, role=user_store.ROLE_ADMIN)
                        logger.info("admin account bootstrapped from env: %s", u["email"])
                    except ValueError as e:
                        logger.warning("admin bootstrap skipped: %s", e)
    except Exception as e:  # noqa: BLE001
        logger.warning("user store init failed: %s", e)

    # 数据层
    with startup_phase(logger, "data store initialization"):
        store = DataStore()
    with startup_phase(logger, "repository initialization"):
        repo = KlineRepository(store)
    app.state.datastore = store
    app.state.repo = repo
    # 去内置化种子迁移必须赶在下方 refresh_cache 之前: enriched 缓存构建时
    # 编译自定义信号表达式, 晚种会让本次启动的看板/梯队引用的 signal_limit_*
    # 列缺失 (跌停恒 0、连板梯队无数据), 直到下一次缓存刷新才自愈。
    _seeded_signals = 0
    try:
        with startup_phase(logger, "signal seeding"):
            from app.strategy import custom_signals as _cs_seed

            _seeded_signals = _cs_seed.seed_if_empty(store.data_dir)
    except Exception as e:  # noqa: BLE001
        logger.warning("signal seeding failed (ignored): %s", e)
    # 自定义/复合因子载入注册表 (P3); 单个失败只跳过该因子 (fail-隔离)
    with startup_phase(logger, "custom factor module import"):
        from app.factors.store import load_runtime_registry

    try:
        with startup_phase(logger, "custom factor registration"):
            loaded_factors = load_runtime_registry(store.data_dir)
        if loaded_factors:
            logger.info("custom factors loaded: %s", len(loaded_factors))
    except Exception as exc:  # noqa: BLE001
        logger.warning("custom factors load failed: %s", exc)
    with startup_phase(logger, "mining manager initialization"):
        from app.services.mining_jobs import MiningRunStore
        from app.services.mining_manager import MiningJobManager

        mining_manager = MiningJobManager(store.data_dir)
    # A degraded instance must never rewrite another owner's live run manifests.
    with startup_phase(logger, "mining run recovery"):
        recovered_mining_runs = (
            mining_manager.recover_interrupted() if app.state.mining_lock_acquired else 0
        )
    app.state.mining_manager = mining_manager
    if recovered_mining_runs:
        logger.warning("recovered %d interrupted mining runs", recovered_mining_runs)
    # 策略守护复验: 复用挖掘的任务编排 (线程 + spawn 子进程 + 持久 run store),
    # 独立 namespace 存 run 记录; 与挖掘共用 heavy 槽 (避免双份全市场矩阵)。
    with startup_phase(logger, "guardian manager initialization"):
        guardian_manager = MiningJobManager(
            store.data_dir,
            kind="guardian",
            store=MiningRunStore(store.data_dir, namespace="guardian"),
        )
    with startup_phase(logger, "guardian run recovery"):
        recovered_guardian_runs = guardian_manager.recover_interrupted()
    app.state.guardian_manager = guardian_manager
    if recovered_guardian_runs:
        logger.warning(
            "recovered %d interrupted guardian runs", recovered_guardian_runs
        )
    # 在接受回测请求前固定 managed generation，避免首批并发 worker 各自创建版本。
    if settings.backtest_matrix_disk_cache_enabled:
        try:
            with startup_phase(logger, "enriched generation initialization"):
                repo.get_matrix_data_generation("stock")
        except EnrichedGenerationUnavailableError as exc:
            logger.warning("enriched generation requires a full rebuild: %s", exc)
    # 指标异步预热标志: enriched 缓存在后台线程构建, 完成后置 True
    app.state.indicators_ready = False
    repo._on_warmup_done = lambda: setattr(app.state, "indicators_ready", True)  # noqa: SLF001

    # Polars 缓存预热 — enriched 的重计算 (107万行 compute_indicators) 推后台,
    # instruments/index/ETF 仍同步 (毫秒级)。应用立即 ready, 指标算完后自动替换。
    repo.refresh_cache(background=True)

    # 自定义数据源配置(可选): 失败只记录错误, 不影响 TickFlow 基准路径。
    try:
        from app.data_providers import custom as custom_sources
        custom_sources.load_all()
        logger.info("custom data sources loaded: %d", len(custom_sources.list_sources()))
    except Exception as e:  # noqa: BLE001
        logger.warning("custom data sources init failed: %s", e)

    # 自定义源必须先注册,能力探测才能补充其数据集能力。
    capset = detect_capabilities()
    app.state.capabilities = capset
    logger.info("ready; %d capabilities active", len(capset.all()))
    # 各能力的生效源与可用性: 切换数据源后一眼看出路由落点, 无需回设置页对照
    try:
        from app.data_providers.capabilities import resolve_capability_sources

        for row in resolve_capability_sources():
            logger.info(
                "  能力路由 | %-8s ← %-12s %s",
                row["label"],
                row["source_display"],
                "可用" if row["usable"] else "不可用",
            )
    except Exception as e:  # noqa: BLE001 — 仅日志, 失败不影响启动
        logger.warning("能力路由日志输出失败: %s", e)

    # 发布完整冻结快照后再启动行情与监控线程，避免首批事件使用未完成的注册表。
    from app.extensions.packages import start_managed_extensions
    start_managed_extensions(
        app, current_extension_context(data_dir=store.data_dir, repository=repo),
    )

    # 二次开发升级保护: 后台常备备份用户修改的程序文件 (无清单时自动停用)
    from app.services.upgrade_guard import UpgradeGuard

    upgrade_guard = UpgradeGuard(store.data_dir)
    app.state.upgrade_guard = upgrade_guard
    upgrade_guard.start_background_scan()

    # 全局行情服务
    qs = QuoteService()
    app.state.quote_service = qs
    qs.set_repo(repo)
    qs.boot_check()

    # QuoteService 需要访问 strategy_monitor 等单例
    # 先创建 strategy_monitor，再注入 app.state
    from app.strategy.monitor import StrategyMonitorService
    strategy_monitor = StrategyMonitorService()
    app.state.strategy_monitor = strategy_monitor
    qs.set_app_state(app.state)

    # 五档盘口 sealed 服务(真假涨停/跌停, 独立旁路线)
    from app.services.depth_service import DepthService
    depth_service = DepthService()
    depth_service.set_repo(repo)
    depth_service.set_app_state(app.state)
    app.state.depth_service = depth_service

    # 启动调度器(若 enriched 数据为空,首次启动可手动 POST /api/pipeline/run)
    try:
        daily_pipeline.set_app_state(app.state)  # 供 depth_finalize job 访问 depth_service
        scheduler = daily_pipeline.start_scheduler(repo, capset)
        app.state.scheduler = scheduler
    except Exception as e:  # noqa: BLE001
        logger.warning("scheduler not started: %s", e)
        app.state.scheduler = None

    # depth sealed: 启动补跑(当天文件不存在) + 盘中轮询(有能力时)
    try:
        depth_service.boot_check()
        depth_service.start_polling()
    except Exception as e:  # noqa: BLE001
        logger.warning("depth_service init failed: %s", e)

    # 盘中分钟增量刷新 (Expert 专有): 线程常驻, 开关/时段/能力门控在循环内每轮判断
    try:
        from app.services.minute_refresh import MinuteRefreshService
        minute_refresh = MinuteRefreshService(repo)
        minute_refresh.set_app_state(app.state)
        app.state.minute_refresh = minute_refresh
        minute_refresh.start()
    except Exception as e:
        logger.warning("minute_refresh init failed: %s", e)

    # 停机缺口自检: 延迟后台扫描, 发现最近交易日的盘中快照/缺口时自动创建
    # 修复任务 (盘中停机→次日开实时场景, 不修则坏数据被"只刷今天"分支永久留存)
    try:
        import threading

        from app.services.data_integrity import boot_integrity_check
        from app.services.daily_catchup import launch_daily_catchup

        def _boot_checks() -> None:
            boot_integrity_check(app.state)
            launch_daily_catchup(app.state, allow_late=True)

        timer = threading.Timer(30.0, _boot_checks)
        timer.daemon = True  # 不阻塞进程退出
        timer.start()
    except Exception as e:  # noqa: BLE001
        logger.warning("integrity boot check scheduling failed: %s", e)

    # 启动自愈: 上次进程异常退出可能残留 enriched 发布标记 (state=publishing),
    # 会让所有读写持续报"数据正在发布", 且停止实时行情后没有写入方来接管。
    # 本进程刚启动、不可能持有活跃发布, 因此可直接判定残留并在锁内恢复。
    try:
        from app.enriched_generation import recover_stale_publications

        recovered_publications = recover_stale_publications(store.data_dir)
        if recovered_publications:
            logger.warning(
                "已恢复上次中断的 enriched 发布标记: %s",
                ", ".join(recovered_publications),
            )
    except Exception as e:  # noqa: BLE001
        logger.warning("enriched 发布标记自愈失败 (不影响启动): %s", e)

    # 企业微信智能机器人长连接(可选通道, 失败不阻断启动)
    try:
        from app.services.wecom_bot_service import WecomBotService
        wecom_bot_service = WecomBotService()
        wecom_bot_service.set_app_state(app.state)
        app.state.wecom_bot_service = wecom_bot_service
        wecom_bot_service.boot_check()
    except Exception as e:  # noqa: BLE001
        logger.warning("wecom_bot_service init failed: %s", e)

    # 内置扩展表 (概念/行业): 先创建 config (含拉取配置), 默认开启定时拉取。
    # 必须在 pull_scheduler.refresh() 之前执行, 否则全新部署时 scheduler 读不到
    # 刚创建的预设, 定时任务不会启动。
    try:
        from app.services.ext_presets import ensure_builtin_presets
        await ensure_builtin_presets(store.data_dir)
    except Exception as e:  # noqa: BLE001
        logger.warning("内置扩展表初始化失败 (不影响启动): %s", e)

    # 扩展数据定时拉取: 在预设配置就绪后启动, 自动调度 enabled 的预设。
    from app.services.ext_pull import pull_scheduler
    pull_scheduler.start(store.data_dir)
    pull_scheduler.refresh(store.data_dir)
    app.state.pull_scheduler = pull_scheduler

    # 财务数据 (需 Expert 套餐): 仅初始化调度器供 /api/financials/sync/* 手动同步,
    # 不启动自动调度——用户在「财务分析」页点「同步」手动拉取。
    from app.services.financial_sync import financial_scheduler
    financial_scheduler.start(store.data_dir, capset)
    app.state.financial_scheduler = financial_scheduler

    # 自愈看门狗: 探测 polars 闸与写锁, 僵死时退出交由 supervisor 拉起 (兜底层)。
    from app.watchdog import start_watchdog
    app.state.watchdog = start_watchdog(app.state, repo)

    # 系统级运维巡检: 磁盘水位、日志落盘、调度器心跳、数据完整性。
    # 独立后台线程, 即使 API 层卡住也能发现并告警。
    try:
        from app.services.ops_monitor import start_ops_monitor

        app.state.ops_monitor = start_ops_monitor(app.state)
    except Exception as e:  # noqa: BLE001
        logger.warning("ops monitor not started: %s", e)
        app.state.ops_monitor = None

    # 策略引擎
    from app.services.screener import ScreenerService
    from app.strategy import config as strategy_config
    from app.strategy.engine import StrategyEngine
    from app.strategy.monitor import StrategyMonitorService

    _screener_svc = ScreenerService(repo)
    _etf_screener_svc = ScreenerService(repo, asset_type="etf")
    # 去内置化补全(仅升级用户): 原内置策略按 ID 补回 custom (一次性,
    # 已自行迁移的不覆盖; 新装无旧版使用痕迹, 跳过保持策略库为空)。
    # 必须在 StrategyEngine 创建前完成, 否则首载拿不到刚补回的文件。
    _seeded_strategies = 0
    try:
        from app.strategy.strategy_seeds import seed_missing_builtins

        _seeded_strategies = seed_missing_builtins(store.data_dir)
    except Exception as e:  # noqa: BLE001
        logger.warning("strategy seeding failed (ignored): %s", e)
    strategy_dirs = [
        Path(__file__).resolve().parent / "strategy" / "research",
        store.data_dir / "strategies" / "custom",
        store.data_dir / "strategies" / "ai",
        store.data_dir / "strategies" / "composite",
    ]
    strategy_engine = StrategyEngine(
        strategy_dirs=strategy_dirs,
        override_loader=lambda sid: strategy_config.load_override(store.data_dir, sid),
    )
    # 去内置化一次性迁移: ①信号目录缺种子时补入 20 个默认定义;
    # ②内置残留类加载失败 (composite 断链 / import 已删模块) 的策略文件
    #   自动归档出扫描目录并重载。摘要挂到 app.state 供前端提示一次。
    legacy_migration: dict = {}
    try:
        from app.strategy.legacy_migrate import archive_unloadable_strategies

        if _seeded_signals:
            legacy_migration["seeded_signals"] = _seeded_signals
            logger.info("legacy migrate: seeded %d default signals", _seeded_signals)
        if _seeded_strategies:
            legacy_migration["seeded_strategies"] = _seeded_strategies
            logger.info("legacy migrate: seeded %d builtin strategies", _seeded_strategies)
        archived = archive_unloadable_strategies(strategy_engine.load_errors())
        if archived:
            strategy_engine.reload()
            legacy_migration["archived_files"] = archived
            logger.info("legacy migrate: archived %d unloadable files", len(archived))
        if legacy_migration:
            app.state.legacy_migration = legacy_migration
    except Exception as e:  # noqa: BLE001
        logger.warning("legacy migrate failed (ignored): %s", e)
    app.state.strategy_engine = strategy_engine
    logger.info("strategy engine loaded: %d strategies", len(strategy_engine.list_strategies()))

    matrix_prewarm_owner = MatrixCachePrewarmOwner()

    def _schedule_matrix_cache_prewarm() -> None:
        if (
            not settings.backtest_matrix_disk_cache_enabled
            or not settings.backtest_matrix_cache_prewarm
        ):
            return

        def _prewarm() -> None:
            from app.backtest.engine import BacktestEngine
            from app.backtest.matrix import MatrixPrewarmCancelledError
            from app.backtest.strategy import prewarm_matrix_cache
            from app.services.heavy_job_limiter import (
                HeavyJobCancelledError,
                shared_heavy_job_limiter,
            )

            try:
                latest = repo.latest_enriched_date("stock")
                if latest is None:
                    logger.info("matrix cache prewarm skipped: no stock enriched data")
                    return

                with shared_heavy_job_limiter.slot(
                    "exclusive",
                    cancel_event=matrix_prewarm_owner.cancel_event,
                ):
                    result = prewarm_matrix_cache(
                        BacktestEngine(repo),
                        strategy_engine,
                        asset_type="stock",
                        latest_date=latest,
                        years=settings.backtest_matrix_cache_prewarm_years,
                        cancel_event=matrix_prewarm_owner.cancel_event,
                    )
                logger.info("matrix cache prewarm done: %s", result)
            except (HeavyJobCancelledError, MatrixPrewarmCancelledError):
                logger.info("matrix cache prewarm cancelled")
            except Exception:  # noqa: BLE001
                logger.exception("matrix cache prewarm failed")

        if not matrix_prewarm_owner.schedule(_prewarm):
            logger.info("matrix cache prewarm already running or shutting down, skip")

    repo._on_refresh_done = _schedule_matrix_cache_prewarm  # noqa: SLF001
    if repo.enriched_ready:
        _schedule_matrix_cache_prewarm()

    # 通用监控规则引擎: 启动时 reload 规则到内存态 (修复重启后告警失效)
    from app.services import preferences
    from app.services.sector_monitor import SectorMonitorService
    from app.strategy import monitor_rules as mr_store
    from app.strategy.monitor import MonitorRuleEngine
    monitor_engine = MonitorRuleEngine()
    sector_monitor_service = SectorMonitorService(repo)
    monitor_engine.set_strategy_engine(strategy_engine)
    monitor_engine.set_data_dir(store.data_dir)
    monitor_engine.set_sector_monitor_service(sector_monitor_service)
    # 复用 ScreenerService 的历史窗口加载器 (三级缓存, 启动预计算命中 ~0ms),
    # 让声明 filter_history 的策略 (如反包) 也能在实时监控里跑策略 → 盘中触发通知。
    monitor_engine.set_history_loader(_screener_svc._load_enriched_history)
    # ETF 版历史加载器: asset_type=etf 的 strategy 型规则用 (读 kline_etf_enriched)。
    monitor_engine.set_history_loader_etf(_etf_screener_svc._load_enriched_history)

    # 自动迁移: 把旧 strategy_monitor_ids 同步为 type=strategy 规则 (统一到监控页)
    try:
        if preferences.get_strategy_monitor_enabled():
            ids = preferences.get_strategy_monitor_ids()
            if ids:
                names = {s["id"]: s["name"] for s in strategy_engine.list_strategies()}
                mr_store.migrate_strategy_monitors(store.data_dir, ids, names)
                logger.info("strategy monitor migrated: %d strategies", len(ids))
    except Exception as e:  # noqa: BLE001
        logger.warning("strategy monitor migration failed: %s", e)

    try:
        rules = mr_store.load_owned(store.data_dir)
        monitor_engine.set_rules(rules)
        logger.info("monitor engine loaded: %d rules", monitor_engine.rule_count)
    except Exception as e:  # noqa: BLE001
        logger.warning("monitor engine load failed: %s", e)
    # 模拟盘「绑定即评估」: 把账户绑定但无监控规则的策略注入评估循环
    # (set_rules 全量覆盖, 必须在其后注入; 失败不阻断启动)。
    try:
        from app.strategy import paper_watch

        synced = paper_watch.sync(store.data_dir, monitor_engine, strategy_engine)
        logger.info("paper watch synced at startup: %d strategies", len(synced["watching"]))
    except Exception as e:  # noqa: BLE001
        logger.warning("paper watch sync failed (ignored): %s", e)
    app.state.monitor_engine = monitor_engine
    app.state.sector_monitor_service = sector_monitor_service

    # 源码内二次开发启动钩子: 仅暴露稳定只读上下文, 单个扩展失败不影响核心启动。
    extension_registry = app.state.extension_registry
    start_backend_extensions(
        current_extension_context(data_dir=store.data_dir, repository=repo),
        extension_registry,
    )
    try:
        yield
    finally:
        repo._on_refresh_done = None  # noqa: SLF001
        wd = getattr(app.state, "watchdog", None)
        if wd:
            await wd.stop()
        ops = getattr(app.state, "ops_monitor", None)
        if ops:
            ops.stop()
        system_alert_mod = sys.modules.get("app.services.system_alert")
        if system_alert_mod is not None:
            system_alert_mod.shutdown()
        if not matrix_prewarm_owner.shutdown(timeout=5.0):
            logger.warning("matrix cache prewarm did not stop within 5 seconds")
        mmanager = getattr(app.state, "mining_manager", None)
        if mmanager:
            mmanager.shutdown()
        gmanager = getattr(app.state, "guardian_manager", None)
        if gmanager:
            gmanager.shutdown()
        if app.state.scheduler:
            app.state.scheduler.shutdown(wait=False)
        ps = getattr(app.state, "pull_scheduler", None)
        if ps:
            ps.stop()
        fsc = getattr(app.state, "financial_scheduler", None)
        if fsc:
            fsc.stop()
        qs = getattr(app.state, "quote_service", None)
        if qs:
            qs.stop()
        dsvc = getattr(app.state, "depth_service", None)
        if dsvc:
            dsvc.stop_polling()
        wbot = getattr(app.state, "wecom_bot_service", None)
        if wbot:
            wbot.stop()
        mrs = getattr(app.state, "minute_refresh", None)
        if mrs:
            mrs.stop()
        # webhook/系统通知投递线程池: 放弃排队投递并关闭, 避免通知静默丢失在 daemon 线程里
        from app.services.quote_service import shutdown_delivery_executors

        shutdown_delivery_executors()
        logger.info("shutdown")


@asynccontextmanager
async def lifespan(app: FastAPI):
    mining_process_lock = MiningProcessLock(settings.data_dir)
    lock_acquired = True
    try:
        mining_process_lock.acquire()
    except MiningProcessLockError:
        # 撞锁不再让整个后端起不来 (典型: 旧实例挂死在 shutdown 持锁不放):
        # 面板以降级模式启动 — 挖掘启动入口返回 409, 用户可在挖掘页查看
        # 持有者并 (满足陈旧判定时) 强制释放, 之后自动恢复。
        lock_acquired = False
        logger.warning(
            "挖掘锁被其他进程占用, 面板以降级模式启动 (挖掘功能暂不可用; "
            "可在挖掘页查看锁状态并强制释放)"
        )
    app.state.mining_process_lock = mining_process_lock
    app.state.mining_lock_acquired = lock_acquired
    try:
        async with _application_lifespan(app):
            yield
    finally:
        # 用 lock.acquired 而非启动时的 flag: 强制释放接管后同样要在退出时释放
        if mining_process_lock.acquired:
            mining_process_lock.release()


app = FastAPI(
    title="Tick Stock Panel",
    version=__version__,
    description="A 股策略 + 回测面板 — TickFlow 适配",
    lifespan=lifespan,
)

@app.exception_handler(WorkBusyError)
@app.exception_handler(HeavyJobLimitTimeoutError)
async def shared_work_busy_handler(request: Request, exc: WorkBusyError) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": str(exc), "code": "work_busy"},
                        headers={"Retry-After": "3"})


# CORS: 允许局域网访问 (自托管场景, 放开所有来源)
# 注: allow_credentials=True 与 allow_origins=['*'] 不能共存 (浏览器规范),
# 本项目认证走 header (API Key), 不依赖 cookie, 故关闭 credentials 换取通配来源。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)


# ================================================================
# 访问认证中间件
# ================================================================
# 拦截所有 /api/ 请求, 账号体系是唯一认证通道:
#   1. 尚无邮箱账号 + 本机/内网 → 放行(让本机用户注册首个管理员)
#   2. 尚无邮箱账号 + 公网      → 拒绝(403, 防陌生人抢占首个管理员)
#   3. 已有账号                → 校验邮箱账号会话, 无效则 401(前端跳登录)
# 白名单: /api/accounts/* (注册/登录/状态本身)、/health 等探活。
_AUTH_WHITELIST_PREFIX = ("/api/accounts/",)
_AUTH_WHITELIST_EXACT = (
    "/health",
    "/ready",
    "/api/health",
    "/openapi.json",
    "/api/openapi.json",
    "/docs",
    "/redoc",
    # SSE 事件流: EventSource 带不了 Authorization 头, 凭证即 query 里的
    # 一次性票据, 端点内校验 (api/events.py); POST /api/events/ticket 不在
    # 白名单, 仍走网关 Bearer 通道
    "/api/events",
)


@app.middleware("http")
async def workload_middleware(request: Request, call_next):
    import time

    from app.services.workload import workload

    group = next((name for name in ("screener", "kline", "overview", "watchlist", "backtest")
                  if request.url.path == f"/api/{name}" or request.url.path.startswith(f"/api/{name}/")), None)
    if group is None:
        return await call_next(request)
    started = time.perf_counter()
    try:
        return await call_next(request)
    finally:
        workload.record(f"http.{group}", "request", elapsed_s=time.perf_counter() - started)


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    # 仅 /api/ 走认证; 静态资源(前端页面/assets)放行, 由前端处理跳转
    if not path.startswith("/api/"):
        return await call_next(request)
    # CORS 预检不带凭据, 直接放行 (CORSMiddleware 在外层应答)
    if request.method == "OPTIONS":
        return await call_next(request)
    # 白名单放行(注册/登录/探活本身不拦)
    if path.startswith(_AUTH_WHITELIST_PREFIX) or path in _AUTH_WHITELIST_EXACT:
        return await call_next(request)

    # ── API Token 通道 (外部调用方; 与邮箱账号会话并行, 见 open-platform-plan §4) ──
    authz = request.headers.get("authorization", "")
    if authz.startswith("Bearer "):
        from app.services import api_gateway
        # 网络门禁先行: 默认仅本机/内网可调 Token 通道 (API_TOKEN_LOCAL_ONLY=0 放开);
        # 先于 evaluate 拦截 → 不消耗限流额度, 公网探测者也拿不到 Token 有效性信号
        gate = api_gateway.token_network_verdict(accounts_api._client_ip(request))
        if gate is not None:
            return JSONResponse(
                status_code=gate["status"], content={"detail": gate["detail"]},
            )
        verdict = api_gateway.evaluate(
            settings.data_dir, request.method, path, authz[len("Bearer "):].strip(),
        )
        if verdict["status"] is not None:
            return JSONResponse(
                status_code=verdict["status"], content={"detail": verdict["detail"]},
                headers=verdict["headers"],
            )
        response = await call_next(request)
        for k, v in verdict["headers"].items():
            response.headers[k] = v
        return response

    from app.services import user_store as user_store_service
    # 情况 1+2: 没有任何邮箱账号 (系统未初始化)
    if not user_store_service.has_users():
        # 本机/内网 → 放行, 由 /login 注册首个管理员
        if accounts_api._is_local_network(accounts_api._client_ip(request)):
            return await call_next(request)
        # 公网 → 拒绝。不裸奔, 也不给公网初始化的机会(防抢占)
        return JSONResponse(
            status_code=403,
            content={
                "detail": "面板尚未初始化,请通过 SSH/本机浏览器访问以注册管理员",
                "code": "NOT_INITIALIZED",
            },
        )

    # 情况 3: 已初始化, 只认邮箱账号会话
    user_token = request.cookies.get(accounts_api.COOKIE_NAME)
    if user_token:
        current_user = user_store_service.get_user_by_token(user_token)
        if current_user is not None:
            request.state.user = current_user
            # 注入租户上下文: 该请求的私有数据(自选/告警/AI报告/会话)落盘到用户专属目录
            from app.services import tenant as tenant_service
            tenant_service.set_current_user(current_user["id"])
            return await call_next(request)
    # 未登录: 401(前端跳登录页)
    return JSONResponse(status_code=401, content={"detail": "未登录或会话已过期"})


# ================================================================
# 请求级可观测性
# ================================================================
# 该中间件最后注册 => 位于认证/业务中间件最外层: request_id 在任何handler
# 之前写入 request.state, 异常兜底和访问日志都能拿到同一编号。
_REQUEST_ID_SAFE = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")


def _new_request_id(raw: str | None) -> str:
    """只接受长度/字符集合理的上游 request id, 防止日志注入。"""
    value = (raw or "").strip()
    if 8 <= len(value) <= 64 and all(ch in _REQUEST_ID_SAFE for ch in value):
        return value
    return uuid.uuid4().hex[:16]


@app.middleware("http")
async def request_context_middleware(request: Request, call_next):
    request_id = _new_request_id(request.headers.get("x-request-id"))
    request.state.request_id = request_id
    started = time.perf_counter()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
    except Exception:
        logger.error(
            "unhandled request exception | request_id=%s method=%s path=%s",
            request_id, request.method, request.url.path,
            exc_info=True,
        )
        try:
            from app.services.system_alert import record_http_status

            record_http_status(500)
        except Exception:  # noqa: BLE001 — 可观测性不得反噬主流程
            logger.debug("system_alert 5xx 计数失败", exc_info=True)
        response = JSONResponse(
            status_code=500,
            content={
                "detail": "服务器内部错误",
                "code": "internal_error",
                "request_id": request_id,
            },
            headers={"X-Request-ID": request_id},
        )
    finally:
        elapsed_ms = (time.perf_counter() - started) * 1000.0

    response.headers["X-Request-ID"] = request_id
    path = request.url.path
    if path.startswith("/api/"):
        user = getattr(request.state, "user", None) or {}
        user_id = user.get("id") if isinstance(user, dict) else None
        level = logging.ERROR if status_code >= 500 else logging.INFO
        # 不记录 query, 防止 token/webhook 等 query 凭证落盘; 路径足够定位。
        logger.log(
            level,
            "http request | request_id=%s method=%s path=%s status=%s duration_ms=%.1f user_id=%s",
            request_id, request.method, path, status_code, elapsed_ms,
            user_id if user_id is not None else "-",
        )
        if elapsed_ms >= 2000.0:
            logger.warning(
                "slow request | request_id=%s method=%s path=%s status=%s duration_ms=%.0f",
                request_id, request.method, path, status_code, elapsed_ms,
            )
        if status_code >= 500:
            try:
                from app.services.system_alert import record_http_status

                record_http_status(status_code)
            except Exception:  # noqa: BLE001
                logger.debug("system_alert 5xx 计数失败", exc_info=True)
    return response


# 路由
# 业务路由按「哪些页面会读它」挂页面权限依赖（anyOf），页面权限不再只是前端隐藏。
# 认证/账号/管理后台/个人设置/全局轮询（alerts/events/task-registry）不挂页面依赖。
def _page_deps(name: str) -> list:
    return [
        Depends(permissions_api.require_page_access(*page_access.PAGE_ACCESS[name]))
    ]


app.include_router(core_router)
app.include_router(accounts_api.router)
app.include_router(admin_users_api.router)
app.include_router(navigation_api.router)
app.include_router(system_api.router)
app.include_router(kline.router, dependencies=_page_deps("kline"))
app.include_router(watchlist.router, dependencies=_page_deps("watchlist"))
app.include_router(screener.router, dependencies=_page_deps("screener"))
app.include_router(backtest.router, dependencies=_page_deps("backtest"))
app.include_router(factors.router, dependencies=_page_deps("factors"))
app.include_router(definitions.router, dependencies=_page_deps("definitions"))
app.include_router(mining.router, dependencies=_page_deps("mining"))
app.include_router(guardian.router, dependencies=_page_deps("guardian"))
app.include_router(lab.router, dependencies=_page_deps("lab"))
app.include_router(intraday.router, dependencies=_page_deps("intraday"))
app.include_router(indices.router, dependencies=_page_deps("indices"))
app.include_router(overview.router, dependencies=_page_deps("overview"))
app.include_router(pages_admin.router)
app.include_router(paper.router, dependencies=_page_deps("paper"))
app.include_router(abnormal.router, dependencies=_page_deps("abnormal"))
app.include_router(regime.router, dependencies=_page_deps("regime"))
app.include_router(analysis.router)
app.include_router(pipeline.router, dependencies=_page_deps("pipeline"))
app.include_router(position_policies.router, dependencies=_page_deps("position_policies"))
app.include_router(data.router, dependencies=_page_deps("data"))
app.include_router(ext_data.router, dependencies=_page_deps("ext_data"))
app.include_router(ext_pages.router)
app.include_router(task_registry.router)
app.include_router(financials.router, dependencies=_page_deps("financials"))
app.include_router(stock_analysis.router, dependencies=_page_deps("stock_analysis"))
app.include_router(stock_growth.router, dependencies=_page_deps("stock_growth"))
app.include_router(aistock_proxy.router)
app.include_router(market_recap.router, dependencies=_page_deps("market_recap"))
app.include_router(settings_api.router)
app.include_router(upgrade_guard_api.router)
app.include_router(strategy.router, dependencies=_page_deps("strategy"))
app.include_router(strategy_pool.router, dependencies=_page_deps("strategy_pool"))
app.include_router(signals.router, dependencies=_page_deps("signals"))
app.include_router(monitor_rules.router, dependencies=_page_deps("monitor_rules"))
app.include_router(config_backup.router)
app.include_router(import_center.router)
app.include_router(lots.router, dependencies=_page_deps("lots"))
app.include_router(alerts.router)
app.include_router(events.router)
app.include_router(rps.router, dependencies=_page_deps("rps"))
app.include_router(sector_rotation.router, dependencies=_page_deps("sector_rotation"))

# 二次开发路由与小粒度策略在所有核心路由后注册, 禁止覆盖核心路径。
extension_registry, extension_load_errors = configure_backend_extensions(app)
app.state.extension_registry = extension_registry
app.state.extension_load_errors = extension_load_errors


# 能力门控异常 → 403(而非默认 500)
# 业务代码用 capset.require(Cap.X) 断言能力,缺失时抛 CapabilityDenied;
# 若不注册 handler 会冒泡成 500 Internal Server Error,对前端不友好且语义错误。
from fastapi import Request
from fastapi.responses import JSONResponse

from app.tickflow.capabilities import CapabilityDenied


@app.get("/api/openapi.json", include_in_schema=False)
async def openapi_contract_view(tier: str = "a"):
    """Tier A 契约视图: 只保留对外开放 (Token 可达) 的端点 = 稳定承诺面。

    开放清单的权威来源是 api_gateway 的规则表 — 规则表即契约, 单源维护。
    二开方以此生成客户端; 未出现在此视图的端点属内部实现, 随时变化。
    """
    spec = app.openapi()
    if tier == "a":
        from app.services import api_gateway

        kept_paths: dict = {}
        for path, ops in spec.get("paths", {}).items():
            kept_ops = {}
            for method, op in ops.items():
                if method in ("get", "post", "put", "delete", "patch") and api_gateway.required_scope(
                    method.upper(), path,
                ):
                    kept_ops[method] = op
            if kept_ops:
                kept_paths[path] = kept_ops
        spec["paths"] = kept_paths
        spec["x-tier"] = "a"
    return JSONResponse(spec)


@app.exception_handler(CapabilityDenied)
async def capability_denied_handler(request: Request, exc: CapabilityDenied) -> JSONResponse:
    return JSONResponse(
        status_code=403,
        content={"detail": str(exc), "suggestion": exc.suggestion},
    )


@app.exception_handler(DailyIntegrityError)
async def daily_integrity_error_handler(request: Request, exc: DailyIntegrityError) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(exc), "code": "daily_integrity"})


@app.exception_handler(ProviderRequestError)
async def provider_request_error_handler(request: Request, exc: ProviderRequestError) -> JSONResponse:
    return JSONResponse(status_code=502, content={"detail": str(exc), "code": "provider_request"})


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """全局未捕获异常兜底: 记录完整堆栈 + 请求上下文, 返回可追踪的错误编号。

    业务异常 (ProviderRequestError / CapabilityDenied / DailyIntegrityError 等)
    有各自的语义 handler, 不会进入这里; 这里只处理真正未预期的异常。
    """
    request_id = getattr(request.state, "request_id", None) or _new_request_id(None)
    logger.error(
        "unhandled exception | request_id=%s method=%s path=%s",
        request_id, request.method, request.url.path,
        exc_info=(type(exc), exc, exc.__traceback__),
    )
    try:
        from app.services.system_alert import record_http_status

        record_http_status(500)
    except Exception:  # noqa: BLE001
        logger.debug("system_alert 5xx 计数失败", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "detail": "服务器内部错误",
            "code": "internal_error",
            "request_id": request_id,
        },
        headers={"X-Request-ID": request_id},
    )


# 生产期静态文件(前端 dist)
def _register_static_routes(target: FastAPI, static_dir: Path) -> None:
    """挂载前端构建产物: /assets 走静态服务, 根级文件直出, 其余回退 SPA。

    根级静态文件 (favicon.svg / donate-wechat.jpg 等) 必须原样返回。此前所有
    未匹配路径都回退 index.html, 浏览器把 HTML 当图标解析失败, Docker 与桌面
    安装包的浏览器页签因此没有图标。
    """
    # 旧客户端可能缓存了 text/plain 的 JS/CSS。使用版本化资源地址，连带
    # 相对路径动态 import 的分块一起绕过旧缓存；保留 /assets 兼容旧链接。
    asset_base = f"/_static/{__version__}/assets"
    if (static_dir / "assets").exists():
        assets = FrontendStaticFiles(directory=static_dir / "assets")
        target.mount("/assets", assets, name="assets")
        target.mount(asset_base, assets, name="versioned-assets")

    # 旧地址在服务端直接跳到独立管理后台, 不必等前端 JS 再重定向。
    # /settings 曾是多用户改造前的「设置」页, 现统一为 /admin。
    @target.get("/settings", include_in_schema=False)
    def legacy_settings_root(request: Request):
        query = request.url.query
        return RedirectResponse(f"/admin{('?' + query) if query else ''}", status_code=307)

    @target.get("/settings/keys", include_in_schema=False)
    def legacy_settings_keys():
        return RedirectResponse("/admin?tab=data-sources", status_code=307)

    @target.get("/settings/ai", include_in_schema=False)
    def legacy_settings_ai():
        return RedirectResponse("/admin?tab=ai", status_code=307)

    @target.get("/settings/queries", include_in_schema=False)
    def legacy_settings_queries():
        return RedirectResponse("/admin?tab=queries", status_code=307)

    @target.get("/settings/{rest:path}", include_in_schema=False)
    def legacy_settings_other(request: Request):
        query = request.url.query
        return RedirectResponse(f"/admin{('?' + query) if query else ''}", status_code=307)

    @target.get("/data", include_in_schema=False)
    def legacy_data():
        return RedirectResponse("/admin/data", status_code=307)

    @target.get("/branding", include_in_schema=False)
    def legacy_branding():
        return RedirectResponse("/admin/branding", status_code=307)

    def _index_response():
        index = static_dir / "index.html"
        if index.exists():
            markup = index.read_text(encoding="utf-8")
            for q in ('"', "'"):
                markup = markup.replace(f"{q}/assets/", f"{q}{asset_base}/")
            return HTMLResponse(
                markup,
                headers={"Cache-Control": "no-store, must-revalidate"},
            )
        return {"error": "frontend not built"}

    def _admin_session_state(request: Request) -> tuple[bool, bool]:
        """返回 (已登录, 是否有后台权限)。管理后台 HTML 也要先验会话。"""
        from app.services import rbac as rbac_service
        from app.services import user_store as user_store_service

        user_token = request.cookies.get(accounts_api.COOKIE_NAME)
        if user_token:
            current = user_store_service.get_user_by_token(user_token)
            if current is not None:
                can_admin = rbac_service.ADMIN_ACCESS in user_store_service.user_permissions(current)
                return True, can_admin
        # 未初始化也一律先去 /login: 首个注册账号自动成为超级管理员,
        # 后台不向任何未登录请求直接返回页面。
        return False, False

    @target.get("/admin", include_in_schema=False)
    @target.get("/admin/{rest:path}", include_in_schema=False)
    def admin_spa(request: Request, rest: str = ""):
        authenticated, is_admin = _admin_session_state(request)
        if not authenticated:
            target_url = request.url.path + (("?" + request.url.query) if request.url.query else "")
            return RedirectResponse("/login?redirect=" + quote(target_url), status_code=307)
        if not is_admin:
            return RedirectResponse("/", status_code=307)
        return _index_response()

    @target.get("/{full_path:path}", include_in_schema=False)
    def spa_fallback(full_path: str):
        # 只直出 dist 根目录文件 (单段路径); index.html 仍走下方 no-store 分支。
        if full_path and "/" not in full_path and full_path not in (".", ".."):
            asset = static_dir / full_path
            if full_path != "index.html" and asset.is_file():
                return frontend_file_response(asset)
        return _index_response()


_static = Path(settings.static_dir)
if _static.exists():
    _register_static_routes(app, _static)
