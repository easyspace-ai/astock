"""Reverse-proxy /api/v1/* to the Go aistock sidecar.

Dev: Vite may proxy /api/v1 directly; this module covers production /
same-origin FastAPI deployments where the browser talks only to the panel
backend.

提示词管理接口（/api/v1/analysis/prompts*）在反代层加超管守卫；其余
analysis / picker / market 路由按 sidecar 模式透传，不做额外鉴权。
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from urllib.parse import urljoin

import httpx
from fastapi import APIRouter, Request, Response
from fastapi.responses import StreamingResponse

from app.api.permissions import require_admin
from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["aistock-proxy"])

_HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
}

# 提示词管理路径前缀（去掉 /api/v1 后的后缀）。
_PROMPTS_PREFIX = "analysis/prompts"


def aistock_upstream_base() -> str:
    """Normalized upstream base URL (no trailing slash), or empty if disabled."""
    raw = (settings.aistock_url or "").strip()
    if not raw:
        return ""
    return raw.rstrip("/")


def build_upstream_url(path: str, query: str = "") -> str:
    """Build full upstream URL for a path under /api/v1."""
    base = aistock_upstream_base()
    if not base:
        raise ValueError("AISTOCK_URL is empty")
    suffix = path.lstrip("/")
    url = urljoin(base + "/", "api/v1/" + suffix) if suffix else base + "/api/v1"
    if query:
        url = f"{url}?{query}"
    return url


def _is_prompts_path(path: str) -> bool:
    suffix = path.lstrip("/")
    return suffix == _PROMPTS_PREFIX or suffix.startswith(_PROMPTS_PREFIX + "/")


def _filter_request_headers(headers: httpx.Headers | dict[str, str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in headers.items():
        if key.lower() in _HOP_BY_HOP:
            continue
        out[key] = value
    return out


def _filter_response_headers(headers: httpx.Headers) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in headers.items():
        if key.lower() in _HOP_BY_HOP:
            continue
        out[key] = value
    return out


@router.api_route("/api/v1", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
@router.api_route("/api/v1/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
async def proxy_aistock(request: Request, path: str = "") -> Response:
    # 提示词管理仅超管可访问；其余 sidecar 路由透传。
    if _is_prompts_path(path):
        require_admin(request)

    base = aistock_upstream_base()
    if not base:
        return Response(
            content='{"detail":"aistock sidecar proxy disabled (set AISTOCK_URL)"}',
            status_code=503,
            media_type="application/json",
        )

    upstream = build_upstream_url(path, request.url.query)
    body = await request.body()
    req_headers = _filter_request_headers(request.headers)

    # read = idle between chunks (SSE / long W6 prefetch). Default ~45m via settings.
    timeout = httpx.Timeout(
        connect=10.0,
        read=settings.aistock_proxy_read_timeout,
        write=60.0,
        pool=10.0,
    )

    try:
        client = httpx.AsyncClient(timeout=timeout, follow_redirects=False)
    except Exception as exc:  # noqa: BLE001
        logger.error("aistock proxy client init failed: %s", exc)
        return Response(
            content='{"detail":"aistock proxy misconfigured"}',
            status_code=502,
            media_type="application/json",
        )

    try:
        req = client.build_request(
            request.method,
            upstream,
            headers=req_headers,
            content=body,
        )
        upstream_resp = await client.send(req, stream=True)
    except httpx.ConnectError:
        await client.aclose()
        return Response(
            content='{"detail":"aistock sidecar unreachable; start Go server on AISTOCK_URL"}',
            status_code=502,
            media_type="application/json",
        )
    except httpx.TimeoutException:
        await client.aclose()
        return Response(
            content='{"detail":"aistock sidecar timeout"}',
            status_code=504,
            media_type="application/json",
        )
    except Exception as exc:  # noqa: BLE001
        await client.aclose()
        logger.warning("aistock proxy error: %s", exc)
        return Response(
            content='{"detail":"aistock proxy error"}',
            status_code=502,
            media_type="application/json",
        )

    resp_headers = _filter_response_headers(upstream_resp.headers)
    media_type = upstream_resp.headers.get("content-type")

    async def body_iter() -> AsyncIterator[bytes]:
        try:
            async for chunk in upstream_resp.aiter_bytes():
                yield chunk
        finally:
            await upstream_resp.aclose()
            await client.aclose()

    return StreamingResponse(
        body_iter(),
        status_code=upstream_resp.status_code,
        headers=resp_headers,
        media_type=media_type,
    )
