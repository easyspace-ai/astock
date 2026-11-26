"""公司图谱 API — /api/graph。

薄层: 参数校验 + 调 service/store, 不放网络请求与重计算。
data_dir 取自 request.app.state.repo.store.data_dir (与 paper_execution 同模式)。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.api.permissions import require_page_access
from app.services import rbac

from . import service
from .client import CninfoClient
from .em_client import EastmoneyClient


class TrackedSymbolsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbols: list[str] = Field(default_factory=list, max_length=200)


def build_router() -> APIRouter:
    router = APIRouter(
        prefix="/api/graph",
        tags=["company-graph"],
        dependencies=[Depends(require_page_access(rbac.PAGE_EXT_COMPANY_GRAPH))],
    )

    def _data_dir(request: Request):
        return request.app.state.repo.store.data_dir

    @router.get("")
    def overview(request: Request) -> dict:
        return service.overview(_data_dir(request))

    @router.put("")
    def set_tracked(request: Request, req: TrackedSymbolsRequest) -> dict:
        from . import store

        data_dir = _data_dir(request)
        symbols = []
        try:
            for raw in req.symbols:
                symbol = service.normalize_symbol(raw)
                if symbol not in symbols:
                    symbols.append(symbol)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        store.save_tracked(data_dir, symbols)
        return {"ok": True, "tracked": symbols}

    @router.post("/{symbol}/sync")
    def sync_company(
        request: Request, symbol: str, with_announcements: bool = True, with_news: bool = True
    ) -> dict:
        try:
            return service.sync_company(
                _data_dir(request),
                symbol,
                client=CninfoClient(),
                em_client=EastmoneyClient(),
                with_announcements=with_announcements,
                with_news=with_news,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @router.get("/{symbol}")
    def company_detail(request: Request, symbol: str) -> dict:
        try:
            detail = service.company_detail(_data_dir(request), symbol)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        if detail is None:
            raise HTTPException(status_code=404, detail=f"该公司尚未同步: {symbol}")
        return detail

    return router
