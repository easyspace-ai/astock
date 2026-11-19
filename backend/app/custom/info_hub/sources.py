"""实时源抓取：巨潮/东财公告、同花顺/东财新闻、同花顺快讯。

只负责“抓原始层并落盘”，统一层仍由 announcements.rebuild / news.rebuild 生成。
任一源失败只记录 health，不影响其他源。
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

from . import config, models, store

logger = logging.getLogger(__name__)
CST = timezone(timedelta(hours=8))

CNINFO_URL = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
EM_ANN_URL = "https://np-anotice-stock.eastmoney.com/api/security/ann"
FUYAO_BASE = "https://fuyao.aicubes.cn"
EM_SEARCH_URL = "https://search-api-web.eastmoney.com/search/jsonp"
MARKETS = {"sh": ("sse", "sh"), "sz": ("szse", "sz"), "bj": ("bj", "bj")}


def _key(provider: str = "fuyao") -> str:
    try:
        from app.plugins.fuyao.provider import get_api_key

        key = get_api_key() or ""
        if key:
            return key
    except Exception:  # noqa: BLE001 - 插件缺失时退回本地 Key
        pass
    key = os.environ.get("FUYAO_API_KEY", "").strip()
    if key:
        return key
    p = Path.home() / ".fuyao_api_key"
    return p.read_text("utf-8").strip() if p.exists() else ""


def _headers(source: str = "") -> dict[str, str]:
    ua = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/139 Safari/537.36"
    headers = {"User-Agent": ua, "Accept": "application/json, text/plain, */*"}
    if source == "cninfo":
        headers["Referer"] = "https://www.cninfo.com.cn/"
    elif source == "em":
        headers["Referer"] = "https://data.eastmoney.com/notices/hsa/5.html"
    return headers


def _fuyao_json(path: str, params: dict[str, Any], attempts: int = 3) -> dict[str, Any]:
    """扶摇请求走系统 urllib；避免长连接/代理环境下 httpx TLS 会话偶发握手失败。"""
    key = _key()
    if not key:
        raise RuntimeError("fuyao api key 未配置")
    url = f"{FUYAO_BASE}{path}?{urllib.parse.urlencode(params)}"
    last: Exception | None = None
    # 先走系统 curl：短进程请求快，且能绕开常驻进程里偶发的 TLS 会话僵死。
    try:
        completed = subprocess.run(
            ["curl", "-sS", "--http1.1", "--max-time", "12", "-H", f"X-api-key: {key}", url],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if completed.returncode == 0 and completed.stdout.strip():
            return json.loads(completed.stdout)
        last = RuntimeError((completed.stderr or "curl failed")[:200])
    except Exception as exc:  # noqa: BLE001
        last = exc
    for index in range(max(attempts, 1)):
        request = urllib.request.Request(
            url,
            headers={
                "X-api-key": key,
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/139 Safari/537.36",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return json.loads(response.read().decode("utf-8", "ignore"))
        except Exception as exc:  # noqa: BLE001 - 网络抖动时重试，最后一次抛出
            last = exc
            if index + 1 < attempts:
                time.sleep(1.0 * (index + 1))
    # 最后一次降级用系统 curl：个别常驻进程里 Python SSL 会话会持续握手失败，curl 子进程可绕过该会话状态。
    try:
        completed = subprocess.run(
            ["curl", "-sS", "--http1.1", "--max-time", "20", "-H", f"X-api-key: {key}", url],
            capture_output=True,
            text=True,
            timeout=25,
        )
        if completed.returncode == 0 and completed.stdout.strip():
            return json.loads(completed.stdout)
    except Exception as exc:  # noqa: BLE001
        last = exc
    raise last if last else RuntimeError("fuyao request failed")


def fetch_announcements(data_dir: str | Path, date: str | None = None, max_pages: int = 90) -> dict[str, Any]:
    date = date or datetime.now(CST).date().isoformat()
    config.ensure_layout(data_dir)
    result: dict[str, Any] = {"date": date, "cninfo": 0, "em": 0, "errors": []}
    try:
        rows = fetch_cninfo_day(date, max_pages=max_pages)
        store.write_jsonl(store.raw_path(data_dir, "announcements", "cninfo", date), rows)
        result["cninfo"] = len(rows)
        store.update_source_state(data_dir, "cninfo", {"last_ok_at": store.now_text(), "last_count": len(rows), "last_error": "", "consecutive_fail": 0, "cooldown_until": ""})
    except Exception as exc:  # noqa: BLE001
        result["errors"].append(f"cninfo:{type(exc).__name__}: {exc}"[:200])
        _fail(data_dir, "cninfo", exc)
    try:
        rows = fetch_em_day(date, max_pages=max_pages)
        store.write_jsonl(store.raw_path(data_dir, "announcements", "em", date), rows)
        result["em"] = len(rows)
        store.update_source_state(data_dir, "em", {"last_ok_at": store.now_text(), "last_count": len(rows), "last_error": "", "consecutive_fail": 0, "cooldown_until": ""})
    except Exception as exc:  # noqa: BLE001
        result["errors"].append(f"em:{type(exc).__name__}: {exc}"[:200])
        _fail(data_dir, "em", exc)
    result["degraded"] = bool(result["errors"])
    store.record_run(data_dir, {"action": "fetch_announcements", **result})
    return result


def fetch_cninfo_day(date: str, max_pages: int = 90, page_size: int = 30) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    with httpx.Client(timeout=30, headers=_headers("cninfo")) as client:
        for market, (column, plate) in MARKETS.items():
            for page in range(1, max_pages + 1):
                payload = {
                    "pageNum": str(page), "pageSize": str(page_size),
                    "column": column, "plate": plate, "stock": "",
                    "searchkey": "", "secid": "", "category": "", "trade": "",
                    "seDate": f"{date}~{date}", "sortName": "", "sortType": "", "isHLtitle": "true",
                    "tabName": "fulltext",
                }
                resp = client.post(CNINFO_URL, data=payload)
                resp.raise_for_status()
                data = resp.json()
                items = data.get("announcements") or []
                added = 0
                for item in items:
                    ann_id = str(item.get("announcementId") or "")
                    if ann_id and ann_id in seen_ids:
                        continue
                    raw = _cninfo_raw(item, market)
                    if raw:
                        rows.append(raw)
                        if ann_id:
                            seen_ids.add(ann_id)
                        added += 1
                if len(items) < page_size or added == 0:
                    break
                time.sleep(0.35)
    return rows


def _cninfo_raw(item: dict[str, Any], market: str) -> dict[str, Any] | None:
    code = str(item.get("secCode") or "").zfill(6)
    ann_id = str(item.get("announcementId") or "")
    title = str(item.get("announcementTitle") or "")
    if not code or not ann_id or not title:
        return None
    ms = item.get("announcementTime")
    dt = datetime.fromtimestamp(float(ms) / 1000, CST) if ms else None
    ann_date = dt.date().isoformat() if dt else ""
    ann_time = dt.strftime("%H:%M") if dt and dt.strftime("%H:%M") != "00:00" else None
    adjunct = str(item.get("adjunctUrl") or "")
    return {
        "announcement_id": ann_id,
        "sec_code": code,
        "sec_name": str(item.get("secName") or ""),
        "org_id": str(item.get("orgId") or ""),
        "ann_date": ann_date,
        "ann_time": ann_time,
        "market": market,
        "title": title,
        "ann_type_raw": str(item.get("announcementTypeName") or ""),
        # 巨潮列目录接口返回 announcementType 为 "0101||0102" 字符串；
        # 历史爬虫落盘版本可能是 [{"column_code": ...}]，两种都兼容。
        "ann_type_codes": _cninfo_type_codes(item.get("announcementType")),
        "adjunct_size_kb": round(float(item.get("adjunctSize") or 0) / 1024, 1) if item.get("adjunctSize") else None,
        "pdf_url": f"https://static.cninfo.com.cn/{adjunct}" if adjunct else "",
        "detail_url": f"https://www.cninfo.com.cn/new/disclosure/detail?announcementId={ann_id}",
        "is_correction": bool(re.search("更正|补充", title)),
    }


def _cninfo_type_codes(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [part for part in value.split("||") if part]
    codes: list[str] = []
    for item in value if isinstance(value, list) else []:
        if isinstance(item, dict):
            code = item.get("column_code") or item.get("code")
            if code:
                codes.append(str(code))
        elif item:
            codes.append(str(item))
    return codes


def fetch_em_day(date: str, max_pages: int = 90, page_size: int = 100) -> list[dict[str, Any]]:
    prev = (datetime.fromisoformat(date) - timedelta(days=1)).date().isoformat()
    rows: list[dict[str, Any]] = []
    with httpx.Client(timeout=30, headers=_headers("em")) as client:
        for page in range(1, max_pages + 1):
            resp = client.get(EM_ANN_URL, params={
                "page_size": page_size, "page_index": page,
                "ann_type": "SHA,CYB,SZA,BJA,INV", "client_source": "web",
                "f_node": 0, "stock_list": "",
            })
            resp.raise_for_status()
            payload = resp.json()
            if payload.get("success") != 1:
                raise RuntimeError(f"东财 success={payload.get('success')}: {payload.get('message')}")
            items = (payload.get("data") or {}).get("list") or []
            for item in items:
                if (item.get("notice_date") or "")[:10] == date:
                    rows.append(item)
            if not items or (items[-1].get("display_time") or "")[:10] < prev:
                break
            time.sleep(0.15)
    return rows


def fetch_news(data_dir: str | Path, dates: list[str] | None = None) -> dict[str, Any]:
    cfg = config.load_config(data_dir)["sources"]["news"]
    pool = config.load_watchlist(data_dir)["sectors"]
    since = min(dates) if dates else (datetime.now(CST).date() - timedelta(days=int(cfg.get("since_days", 7)))).isoformat()
    result: dict[str, Any] = {"since": since, "codes": 0, "rows": 0, "sources": {}, "errors": [], "skipped_sources": {}}
    for _sector, items in pool.items():
        for item in items:
            code = item["code"]
            result["codes"] += 1
            rows: list[dict[str, Any]] = []
            for source, fetcher in (("ths", _fetch_ths_news), ("em", _fetch_em_news)):
                if _in_cooldown(data_dir, source):
                    result["skipped_sources"][source] = result["skipped_sources"].get(source, 0) + 1
                    continue
                try:
                    got = fetcher(code, item.get("name") or "", item.get("aliases") or [], since, cfg)
                    rows.extend(got)
                    result["sources"][source] = result["sources"].get(source, 0) + len(got)
                except Exception as exc:  # noqa: BLE001
                    result["errors"].append(f"{source}/{code}:{type(exc).__name__}: {exc}"[:160])
                    _fail(data_dir, source, exc)
            if not rows:
                continue
            by_date: dict[str, list[dict[str, Any]]] = {}
            for row in rows:
                by_date.setdefault(row.get("publish_date") or "", []).append(row)
            for date, items2 in by_date.items():
                if not date:
                    continue
                path = store.raw_path(data_dir, "news", "legacy", date)
                existing = {r.get("news_id"): r for r in store.read_jsonl(path)}
                for row in items2:
                    existing[row["news_id"]] = row
                store.write_jsonl(path, sorted(existing.values(), key=lambda x: x.get("publish_time") or "", reverse=True))
            result["rows"] += len(rows)
            time.sleep(0.2)
    for source in ("ths", "em"):
        if result["skipped_sources"].get(source):
            continue
        if not any(err.startswith(f"{source}/") for err in result["errors"]):
            store.update_source_state(data_dir, source, {
                "last_ok_at": store.now_text(), "last_count": result["sources"].get(source, 0),
                "last_error": "", "consecutive_fail": 0, "cooldown_until": "",
            })
    result["degraded"] = bool(result["errors"]) or bool(result["skipped_sources"])
    store.record_run(data_dir, {"action": "fetch_news", **result})
    return result


def _fetch_ths_news(code: str, name: str, aliases: list[str], since: str, cfg: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = "first"
    types = set(cfg.get("include_types") or [1, 2])
    for _ in range(max(int(cfg.get("max_pages", 2)), 1)):
        payload = _fuyao_json(
            "/api/news/stock/timeline-list",
            {"thscode": models.symbol_of(code), "offset": offset, "size": int(cfg.get("page_size", 100))},
        )
        if payload.get("code") != 0:
            raise RuntimeError(f"扶摇 code={payload.get('code')}: {payload.get('message')}")
        data = payload.get("data") or {}
        items = data.get("items") or []
        for item in items:
            if item.get("type") not in types:
                continue
            title = str(item.get("title") or "")
            if not _on_topic(title, code, name, aliases):
                continue
            ptime, pdate = _ms_time(item.get("publish_time_ms"))
            if not pdate or pdate < since:
                continue
            rows.append({
                "news_id": f"ths:{item.get('id')}", "source": "ths", "news_kind": "report" if item.get("type") == 2 else "news",
                "source_rank": 1, "code": code, "name": name, "title": title,
                "summary": str(item.get("summary") or ""), "content": "", "media": str(item.get("source") or "同花顺"),
                "publish_time": ptime, "publish_date": pdate, "url": str(item.get("jump_url") or ""),
                "match_type": "source", "heat": 0, "dup_of": "", "extra": {"type": item.get("type"), "author": item.get("author")},
                "fetched_at": store.now_text(),
            })
        if not data.get("has_more"):
            break
        offset = data.get("offset") or ""
        if not offset:
            break
        time.sleep(0.2)
    return rows


def _fetch_em_news(code: str, name: str, aliases: list[str], since: str, cfg: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    keywords = [code, name, *list(aliases)]
    keywords = list(dict.fromkeys(k for k in keywords if k))
    with httpx.Client(timeout=20, headers=_headers("em")) as client:
        for kw in keywords:
            for page in range(1, max(int(cfg.get("max_pages", 2)), 1) + 1):
                param = {
                    "uid": "", "keyword": kw, "type": ["cmsArticleWebOld"],
                    "client": "web", "clientType": "web", "clientVersion": "curr",
                    "param": {"cmsArticleWebOld": {"searchScope": "default", "sort": "time", "pageIndex": page, "pageSize": 20, "preTag": "", "postTag": ""}},
                }
                resp = client.get(EM_SEARCH_URL, params={"cb": "cb", "param": json.dumps(param, ensure_ascii=False)})
                resp.raise_for_status()
                text = resp.text
                if "(" not in text:
                    break
                payload = json.loads(text[text.index("(") + 1: text.rindex(")")])
                items = (payload.get("result") or {}).get("cmsArticleWebOld") or []
                if not items:
                    break
                for item in items:
                    title = str(item.get("title") or "")
                    if not _on_topic(title, code, name, aliases):
                        continue
                    ptime, pdate, _precision = models.parse_datetime(item.get("date"))
                    if not pdate or pdate < since:
                        continue
                    art = str(item.get("code") or "")
                    rows.append({
                        "news_id": f"em:{art}", "source": "em", "news_kind": "news", "source_rank": 2,
                        "code": code, "name": name, "title": title, "summary": str(item.get("content") or ""),
                        "content": "", "media": str(item.get("mediaName") or "东方财富"),
                        "publish_time": ptime, "publish_date": pdate, "url": str(item.get("url") or ""),
                        "match_type": "keyword", "heat": int(item.get("clickNum") or 0), "dup_of": "",
                        "extra": {"kw": kw, "article_id": art}, "fetched_at": store.now_text(),
                    })
                if all((models.parse_datetime(i.get("date"))[1] or "9999") < since for i in items):
                    break
                time.sleep(0.5)
    return rows


def fetch_flash(data_dir: str | Path, max_pages: int | None = None) -> dict[str, Any]:
    cfg = config.load_config(data_dir)["sources"]["flash"]
    pool = []
    for _sector, items in config.load_watchlist(data_dir)["sectors"].items():
        for item in items:
            pool.append((item["code"], item["name"], item.get("aliases") or []))
    rows: list[dict[str, Any]] = []
    seq = "0"
    pages = max_pages or int(cfg.get("max_pages", 5))
    for _ in range(pages):
        payload = _fuyao_json(
            "/api/news/flash/list",
            {"tag_id": int(cfg.get("tag_id", 21103)), "seq": seq},
            attempts=max(int(cfg.get("retries", 1)), 1) + 1,
        )
        if payload.get("code") != 0:
            raise RuntimeError(f"扶摇 code={payload.get('code')}: {payload.get('message')}")
        data = payload.get("data") or {}
        items = data.get("items") or []
        for item in items:
            rows.append(_flash_raw(item, pool, int(cfg.get("tag_id", 21103))))
        if not items:
            break
        seq = str(items[-1].get("seq") or "")
        if not seq:
            break
        time.sleep(0.2)
    by_date: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_date.setdefault(row.get("publish_date") or "", []).append(row)
    for date, items in by_date.items():
        if not date:
            continue
        path = store.raw_path(data_dir, "flash", "ths_flash", date)
        existing = {r.get("flash_id"): r for r in store.read_jsonl(path)}
        for row in items:
            existing[row.get("flash_id") or row.get("event_id") or ""] = row
        store.write_jsonl(path, sorted(existing.values(), key=lambda x: x.get("publish_time") or "", reverse=True))
    store.update_source_state(data_dir, "ths_flash", {"last_ok_at": store.now_text(), "last_count": len(rows), "last_error": "", "consecutive_fail": 0, "cooldown_until": ""})
    store.record_run(data_dir, {"action": "fetch_flash", "rows": len(rows), "pages": pages})
    return {"rows": len(rows), "pages": pages, "degraded": False}


def _flash_raw(item: dict[str, Any], pool: list[tuple[str, str, list[str]]], tag_id: int) -> dict[str, Any]:
    ptime, pdate = _ms_time(item.get("create_time_ms"))
    title = str(item.get("title") or "")
    summary = str(item.get("summary") or "")[:300]
    hits: list[tuple[str, str]] = []
    for code, name, aliases in pool:
        for key in [name, *aliases]:
            if key and key in title:
                hits.append((code, name))
                break
    if not hits:
        for code, name, _ in pool:
            if name and name in summary:
                hits.append((code, name))
    return {
        "flash_id": f"flash:{item.get('id')}", "source": "ths_flash", "news_kind": "flash", "source_rank": 1,
        "title": title, "summary": summary, "content": "", "media": "同花顺7x24快讯",
        "publish_time": ptime, "publish_date": pdate, "url": str(item.get("encoded_url") or ""),
        "encoded_seq": _encoded_seq(str(item.get("encoded_url") or "")),
        "tags": [t.get("name") for t in (item.get("tags") or []) if isinstance(t, dict)],
        "tag_id": tag_id, "sec_codes": [c for c, _ in hits], "sec_names": [n for _, n in hits],
        "code": hits[0][0] if hits else "", "name": hits[0][1] if hits else "",
        "match_type": "title" if hits else "market", "heat": 0, "extra": {"seq": item.get("seq")},
        "fetched_at": store.now_text(),
    }


def _on_topic(title: str, code: str, name: str, aliases: list[str]) -> bool:
    keys = [name, *aliases]
    if name.endswith(("A", "B")):
        keys.append(name[:-1])
    return code in title or any(k and k in title for k in keys if k)


def _ms_time(value: Any) -> tuple[str, str]:
    try:
        ts = float(value) / 1000.0
        dt = datetime.fromtimestamp(ts, CST)
    except (TypeError, ValueError):
        return "", ""
    return dt.strftime("%Y-%m-%d %H:%M:%S"), dt.date().isoformat()


def _encoded_seq(url: str) -> str:
    m = re.search(r"/[0-9]{8}/([0-9a-f]{20,})\.shtml", url)
    return m.group(1) if m else ""


def _in_cooldown(data_dir: str | Path, source: str) -> bool:
    state = store.load_sources_state(data_dir).get(source) or {}
    until = str(state.get("cooldown_until") or "")
    if not until:
        return False
    try:
        return datetime.fromisoformat(until) > datetime.now(CST)
    except ValueError:
        return False


def _fail(data_dir: str | Path, source: str, exc: Exception) -> None:
    state = store.load_sources_state(data_dir).get(source) or {}
    fail = int(state.get("consecutive_fail") or 0) + 1
    store.update_source_state(data_dir, source, {
        "last_error": f"{type(exc).__name__}: {exc}"[:200],
        "consecutive_fail": fail,
        "cooldown_until": (datetime.now(CST) + timedelta(minutes=15)).isoformat(timespec="seconds") if fail >= 3 else state.get("cooldown_until"),
    })
