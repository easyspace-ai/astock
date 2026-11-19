"""扶摇(同花顺) 个股分时新闻客户端 — 图谱消息层。

接口文档: https://fuyao.aicubes.cn/docs/api-reference/news-stock-timeline (2026-11 实测)
GET /api/news/stock/timeline-list?thscode=<同花顺代码>&offset=first&size=1~100
认证: X-api-key (与 fuyao 行情插件共用同一把 Key, 见 secrets_store 字段 fuyao_api_key)。
响应信封 {code, message, data: {items[], has_more, offset}}; code=0 为成功。

thscode 与本项目标准代码同形(000019.SZ / 600519.SH), 直接透传。
时间口径: publish_time_ms 为 Unix 毫秒时间戳(精确时刻, 非零点口径),
date 字段为换算后的北京时间 ISO 字符串(秒)。
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://fuyao.aicubes.cn"
_NEWS_PATH = "/api/news/stock/timeline-list"

_REQUEST_INTERVAL_S = 0.2
_BJT = timezone(timedelta(hours=8))

_FIRST_OFFSET = "first"
_MAX_SIZE = 100


class FuyaoNewsError(Exception):
    """扶摇新闻接口错误(配置缺失 / 网络失败 / 信封 code != 0)。"""


def beijing_dt_of_ms(ms: int | float | None) -> str | None:
    """Unix 毫秒 → 北京时间 ISO 字符串(秒); 非法输入返回 None。"""
    try:
        ts = float(ms)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if ts <= 0:
        return None
    return datetime.fromtimestamp(ts / 1000.0, tz=_BJT).isoformat(timespec="seconds")


def default_api_key() -> str:
    """读取扶摇 API Key(与 fuyao 行情插件共用)。插件缺失时返回空串。"""
    try:
        from app.plugins.fuyao.provider import get_api_key

        return get_api_key() or ""
    except ImportError:
        return ""


class FuyaoNewsClient:
    """扶摇新闻客户端。`transport` 供测试注入 (httpx.MockTransport)。"""

    def __init__(
        self,
        api_key: str,
        timeout: float = 20.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise FuyaoNewsError("fuyao api key 未配置")
        self._http = httpx.Client(
            base_url=BASE_URL,
            headers={"X-api-key": api_key},
            timeout=timeout,
            transport=transport,
        )
        self._last_request_ts = 0.0

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> FuyaoNewsClient:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _throttle(self) -> None:
        now = time.monotonic()
        wait = self._last_request_ts + _REQUEST_INTERVAL_S - now
        if wait > 0:
            time.sleep(wait)
        self._last_request_ts = time.monotonic()

    def fetch_latest(self, symbol: str, *, max_pages: int = 2) -> list[dict]:
        """按标准代码拉最新新闻(归一列表, 时间降序)。max_pages 限制翻页上限。"""
        offset: str | None = _FIRST_OFFSET
        out: list[dict] = []
        seen_ids: set[str] = set()
        for _ in range(max(max_pages, 1)):
            if offset is None:
                break
            self._throttle()
            try:
                resp = self._http.get(
                    _NEWS_PATH,
                    params={"thscode": symbol, "offset": offset, "size": _MAX_SIZE},
                )
            except httpx.HTTPError as e:
                raise FuyaoNewsError(f"网络请求失败: {e}") from e
            if resp.status_code != 200:
                raise FuyaoNewsError(f"HTTP {resp.status_code}: {_NEWS_PATH}")
            try:
                payload = resp.json()
            except ValueError as e:
                raise FuyaoNewsError(f"响应不是 JSON: {_NEWS_PATH}") from e
            data = self._envelope(payload)
            items = data.get("items") or []
            for item in items:
                normalized = self._normalize(item)
                if normalized and normalized["id"] not in seen_ids:
                    seen_ids.add(normalized["id"])
                    out.append(normalized)
            offset = data.get("offset") if data.get("has_more") else None
        out.sort(key=lambda it: it.get("publish_time_ms") or 0, reverse=True)
        return out

    def _envelope(self, payload: Any) -> dict:
        if not isinstance(payload, dict):
            raise FuyaoNewsError("响应不是 JSON 信封")
        if payload.get("code") != 0:
            raise FuyaoNewsError(f"接口 code={payload.get('code')}: {payload.get('message')}")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise FuyaoNewsError("响应缺少 data")
        return data

    def _normalize(self, item: Any) -> dict | None:
        if not isinstance(item, dict):
            return None
        item_id = str(item.get("id") or "").strip()
        title = str(item.get("title") or "").strip()
        if not item_id or not title:
            return None
        return {
            "id": item_id,
            "title": title,
            "type": item.get("type"),
            "author": item.get("author"),
            "source": item.get("source"),
            "summary": item.get("summary"),
            "publish_time_ms": item.get("publish_time_ms"),
            "date": beijing_dt_of_ms(item.get("publish_time_ms")),
            "url": item.get("jump_url"),
            "pic_url": item.get("pic_url"),
        }
