"""资讯中心查询服务：统一过滤、统计、详情与时间线。"""
from __future__ import annotations

import re
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from . import announcements, config, models, news, store


class SyncBusyError(RuntimeError):
    """已有抓取在跑; 手动触发不排队, 由路由层转成 409 让前端提示稍后再试。"""


# 每个 data_dir 一把抓取锁: 第三方源是部署级共享资源, 不能按请求并发。
_SYNC_LOCKS: dict[str, threading.Lock] = {}
_SYNC_LOCKS_GUARD = threading.Lock()


def _sync_lock(data_dir: str | Path) -> threading.Lock:
    key = str(Path(data_dir))
    with _SYNC_LOCKS_GUARD:
        lock = _SYNC_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _SYNC_LOCKS[key] = lock
        return lock


class InfoService:
    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self._lock = threading.Lock()
        self._cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
        self._ttl = 20.0

    def invalidate(self) -> None:
        with self._lock:
            self._cache.clear()

    def _events(self, kind: str) -> list[dict[str, Any]]:
        with self._lock:
            cached = self._cache.get(kind)
            if cached and time.time() - cached[0] < self._ttl:
                return cached[1]
        rows = store.load_all_unified(self.data_dir, kind)
        with self._lock:
            self._cache[kind] = (time.time(), rows)
        return rows

    def announcements(self, params: dict[str, Any]) -> dict[str, Any]:
        rows = self._events("announcements")
        source = str(params.get("source") or "")
        if source:
            rows = [r for r in rows if _ann_source_match(r, source)]
        code = models.symbol_from_symbol(str(params.get("code") or params.get("symbol") or ""))
        if code:
            rows = [r for r in rows if r.get("code") == code]
        industry = str(params.get("industry") or "")
        if industry:
            rows = [r for r in rows if r.get("industry_l1") == industry]
        type_name = str(params.get("type") or "")
        if type_name:
            rows = [r for r in rows if r.get("type_primary") == type_name]
        date = str(params.get("date") or "")
        from_date = str(params.get("from") or params.get("from_date") or "")
        to_date = str(params.get("to") or params.get("to_date") or "")
        if date:
            rows = [r for r in rows if (r.get("biz_date") or r.get("ann_date")) == date]
        if from_date:
            rows = [r for r in rows if (r.get("biz_date") or "") >= from_date]
        if to_date:
            rows = [r for r in rows if (r.get("biz_date") or "") <= to_date]
        if str(params.get("important") or "") in ("1", "true", "True"):
            rows = [r for r in rows if r.get("is_material")]
        q = str(params.get("q") or "").strip().lower()
        if q:
            rows = [r for r in rows if q in (r.get("title") or "").lower() or q in (r.get("name") or "").lower() or q in (r.get("code") or "")]
        rows.sort(key=lambda r: (r.get("disclosed_at") or r.get("ann_date") or "", r.get("code") or ""), reverse=True)
        return _page(rows, params)

    def announcement_detail(self, event_id: str) -> dict[str, Any] | None:
        for row in self._events("announcements"):
            if row.get("event_id") == event_id or row.get("fp") == event_id:
                return row
        return None

    def news_rows(self, params: dict[str, Any], kind: str = "news") -> dict[str, Any]:
        rows = [r for r in self._events("news") if r.get("news_kind") == kind] if kind != "all" else self._events("news")
        code = models.symbol_from_symbol(str(params.get("code") or params.get("symbol") or ""))
        if code:
            rows = [r for r in rows if r.get("code") == code]
        sector = str(params.get("sector") or "")
        if sector:
            rows = [r for r in rows if r.get("sector") == sector]
        source = str(params.get("source") or "")
        if source:
            rows = [r for r in rows if source in (r.get("sources") or [])]
        date = str(params.get("date") or "")
        from_date = str(params.get("from") or params.get("from_date") or "")
        to_date = str(params.get("to") or params.get("to_date") or "")
        if date:
            rows = [r for r in rows if r.get("publish_date") == date]
        if from_date:
            rows = [r for r in rows if (r.get("publish_date") or "") >= from_date]
        if to_date:
            rows = [r for r in rows if (r.get("publish_date") or "") <= to_date]
        q = str(params.get("q") or "").strip().lower()
        if q:
            rows = [r for r in rows if q in (r.get("title") or "").lower() or q in (r.get("summary") or "").lower() or q in (r.get("name") or "").lower()]
        rows.sort(key=lambda r: (r.get("publish_time") or r.get("publish_date") or ""), reverse=True)
        return _page(rows, params)

    def news_detail(self, event_id: str) -> dict[str, Any] | None:
        for row in self._events("news"):
            if row.get("event_id") == event_id:
                return row
        return None

    def flash(self, params: dict[str, Any]) -> dict[str, Any]:
        rows = self._events("flash")
        code = models.symbol_from_symbol(str(params.get("code") or ""))
        if code:
            rows = [r for r in rows if code in (r.get("sec_codes") or []) or r.get("code") == code]
        sector = str(params.get("sector") or "")
        if sector:
            rows = [r for r in rows if r.get("sector") == sector or any(_sector_of_code(self.data_dir, c) == sector for c in (r.get("sec_codes") or []))]
        date = str(params.get("date") or "")
        if date:
            rows = [r for r in rows if r.get("publish_date") == date]
        if str(params.get("hit") or "") in ("1", "true", "True"):
            rows = [r for r in rows if r.get("sec_codes")]
        q = str(params.get("q") or "").strip().lower()
        if q:
            rows = [r for r in rows if q in (r.get("title") or "").lower() or q in (r.get("summary") or "").lower()]
        rows.sort(key=lambda r: (r.get("publish_time") or ""), reverse=True)
        return _page(rows, params)

    def timeline(self, code: str, days: int = 30, kinds: set[str] | None = None) -> dict[str, Any]:
        code = models.symbol_from_symbol(code)
        if not code:
            raise ValueError("缺少 code")
        since = (datetime.now().date() - timedelta(days=days)).isoformat()
        kinds = kinds or {"announcement", "news", "report", "flash"}
        events: list[dict[str, Any]] = []
        if "announcement" in kinds:
            ann_rows = [r for r in self._events("announcements") if r.get("code") == code and (r.get("biz_date") or "") >= since]
            ann_rows.extend(r for r in store.load_by_code(self.data_dir, "announcements", code) if (r.get("biz_date") or "") >= since)
            seen: set[str] = set()
            for row in ann_rows:
                if row.get("event_id") not in seen:
                    seen.add(row.get("event_id") or "")
                    events.append(row)
        if "news" in kinds:
            events.extend(r for r in self._events("news") if r.get("code") == code and r.get("news_kind") == "news" and (r.get("publish_date") or "") >= since)
        if "report" in kinds:
            events.extend(r for r in self._events("news") if r.get("code") == code and r.get("news_kind") == "report" and (r.get("publish_date") or "") >= since)
        if "flash" in kinds:
            events.extend(r for r in self._events("flash") if code in (r.get("sec_codes") or []) and (r.get("publish_date") or "") >= since)
        events.sort(key=lambda r: _event_time(r), reverse=True)
        counts: dict[str, int] = {}
        for event in events:
            counts[event.get("event_kind") or "other"] = counts.get(event.get("event_kind") or "other", 0) + 1
        return {
            "code": code,
            "symbol": models.symbol_of(code),
            "name": config.name_map(self.data_dir).get(code, ""),
            "days": days,
            "since": since,
            "counts": counts,
            "events": events,
        }

    def stats(self, date: str | None = None) -> dict[str, Any]:
        ann = self._events("announcements")
        nws = self._events("news")
        fla = self._events("flash")
        if date:
            ann = [r for r in ann if (r.get("biz_date") or "") == date]
            nws = [r for r in nws if (r.get("publish_date") or "") == date]
            fla = [r for r in fla if (r.get("publish_date") or "") == date]
        return {
            "date": date,
            "totals": {"announcements": len(ann), "news": len(nws), "flash": len(fla)},
            "announcements": {
                "both": sum(1 for r in ann if len(r.get("sources") or []) > 1),
                "cninfo_only": sum(1 for r in ann if r.get("sources") == ["cninfo"]),
                "em_only": sum(1 for r in ann if r.get("sources") == ["em"]),
                "material": sum(1 for r in ann if r.get("is_material")),
            },
            "news": {
                "news": sum(1 for r in nws if r.get("news_kind") == "news"),
                "report": sum(1 for r in nws if r.get("news_kind") == "report"),
                "sources": _count_field(nws, "sources"),
            },
            "flash": {"hit": sum(1 for r in fla if r.get("sec_codes")), "total": len(fla)},
            "sectors": _count_field([r for r in nws if r.get("sector")], "sector"),
            "dates": {
                "announcements": store.data_bounds(self.data_dir, "announcements"),
                "news": store.data_bounds(self.data_dir, "news"),
                "flash": store.data_bounds(self.data_dir, "flash"),
            },
        }

    def health(self) -> dict[str, Any]:
        from . import scheduler

        cfg = config.load_config(self.data_dir)
        return {
            "ok": True,
            "data_dir": str(self.data_dir),
            "counts": self.stats()["totals"],
            "dates": {
                "announcements": store.data_bounds(self.data_dir, "announcements"),
                "news": store.data_bounds(self.data_dir, "news"),
                "flash": store.data_bounds(self.data_dir, "flash"),
            },
            "sources": store.load_sources_state(self.data_dir),
            "crosscheck": store.latest_crosscheck(self.data_dir),
            "watchlist": config.load_watchlist(self.data_dir),
            "schedule": cfg.get("schedule"),
            "scheduler": scheduler.status(),
            "runs": store.recent_runs(self.data_dir, 10),
            "updated_at": store.now_text(),
        }

    def sync(
        self,
        kinds: list[str] | None = None,
        legacy_root: str | None = None,
        force: bool = False,
        live: bool = False,
        date: str | None = None,
        *,
        wait: bool = True,
        actor: str = "",
    ) -> dict[str, Any]:
        """抓取/重建资讯数据。

        抓取是「共享数据集的增量刷新」: 多账号共用同一个 info_hub 目录, 并发抓取
        既会重复打第三方源(限流/封 IP), 也会重复 rebuild。这里用按 data_dir 的
        全局单飞锁串行化: 定时任务 wait=True 排队不丢任务, 手动触发 wait=False
        立即返回 SyncBusyError, 由路由层转成 409。
        """
        lock = _sync_lock(self.data_dir)
        if not lock.acquire(blocking=wait):
            raise SyncBusyError("资讯抓取正在进行中，请稍后再试")
        try:
            return self._sync_locked(kinds, legacy_root, force, live, date, actor)
        finally:
            lock.release()

    def _sync_locked(
        self,
        kinds: list[str] | None,
        legacy_root: str | None,
        force: bool,
        live: bool,
        date: str | None,
        actor: str,
    ) -> dict[str, Any]:
        kinds = kinds or ["announcements", "news", "flash"]
        result: dict[str, Any] = {"kinds": kinds, "at": store.now_text()}
        legacy_root = legacy_root or ""
        if legacy_root:
            if "announcements" in kinds:
                result["announcements_import"] = announcements.import_legacy(self.data_dir, legacy_root + "/cninfo-crawler/data")
            if "news" in kinds or "flash" in kinds:
                result["news_import"] = news.import_legacy(self.data_dir, legacy_root + "/stock-news/data")
        if live:
            from . import sources

            if "announcements" in kinds:
                result["announcements_fetch"] = sources.fetch_announcements(self.data_dir, date=date)
            if "news" in kinds:
                result["news_fetch"] = sources.fetch_news(self.data_dir)
            if "flash" in kinds:
                try:
                    result["flash_fetch"] = sources.fetch_flash(self.data_dir)
                except Exception as exc:  # noqa: BLE001
                    result["flash_fetch"] = {"error": f"{type(exc).__name__}: {exc}"}
                    sources._fail(self.data_dir, "ths_flash", exc)
        if "announcements" in kinds:
            result["announcements"] = announcements.rebuild(self.data_dir)
        if "news" in kinds:
            result["news"] = news.rebuild(self.data_dir)
        if "flash" in kinds:
            result["flash"] = news.rebuild(self.data_dir)
        self.invalidate()
        store.record_run(self.data_dir, {
            "action": "sync",
            "kinds": kinds,
            "ok": True,
            "actor": actor,
            "summary": {k: _compact(v) for k, v in result.items() if k != "at"},
        })
        return result


def _compact(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _compact(v) for k, v in value.items() if k not in ("reports", "items", "events")}
    return value


_SERVICE_CACHE: dict[str, InfoService] = {}
_SERVICE_LOCK = threading.Lock()


def service_for(data_dir: str | Path) -> InfoService:
    key = str(Path(data_dir))
    with _SERVICE_LOCK:
        service = _SERVICE_CACHE.get(key)
        if service is None:
            service = InfoService(data_dir)
            _SERVICE_CACHE[key] = service
        return service


def _page(rows: list[dict[str, Any]], params: dict[str, Any]) -> dict[str, Any]:
    try:
        page_no = max(int(params.get("page") or 1), 1)
    except (TypeError, ValueError):
        page_no = 1
    try:
        size = min(max(int(params.get("size") or 20), 1), 200)
    except (TypeError, ValueError):
        size = 20
    total = len(rows)
    start = (page_no - 1) * size
    return {"total": total, "page": page_no, "size": size, "items": rows[start:start + size]}


def _ann_source_match(row: dict[str, Any], mode: str) -> bool:
    sources = set(row.get("sources") or [])
    if mode == "both":
        return {"cninfo", "em"} <= sources
    if mode == "only_cninfo":
        return sources == {"cninfo"}
    if mode == "only_em":
        return sources == {"em"}
    return mode in sources


def _count_field(rows: list[dict[str, Any]], field: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        value = row.get(field)
        values = value if isinstance(value, list) else [value]
        for item in values:
            if item:
                counts[str(item)] = counts.get(str(item), 0) + 1
    return dict(sorted(counts.items(), key=lambda x: -x[1]))


def _sector_of_code(data_dir: str | Path, code: str) -> str | None:
    return config.sector_map(data_dir).get(str(code).zfill(6))


def _event_time(event: dict[str, Any]) -> str:
    return event.get("disclosed_at") or event.get("publish_time") or event.get("ann_date") or event.get("publish_date") or ""


_RE_CODE = re.compile(r"^\d{6}(?:\.(?:SH|SZ|BJ))?$")
