"""资讯中心扩展配置与默认关注池。

数据目录契约见 docs/info-center-integration.md。运行数据只写 DATA_DIR，
不写仓库 data/，也不提交任何机器绝对路径。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# 首版内置关注池：三个板块，聚焦 29 只，避免资讯面过宽。
DEFAULT_SECTORS: dict[str, list[dict[str, str]]] = {
    "新能源": [
        {"code": "300750", "name": "宁德时代", "aliases": ["宁德"]},
        {"code": "002594", "name": "比亚迪"},
        {"code": "300014", "name": "亿纬锂能", "aliases": ["亿纬"]},
        {"code": "002460", "name": "赣锋锂业", "aliases": ["赣锋"]},
        {"code": "002466", "name": "天齐锂业", "aliases": ["天齐"]},
        {"code": "601012", "name": "隆基绿能", "aliases": ["隆基"]},
        {"code": "002459", "name": "晶澳科技", "aliases": ["晶澳"]},
        {"code": "688599", "name": "天合光能", "aliases": ["天合"]},
        {"code": "300274", "name": "阳光电源"},
        {"code": "300124", "name": "汇川技术", "aliases": ["汇川"]},
    ],
    "半导体与算力": [
        {"code": "688981", "name": "中芯国际", "aliases": ["中芯"]},
        {"code": "002371", "name": "北方华创"},
        {"code": "603986", "name": "兆易创新", "aliases": ["兆易"]},
        {"code": "688012", "name": "中微公司", "aliases": ["中微"]},
        {"code": "002049", "name": "紫光国微"},
        {"code": "603501", "name": "韦尔股份", "aliases": ["韦尔"]},
        {"code": "300782", "name": "卓胜微"},
        {"code": "000725", "name": "京东方A", "aliases": ["京东方"]},
        {"code": "002415", "name": "海康威视", "aliases": ["海康"]},
        {"code": "300308", "name": "中际旭创"},
        {"code": "000977", "name": "浪潮信息", "aliases": ["浪潮"]},
    ],
    "白酒与食品": [
        {"code": "600519", "name": "贵州茅台", "aliases": ["茅台"]},
        {"code": "000858", "name": "五粮液"},
        {"code": "000568", "name": "泸州老窖"},
        {"code": "002304", "name": "洋河股份", "aliases": ["洋河"]},
        {"code": "600809", "name": "山西汾酒", "aliases": ["汾酒"]},
        {"code": "000596", "name": "古井贡酒", "aliases": ["古井贡"]},
        {"code": "600887", "name": "伊利股份", "aliases": ["伊利"]},
        {"code": "603288", "name": "海天味业", "aliases": ["海天"]},
    ],
}

DEFAULT_CONFIG: dict[str, Any] = {
    "watchlist": "watchlist.json",
    "sources": {
        "announcements": {
            "order": ["cninfo", "em"],
            "strategy": "all",
            "retries": 1,
            "page_size": 30,
            "max_pages": 90,
        },
        "news": {
            "order": ["ths", "em"],
            "strategy": "all",
            "max_pages": 2,
            "page_size": 100,
            "include_types": [1, 2],
            "since_days": 7,
            "retries": 1,
        },
        "flash": {
            "order": ["ths_flash"],
            "tag_id": 21103,
            "max_pages": 5,
            "since_days": 1,
            "retries": 1,
        },
    },
    "dedupe": {
        "announcement_fuzzy": 0.86,
        "news_title": 0.82,
        "news_window_hours": 6,
    },
    "schedule": {
        "enabled": True,
        "run_on_startup": False,
        "flash_minutes": 30,
        "news_minutes": 30,
        "announcement_times": ["16:40", "20:30"],
        "repair_time": "06:50",
    },
    "retention": {
        "full_market_days": 400,
        "watchlist_days": 1825,
        "news_days": 180,
        "flash_days": 30,
    },
}


@dataclass(frozen=True)
class InfoHubPaths:
    root: Path

    @property
    def config(self) -> Path:
        return self.root / "config.json"

    @property
    def watchlist(self) -> Path:
        return self.root / "watchlist.json"

    @property
    def raw(self) -> Path:
        return self.root / "raw"

    @property
    def unified(self) -> Path:
        return self.root / "unified"

    @property
    def by_code(self) -> Path:
        return self.root / "by_code"

    @property
    def state(self) -> Path:
        return self.root / "state"

    @property
    def overrides(self) -> Path:
        return self.root / "overrides"

    @property
    def crosscheck(self) -> Path:
        return self.root / "crosscheck"


def paths(data_dir: str | Path) -> InfoHubPaths:
    return InfoHubPaths(Path(data_dir) / "info_hub")


def ensure_layout(data_dir: str | Path) -> InfoHubPaths:
    p = paths(data_dir)
    for sub in (p.root, p.raw, p.unified, p.by_code, p.state, p.overrides, p.crosscheck):
        sub.mkdir(parents=True, exist_ok=True)
    if not p.config.exists():
        _write_json(p.config, DEFAULT_CONFIG)
    if not p.watchlist.exists():
        _write_json(p.watchlist, {"sectors": DEFAULT_SECTORS})
    return p


def load_config(data_dir: str | Path) -> dict[str, Any]:
    p = ensure_layout(data_dir)
    try:
        cfg = json.loads(p.config.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        cfg = {}
    merged = _deep_merge(DEFAULT_CONFIG, cfg if isinstance(cfg, dict) else {})
    return merged


def load_watchlist(data_dir: str | Path) -> dict[str, Any]:
    p = ensure_layout(data_dir)
    try:
        data = json.loads(p.watchlist.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {"sectors": DEFAULT_SECTORS}
    return _normalize_watchlist(data)


def save_watchlist(data_dir: str | Path, payload: dict[str, Any]) -> dict[str, Any]:
    p = ensure_layout(data_dir)
    data = _normalize_watchlist(payload)
    _write_json(p.watchlist, data)
    return data


def sector_map(data_dir: str | Path) -> dict[str, str]:
    data = load_watchlist(data_dir)
    out: dict[str, str] = {}
    for sector, items in data["sectors"].items():
        for item in items:
            out[item["code"]] = sector
    return out


def name_map(data_dir: str | Path) -> dict[str, str]:
    data = load_watchlist(data_dir)
    return {item["code"]: item["name"] for items in data["sectors"].values() for item in items}


def _normalize_watchlist(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        data = {}
    sectors = data.get("sectors")
    if not isinstance(sectors, dict):
        sectors = DEFAULT_SECTORS
    out: dict[str, list[dict[str, Any]]] = {}
    seen: set[str] = set()
    for sector, items in sectors.items():
        if not isinstance(items, list):
            continue
        clean: list[dict[str, Any]] = []
        for item in items:
            if isinstance(item, str):
                item = {"code": item, "name": ""}
            if not isinstance(item, dict):
                continue
            code = str(item.get("code") or "").strip().zfill(6)
            if not code.isdigit() or len(code) != 6 or code in seen:
                continue
            seen.add(code)
            clean.append({
                "code": code,
                "name": str(item.get("name") or ""),
                "aliases": [str(a) for a in (item.get("aliases") or []) if str(a).strip()],
            })
        if clean:
            out[str(sector)] = clean
    return {"sectors": out or DEFAULT_SECTORS}


def _deep_merge(base: dict, overlay: dict) -> dict:
    result = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
    tmp.replace(path)
