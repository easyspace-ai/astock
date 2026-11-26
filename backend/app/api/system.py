"""系统健康 API — 管理页排障入口。"""
from __future__ import annotations

from fastapi import APIRouter, Request

from app.api.permissions import require_admin

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/health")
def system_health(request: Request) -> dict:
    """readiness + 磁盘/日志/调度器/数据源/完整性 的统一快照。"""
    require_admin(request)
    from app.services.system_health import system_status

    return system_status(getattr(request.app, "state", None))
