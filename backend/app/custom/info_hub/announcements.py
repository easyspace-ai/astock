"""公告原始层导入、跨源匹配与统一层重建。"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from . import config, models, store

logger = logging.getLogger(__name__)


def import_legacy(data_dir: str | Path, legacy_root: str | Path) -> dict[str, Any]:
    """把 cninfo-crawler/data 的公告原始层复制进 TSP 扩展目录。"""
    legacy = Path(legacy_root)
    config.ensure_layout(data_dir)
    copied = {"dates": 0, "cninfo_rows": 0, "em_rows": 0, "unified_rows": 0}
    for date_dir in sorted(legacy.glob("dt=20*-*-*")):
        date = date_dir.name[3:]
        cn_file = date_dir / "announcements.jsonl"
        em_file = date_dir / "announcements.em.jsonl"
        if cn_file.exists():
            rows = store.read_jsonl(cn_file)
            store.write_jsonl(store.raw_path(data_dir, "announcements", "cninfo", date), rows)
            copied["cninfo_rows"] += len(rows)
            copied["dates"] += 1
        if em_file.exists():
            rows = store.read_jsonl(em_file)
            store.write_jsonl(store.raw_path(data_dir, "announcements", "em", date), rows)
            copied["em_rows"] += len(rows)
    # 已存在的 unified 直接作为兜底导入，后续可从 raw 重建。
    unified_dir = legacy / "unified"
    for file in sorted(unified_dir.glob("dt=*.jsonl")):
        date = file.stem[3:]
        rows = store.read_jsonl(file)
        if rows and not store.unified_path(data_dir, "announcements", date).exists():
            store.save_unified(data_dir, "announcements", date, rows)
            copied["unified_rows"] += len(rows)
    _import_industry(data_dir, legacy)
    _import_stock_history(data_dir, legacy)
    return copied


def _import_industry(data_dir: str | Path, legacy: Path) -> None:
    path = legacy / "industry_map.jsonl"
    if not path.exists():
        return
    mapping: dict[str, dict[str, Any]] = {}
    for row in store.read_jsonl(path):
        code = str(row.get("sec_code") or "").zfill(6)
        if code and row.get("industry_name"):
            mapping[code] = {
                "name": row.get("industry_name"),
                "as_of": row.get("as_of") or "",
            }
    if mapping:
        store.write_json(config.paths(data_dir).state / "industry_map.json", mapping)


def _import_stock_history(data_dir: str | Path, legacy: Path) -> None:
    """单股全历史（如 dt=stock_600519）写入 per-code raw，后续并入时间线。"""
    for date_dir in legacy.glob("dt=stock_*"):
        file = date_dir / "announcements.jsonl"
        if not file.exists():
            continue
        code = date_dir.name.replace("dt=stock_", "")
        rows = store.read_jsonl(file)
        if rows:
            store.write_jsonl(config.paths(data_dir).raw / "announcements" / "stock_history" / f"{code}.jsonl", rows)


def industry_map(data_dir: str | Path) -> dict[str, dict[str, Any]]:
    return store.read_json(config.paths(data_dir).state / "industry_map.json", {}) or {}


def rebuild(data_dir: str | Path, dates: list[str] | None = None) -> dict[str, Any]:
    p = config.ensure_layout(data_dir)
    dates = dates or sorted({
        file.name[3:]
        for source in ("cninfo", "em")
        for file in (p.raw / "announcements" / source).glob("dt=*")
        if (p.raw / "announcements" / source / file.name / "announcements.jsonl").exists()
    })
    industry = industry_map(data_dir)
    overrides = store.load_overrides(data_dir).get("announcements") or {}
    reports: dict[str, Any] = {}
    total = 0
    for date in dates:
        cn_rows = _load_raw(data_dir, "cninfo", date)
        em_rows = _load_raw(data_dir, "em", date)
        if not cn_rows and not em_rows:
            continue
        days = [_shift(date, -1), date, _shift(date, 1)]
        cn_pool: list[dict[str, Any]] = []
        em_pool: list[dict[str, Any]] = []
        for d in days:
            cn_pool.extend(_load_raw(data_dir, "cninfo", d))
            em_pool.extend(_load_raw(data_dir, "em", d))
        rows, report = merge_day(cn_pool, em_pool, date, industry, overrides)
        if rows:
            store.save_unified(data_dir, "announcements", date, rows)
            store.save_crosscheck(data_dir, date, report)
        reports[date] = report
        total += len(rows)
    _merge_stock_history(data_dir, industry)
    all_rows = store.load_all_unified(data_dir, "announcements")
    store.rebuild_by_code(data_dir, all_rows, "announcements")
    return {"dates": len(reports), "rows": total, "reports": reports}


def _merge_stock_history(data_dir: str | Path, industry: dict[str, dict[str, Any]]) -> None:
    directory = config.paths(data_dir).raw / "announcements" / "stock_history"
    if not directory.exists():
        return
    for file in directory.glob("*.jsonl"):
        code = file.stem
        rows = store.read_jsonl(file)
        if not rows:
            continue
        events = [models.ann_from_cninfo(row, (industry.get(code) or {}).get("name")) for row in rows]
        for event in events:
            event["event_id"] = f"ann:{event['symbol']}:{event['biz_date']}:{event['fp']}"
            event["industry_as_of"] = (industry.get(code) or {}).get("as_of")
        existing = store.load_by_code(data_dir, "announcements", code)
        merged = {event["event_id"]: event for event in existing}
        for event in events:
            merged.setdefault(event["event_id"], event)
        store.write_jsonl(store.by_code_path(data_dir, "announcements", code), sorted(merged.values(), key=lambda x: (x.get("disclosed_at") or x.get("ann_date") or ""), reverse=True))


def merge_day(
    cn_rows: list[dict[str, Any]],
    em_rows: list[dict[str, Any]],
    biz_date: str,
    industry: dict[str, dict[str, Any]] | None = None,
    overrides: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    industry = industry or {}
    overrides = overrides or {}
    cn_records = [models.ann_from_cninfo(row, (industry.get(str(row.get("sec_code") or "").zfill(6)) or {}).get("name")) for row in cn_rows]
    em_records = [r for r in (models.ann_from_eastmoney(row, (industry.get(str((row.get("codes") or [{}])[0].get("stock_code") or "").zfill(6)) or {}).get("name")) for row in em_rows) if r]

    force_same = _force_same(overrides)
    blocked = _blocked(overrides)
    em_by_code: dict[str, list[dict[str, Any]]] = {}
    for rec in em_records:
        em_by_code.setdefault(rec["code"], []).append(rec)
    used: set[int] = set()
    merged: list[dict[str, Any]] = []

    for cn in cn_records:
        em = _find_exact(cn, em_by_code, used)
        note, score = "exact", 1.0
        forced = force_same.get((cn["code"], cn["title_norm"]))
        if forced:
            em = next((r for r in em_by_code.get(cn["code"], []) if r["title_norm"] == forced and id(r) not in used), None)
            if em is not None:
                note, score = "manual", 1.0
        if em is None:
            em, score = _fuzzy(cn, em_by_code.get(cn["code"], []), used, blocked)
            note = "fuzzy" if em is not None else ""
        if em is not None and (cn["code"], cn["title_norm"], em["title_norm"]) not in blocked:
            used.add(id(em))
            merged.append(models.merge_ann_pair(cn, em, score, note))
        else:
            merged.append(cn)

    for em in em_records:
        if id(em) not in used:
            merged.append(em)

    by_fp: dict[str, dict[str, Any]] = {}
    for rec in merged:
        old = by_fp.get(rec["fp"])
        if old is None:
            by_fp[rec["fp"]] = rec
            continue
        keep, drop = (rec, old) if len(rec["sources"]) > len(old["sources"]) else (old, rec)
        by_fp[rec["fp"]] = keep
        drop["dup_of"] = keep["fp"]

    rows = sorted(
        (r for r in by_fp.values() if r.get("biz_date") == biz_date),
        key=lambda r: (r.get("disclosed_at") or r.get("ann_date") or "", r.get("code") or ""),
        reverse=True,
    )
    cn_pool = [r for r in cn_records if r["ann_date"] == biz_date]
    em_pool = [r for r in em_records if r["biz_date"] == biz_date]
    matched = sum(1 for r in rows if len(r["sources"]) > 1)
    report = {
        "date": biz_date,
        "cninfo_rows_pool": len(cn_pool),
        "em_rows_pool": len(em_records),
        "merged": len(rows),
        "matched": matched,
        "cninfo_only": sum(1 for r in rows if r["sources"] == ["cninfo"]),
        "em_only": sum(1 for r in rows if r["sources"] == ["em"]),
        "time_filled": sum(1 for r in rows if r.get("time_filled")),
        "ann_date_shift": sum(1 for r in rows if r.get("ann_date_shift")),
        "match_rate": round(matched / max(1, len(rows)), 3),
        "degraded": not (bool(cn_pool) and bool(em_pool)),
        "cninfo_available": bool(cn_pool),
        "em_available": bool(em_pool),
    }
    return rows, report


def _load_raw(data_dir: str | Path, source: str, date: str) -> list[dict[str, Any]]:
    return store.read_jsonl(store.raw_path(data_dir, "announcements", source, date))


def _find_exact(cn: dict[str, Any], by_code: dict[str, list[dict[str, Any]]], used: set[int]) -> dict[str, Any] | None:
    for em in by_code.get(cn["code"], []):
        if id(em) not in used and em["title_norm"] == cn["title_norm"]:
            return em
    return None


def _fuzzy(
    cn: dict[str, Any],
    candidates: list[dict[str, Any]],
    used: set[int],
    blocked: set[tuple[str, str, str]],
) -> tuple[dict[str, Any] | None, float]:
    best = None
    best_ratio = 0.0
    for em in candidates:
        if id(em) in used:
            continue
        if (cn["code"], cn["title_norm"], em["title_norm"]) in blocked:
            continue
        try:
            gap = abs((datetime.fromisoformat(em["biz_date"]) - datetime.fromisoformat(cn["ann_date"])).days)
        except ValueError:
            gap = 9
        if gap > 1:
            continue
        ratio = models.title_sim(cn["title_norm"], em["title_norm"])
        if ratio > best_ratio:
            best, best_ratio = em, ratio
    return (best, best_ratio) if best_ratio >= 0.86 else (None, best_ratio)


def _force_same(overrides: dict[str, Any]) -> dict[tuple[str, str], str]:
    out: dict[tuple[str, str], str] = {}
    for item in overrides.get("same") or []:
        code = str(item.get("code") or "").zfill(6)
        cn_title = models.norm_title(item.get("cn_title") or "")
        em_title = models.norm_title(item.get("em_title") or "")
        if cn_title and em_title:
            out[(code, cn_title)] = em_title
    return out


def _blocked(overrides: dict[str, Any]) -> set[tuple[str, str, str]]:
    return {
        (str(item.get("code") or "").zfill(6), models.norm_title(item.get("cn_title") or ""), models.norm_title(item.get("em_title") or ""))
        for item in overrides.get("diff") or []
        if item.get("cn_title") and item.get("em_title")
    }


def _shift(date: str, days: int) -> str:
    return (datetime.fromisoformat(date) + timedelta(days=days)).date().isoformat()
