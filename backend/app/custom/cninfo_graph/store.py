"""公司图谱数据落盘 — data/graph/ 目录契约。

布局(全部在 DATA_DIR 下, 不提交 Git):
  data/graph/
    tracked.json                          # 重点公司清单: ["000019.SZ", ...]
    state.json                            # {symbol: {org_id, profile:{at,error}, ...}}
    companies/{symbol}/profile.json       # 公司档案(含董监高任职角色, 来源巨潮)
    companies/{symbol}/shareholders.json  # 前五大股东快照(来源巨潮, 每次同步覆盖)
    companies/{symbol}/holders_top10.json # 十大股东/流通股东/户数/实控人(来源东财)
    companies/{symbol}/executives.json    # 董监高详情(简历/薪酬/持股, 来源东财)
    companies/{symbol}/announcements.json # 公告列表(来源巨潮, 增量合并去重)
    companies/{symbol}/news.json          # 个股新闻(来源扶摇, 增量合并去重)

符号统一使用带交易所后缀的标准代码(000019.SZ); 巨潮请求用裸 6 位代码,
由 client 层接受 code 形参剥离。所有写盘走 fs_utils.atomic_write_text。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from app.services.fs_utils import atomic_write_text

logger = logging.getLogger(__name__)

_GRAPH_DIRNAME = "graph"
_TRACKED_FILE = "tracked.json"
_STATE_FILE = "state.json"
_MAX_ANNOUNCEMENTS = 2000  # 单公司公告留存上限(超出丢弃最旧, 防无界增长)
_MAX_NEWS = 300  # 单公司新闻留存上限(时间降序截断)

PROFILE = "profile"
SHAREHOLDERS = "shareholders"
TOP10 = "holders_top10"
EXECUTIVES = "executives"
ANNOUNCEMENTS = "announcements"
NEWS = "news"
_PARTS = (PROFILE, SHAREHOLDERS, TOP10, EXECUTIVES, ANNOUNCEMENTS, NEWS)


def graph_dir(data_dir: Path) -> Path:
    return Path(data_dir) / _GRAPH_DIRNAME


def _company_dir(data_dir: Path, symbol: str) -> Path:
    return graph_dir(data_dir) / "companies" / symbol


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except ValueError:
        logger.warning("graph store: 损坏的 JSON 已忽略: %s", path)
        return None


def _save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=1))


# ---- 重点公司清单 ----
def load_tracked(data_dir: Path) -> list[str]:
    value = _load_json(graph_dir(data_dir) / _TRACKED_FILE)
    return [str(s) for s in value] if isinstance(value, list) else []


def save_tracked(data_dir: Path, symbols: list[str]) -> None:
    _save_json(graph_dir(data_dir) / _TRACKED_FILE, list(symbols))


# ---- 同步状态 ----
def load_state(data_dir: Path) -> dict:
    value = _load_json(graph_dir(data_dir) / _STATE_FILE)
    return value if isinstance(value, dict) else {}


def save_state(data_dir: Path, state: dict) -> None:
    _save_json(graph_dir(data_dir) / _STATE_FILE, state)


def update_symbol_state(data_dir: Path, symbol: str, patch: dict) -> dict:
    """整体读-改-写 state.json(骨架期单进程可接受; 后续接入调度时再评估加锁)。"""
    state = load_state(data_dir)
    entry = state.get(symbol) if isinstance(state.get(symbol), dict) else {}
    entry.update(patch)
    state[symbol] = entry
    save_state(data_dir, state)
    return entry


# ---- 公司数据块 ----
def save_part(data_dir: Path, symbol: str, part: str, value: Any) -> None:
    if part not in _PARTS:
        raise ValueError(f"未知数据块: {part}")
    _save_json(_company_dir(data_dir, symbol) / f"{part}.json", value)


def load_part(data_dir: Path, symbol: str, part: str) -> Any:
    if part not in _PARTS:
        raise ValueError(f"未知数据块: {part}")
    return _load_json(_company_dir(data_dir, symbol) / f"{part}.json")


def merge_announcements(existing: list | None, incoming: list[dict]) -> list[dict]:
    """按 announcement_id 去重合并; 按日期降序, 超上限丢弃最旧。"""
    merged: dict[str, dict] = {}
    order: list[str] = []
    for item in [*(existing or []), *incoming]:
        if not isinstance(item, dict):
            continue
        key = str(item.get("announcement_id") or item.get("title") or id(item))
        if key in merged:
            continue
        merged[key] = item
        order.append(key)

    def sort_key(item: dict) -> str:
        return str(item.get("date") or "")

    items = sorted(merged.values(), key=sort_key, reverse=True)
    return items[:_MAX_ANNOUNCEMENTS]


def merge_news(existing: list | None, incoming: list[dict]) -> list[dict]:
    """按新闻 id 去重合并; 按发布时间降序, 超上限丢弃最旧。"""
    merged: dict[str, dict] = {}
    for item in [*(existing or []), *incoming]:
        if not isinstance(item, dict):
            continue
        key = str(item.get("id") or item.get("title") or id(item))
        if key not in merged:
            merged[key] = item

    def sort_key(item: dict) -> int:
        ts = item.get("publish_time_ms")
        return ts if isinstance(ts, (int, float)) else 0

    items = sorted(merged.values(), key=sort_key, reverse=True)
    return items[:_MAX_NEWS]


def load_company(data_dir: Path, symbol: str) -> dict | None:
    """聚合读取一只公司的全部已存数据; 什么都没有时返回 None。"""
    profile = load_part(data_dir, symbol, PROFILE)
    if profile is None:
        return None
    return {
        "symbol": symbol,
        "profile": profile,
        "shareholders": load_part(data_dir, symbol, SHAREHOLDERS),
        "holders_top10": load_part(data_dir, symbol, TOP10),
        "executives": load_part(data_dir, symbol, EXECUTIVES),
        "announcements": load_part(data_dir, symbol, ANNOUNCEMENTS) or [],
        "news": load_part(data_dir, symbol, NEWS) or [],
    }


def list_companies(data_dir: Path) -> list[str]:
    root = graph_dir(data_dir) / "companies"
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir())
