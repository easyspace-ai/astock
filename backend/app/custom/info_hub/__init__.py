"""TSP 资讯中心扩展入口。

后端扩展契约：EXTENSION_ID / EXTENSION_API_VERSION / setup(registrar)。
路由统一挂在 /api/info，数据写入 <DATA_DIR>/info_hub。
"""
from __future__ import annotations

import logging
from typing import Any

from .routes import router

EXTENSION_ID = "info.center"
EXTENSION_API_VERSION = 1
logger = logging.getLogger(__name__)


def setup(registrar: Any) -> None:
    registrar.include_router(router)
    logger.info("info_hub extension registered")


def startup(context: Any) -> None:
    """扩展启动：只做轻量初始化；定时抓取由 scheduler 模块按配置启动。"""
    try:
        from . import config

        data_dir = getattr(context, "data_dir", None)
        if data_dir:
            config.ensure_layout(data_dir)
            from . import scheduler

            scheduler.start(data_dir)
    except Exception:
        logger.exception("info_hub startup failed")
