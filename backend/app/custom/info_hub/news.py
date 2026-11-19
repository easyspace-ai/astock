"""新闻 / 研报 / 快讯导入与统一层重建。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from . import config, models, store


def import_legacy(data_dir: str | Path, legacy_root: str | Path) -> dict[str, Any]:
    """把 stock-news/data 的新闻、快讯导入 TSP 扩展目录。"""
    legacy = Path(legacy_root)
    copied = {"news_dates": 0, "news_rows": 0, "flash_dates": 0, "flash_rows": 0, "pool": 0}
    for date_dir in sorted(legacy.glob("dt=*")):
        name = date_dir.name[3:]
        if not name.startswith("20"):
            continue
        news = date_dir / "news.jsonl"
        if news.exists():
            rows = store.read_jsonl(news)
            store.write_jsonl(store.raw_path(data_dir, "news", "legacy", name), rows)
            copied["news_dates"] += 1
            copied["news_rows"] += len(rows)
    flash_dir = legacy / "flash"
    for date_dir in sorted(flash_dir.glob("dt=*")):
        name = date_dir.name[3:]
        flash = date_dir / "flash.jsonl"
        if flash.exists():
            rows = store.read_jsonl(flash)
            store.write_jsonl(store.raw_path(data_dir, "flash", "ths_flash", name), rows)
            copied["flash_dates"] += 1
            copied["flash_rows"] += len(rows)
    _import_pool(data_dir, legacy)
    return copied


def _import_pool(data_dir: str | Path, legacy: Path) -> None:
    sectors_file = legacy.parent / "pool" / "sectors.json"
    watch_file = legacy.parent / "pool" / "watchlist.txt"
    if not sectors_file.exists() and not watch_file.exists():
        return
    name_by_code: dict[str, str] = {}
    aliases_by_code: dict[str, list[str]] = {}
    if watch_file.exists():
        for line in watch_file.read_text("utf-8").splitlines():
            parts = line.strip().split()
            if len(parts) >= 2 and parts[0].isdigit():
                name_by_code[parts[0].zfill(6)] = parts[1]
                aliases_by_code[parts[0].zfill(6)] = parts[2:]
    payload: dict[str, list[dict[str, Any]]] = {}
    if sectors_file.exists():
        try:
            import json
            sectors = json.loads(sectors_file.read_text("utf-8"))
        except (OSError, ValueError):
            sectors = {}
        for sector, codes in sectors.items():
            if not isinstance(codes, list):
                continue
            payload[str(sector)] = [
                {"code": str(c).zfill(6), "name": name_by_code.get(str(c).zfill(6), ""),
                 "aliases": aliases_by_code.get(str(c).zfill(6), [])}
                for c in codes
            ]
    elif name_by_code:
        payload["关注池"] = [{"code": code, "name": name} for code, name in name_by_code.items()]
    if payload:
        config.save_watchlist(data_dir, {"sectors": payload})


def rebuild(data_dir: str | Path) -> dict[str, Any]:
    p = config.ensure_layout(data_dir)
    sectors = config.sector_map(data_dir)
    total_news = total_flash = 0
    news_dates = sorted(file.name[3:] for file in (p.raw / "news" / "legacy").glob("dt=*") if (file / "news.jsonl").exists())
    flash_dates = sorted(file.name[3:] for file in (p.raw / "flash" / "ths_flash").glob("dt=*") if (file / "flash.jsonl").exists())
    for date in news_dates:
        rows = store.read_jsonl(store.raw_path(data_dir, "news", "legacy", date))
        if not rows:
            continue
        for row in rows:
            row.setdefault("_sector", sectors.get(str(row.get("code") or "").zfill(6)))
        events = models.merge_news_rows([{k: v for k, v in row.items() if k != "_sector"} for row in rows])
        for event in events:
            event["sector"] = sectors.get(event.get("code", ""))
        store.save_unified(data_dir, "news", date, events)
        total_news += len(events)
    for date in flash_dates:
        rows = store.read_jsonl(store.raw_path(data_dir, "flash", "ths_flash", date))
        events = []
        for row in rows:
            code = str(row.get("code") or "").zfill(6) if row.get("code") else ""
            event = models.flash_event(row, sectors.get(code))
            events.append(event)
        store.save_unified(data_dir, "flash", date, events)
        total_flash += len(events)
    all_news = store.load_all_unified(data_dir, "news")
    all_flash = store.load_all_unified(data_dir, "flash")
    store.rebuild_by_code(data_dir, all_news, "news")
    store.rebuild_by_code(data_dir, all_flash, "flash")
    return {"news_dates": len(news_dates), "news": total_news, "flash_dates": len(flash_dates), "flash": total_flash}
