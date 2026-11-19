"""资讯中心磁盘契约：raw / unified / by_code / state / overrides / crosscheck。"""
from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import config

CST = timezone(timedelta(hours=8))


def now_text() -> str:
    return datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S")


def root(data_dir: str | Path) -> Path:
    return config.paths(data_dir).root


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(item, dict):
                    out.append(item)
    except OSError:
        return []
    return out


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        Path(tmp_name).replace(path)
    except Exception:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    return path


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def write_json(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
    tmp.replace(path)
    return path


def raw_path(data_dir: str | Path, kind: str, source: str, date: str) -> Path:
    p = config.paths(data_dir)
    return p.raw / kind / source / f"dt={date}" / ("announcements.jsonl" if kind == "announcements" else "news.jsonl" if kind == "news" else "flash.jsonl")


def unified_path(data_dir: str | Path, kind: str, date: str) -> Path:
    p = config.paths(data_dir)
    return p.unified / kind / f"dt={date}.jsonl"


def save_unified(data_dir: str | Path, kind: str, date: str, rows: list[dict[str, Any]]) -> Path:
    return write_jsonl(unified_path(data_dir, kind, date), rows)


def load_unified(data_dir: str | Path, kind: str, date: str) -> list[dict[str, Any]]:
    return read_jsonl(unified_path(data_dir, kind, date))


def load_all_unified(data_dir: str | Path, kind: str, from_date: str | None = None, to_date: str | None = None) -> list[dict[str, Any]]:
    p = config.paths(data_dir).unified / kind
    if not p.exists():
        return []
    rows: list[dict[str, Any]] = []
    for file in sorted(p.glob("dt=*.jsonl")):
        date = file.stem[3:]
        if from_date and date < from_date:
            continue
        if to_date and date > to_date:
            continue
        rows.extend(read_jsonl(file))
    return rows


def unified_dates(data_dir: str | Path, kind: str) -> list[str]:
    p = config.paths(data_dir).unified / kind
    if not p.exists():
        return []
    return sorted(file.stem[3:] for file in p.glob("dt=*.jsonl"))


def save_crosscheck(data_dir: str | Path, date: str, report: dict[str, Any]) -> Path:
    return write_json(config.paths(data_dir).crosscheck / f"dt={date}.json", report)


def latest_crosscheck(data_dir: str | Path) -> dict[str, Any] | None:
    p = config.paths(data_dir).crosscheck
    files = sorted(p.glob("dt=*.json"))
    if not files:
        return None
    return read_json(files[-1], None)


def load_sources_state(data_dir: str | Path) -> dict[str, Any]:
    return read_json(config.paths(data_dir).state / "sources.json", {}) or {}


def save_sources_state(data_dir: str | Path, payload: dict[str, Any]) -> Path:
    return write_json(config.paths(data_dir).state / "sources.json", payload)


def update_source_state(data_dir: str | Path, source: str, patch: dict[str, Any]) -> dict[str, Any]:
    state = load_sources_state(data_dir)
    current = dict(state.get(source) or {})
    current.update(patch)
    state[source] = current
    save_sources_state(data_dir, state)
    return current


def record_run(data_dir: str | Path, payload: dict[str, Any]) -> Path:
    p = config.paths(data_dir).state / "runs.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"at": now_text(), **payload}, ensure_ascii=False) + "\n")
    return p


def recent_runs(data_dir: str | Path, limit: int = 20) -> list[dict[str, Any]]:
    rows = read_jsonl(config.paths(data_dir).state / "runs.jsonl")
    return rows[-limit:][::-1]


def load_sync_state(data_dir: str | Path) -> dict[str, Any]:
    return read_json(config.paths(data_dir).state / "sync_state.json", {}) or {}


def save_sync_state(data_dir: str | Path, payload: dict[str, Any]) -> Path:
    return write_json(config.paths(data_dir).state / "sync_state.json", payload)


def load_overrides(data_dir: str | Path) -> dict[str, Any]:
    p = config.paths(data_dir).overrides
    return {
        "announcements": read_json(p / "announcement_matches.json", {}) or {},
        "news": read_json(p / "news_matches.json", {}) or {},
    }


def by_code_path(data_dir: str | Path, kind: str, code: str) -> Path:
    return config.paths(data_dir).by_code / kind / f"{code}.jsonl"


def rebuild_by_code(data_dir: str | Path, events: list[dict[str, Any]], kind: str, limit: int = 5000) -> None:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        code = str(event.get("code") or "")
        if not code:
            continue
        grouped.setdefault(code, []).append(event)
    for code, items in grouped.items():
        items.sort(key=lambda x: x.get("publish_time") or x.get("disclosed_at") or x.get("biz_date") or "", reverse=True)
        write_jsonl(by_code_path(data_dir, kind, code), items[:limit])


def load_by_code(data_dir: str | Path, kind: str, code: str) -> list[dict[str, Any]]:
    return read_jsonl(by_code_path(data_dir, kind, code))


def data_bounds(data_dir: str | Path, kind: str) -> dict[str, Any]:
    dates = unified_dates(data_dir, kind)
    return {"dates": len(dates), "date_from": dates[0] if dates else None, "date_to": dates[-1] if dates else None}
