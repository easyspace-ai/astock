"""巨潮公司图谱扩展 — 公司档案/董监高/前五大股东/公告的采集与查询 API。

背景: 功能池「上市公司信息图谱」的数据层第一块。巨潮资讯网是交易所法定
信披平台, 接口无需登录(2026-11 实测)。其数据不属于行情数据集契约
(daily/realtime/...), 不走 data provider datasets 路由; 依据
docs/secondary-development.md §4 以 backend 扩展注册接入, 不修改核心源码。

数据落盘: <DATA_DIR>/graph/ (契约见 store.py 模块注释)。
"""

from app.extensions import BACKEND_EXTENSION_API_VERSION, BackendExtensionRegistrar

EXTENSION_ID = "company.cninfo-graph"
EXTENSION_API_VERSION = BACKEND_EXTENSION_API_VERSION


def setup(registrar: BackendExtensionRegistrar) -> None:
    from .routes import build_router

    registrar.include_router(build_router())
