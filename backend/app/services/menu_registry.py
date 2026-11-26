"""统一菜单树 — 权限目录与前端导航的共享数据源。

静态节点来自 `services/rbac.py`（核心页面、官方扩展页、管理后台）；
动态节点来自运行时数据：
  - data/analysis_menus/*.json  -> page.analysis.<menu_id>
  - data/ext_pages/*/page.json  -> page.ext.route.<route_slug>

新增分析菜单或运行时扩展页时，权限目录会自动多出一个叶子页面；默认不授权，
由管理员在权限组中勾选。纯分组节点不保存权限码。
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from app.services import rbac
from app.services.ext_pages import PagePackStore

# 动态菜单的基础 order，保证排在静态页面之后；同一来源内按 order/label 稳定排序。
_ANALYSIS_BASE_ORDER = 1000
_RUNTIME_BASE_ORDER = 2000
_SOURCE_BASE_ORDER = 2100
_OVERRIDES_FILE = "menu_overrides.json"
_STATIC_PERMISSION_KEYS = {
    node["key"] for node in rbac.menu_nodes() if node.get("kind") == "permission"
}
_STATIC_ROUTES = {
    node["route"] for node in rbac.menu_nodes() if node.get("route")
}


def _frontend_dist() -> Path:
    override = os.environ.get("TSP_FRONTEND_DIST")
    if override:
        return Path(override)
    # backend/app/services/menu_registry.py -> 仓库根/frontend/dist
    return Path(__file__).resolve().parents[3] / "frontend" / "dist"


def source_page_nodes(manifest_path: Path | None = None) -> list[dict[str, Any]]:
    """读取前端构建生成的源码扩展页清单。

    前端 `pnpm build` 会扫描 `src/custom/*/extension.tsx` 并写出
    `dist/navigation-manifest.json`；后端直接读产物，新增源码扩展页后无需再手工
    同步 rbac 静态清单。静态清单里已有的官方页仍保留原有主题分组。
    """
    path = manifest_path or (_frontend_dist() / "navigation-manifest.json")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return []
    pages = raw.get("pages") if isinstance(raw, dict) else None
    if not isinstance(pages, list):
        return []
    nodes: list[dict[str, Any]] = []
    for page in pages:
        if not isinstance(page, dict):
            continue
        route = str(page.get("path") or "")
        label = str(page.get("label") or "").strip()
        if not route.startswith("/") or not label:
            continue
        key = rbac.extension_page_key(route)
        if key in _STATIC_PERMISSION_KEYS or route in _STATIC_ROUTES:
            continue
        try:
            order = int(page.get("order") or 100)
        except (TypeError, ValueError):
            order = 100
        nodes.append({
            "key": key,
            "label": label,
            "group": rbac.GROUP_PAGE,
            "description": "源码扩展页（构建清单自动发现）",
            "parent": rbac.MENU_EXTENSIONS,
            "order": _SOURCE_BASE_ORDER + order,
            "kind": "permission",
            "route": route,
            "source": "source",
            "visible": True,
        })
    return nodes


def _analysis_nodes(data_dir: Path) -> list[dict[str, Any]]:
    base = data_dir / "analysis_menus"
    if not base.exists():
        return []
    nodes: list[dict[str, Any]] = []
    for path in sorted(base.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            menu_id = str(raw.get("id") or path.stem)
            if not menu_id or not menu_id.replace("_", "").isalnum():
                continue
            label = str(raw.get("label") or menu_id)
            order = int(raw.get("order") or 0)
            visible = raw.get("visible", True) is not False
            route = f"/analysis/{menu_id}"
            nodes.append({
                "key": f"{rbac.PAGE_ANALYSIS_PREFIX}{menu_id}",
                "label": label,
                "group": rbac.GROUP_PAGE,
                "description": "分析中心自定义页面",
                "parent": rbac.MENU_ANALYSIS,
                "order": _ANALYSIS_BASE_ORDER + order,
                "kind": "permission",
                "route": route,
                "source": "analysis",
                "visible": visible,
            })
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            # 单个菜单损坏不阻断权限目录；页面本身由分析菜单接口负责报错。
            continue
    return nodes


def _runtime_nodes(data_dir: Path) -> list[dict[str, Any]]:
    try:
        metas = PagePackStore(data_dir).list()
    except OSError:
        return []
    nodes: list[dict[str, Any]] = []
    for meta in metas:
        if not meta.enabled or not meta.path or not meta.path.startswith("/"):
            continue
        nodes.append({
            "key": rbac.extension_page_key(meta.path),
            "label": meta.label or meta.id,
            "group": rbac.GROUP_PAGE,
            "description": "运行时页面插件",
            "parent": rbac.MENU_EXTENSIONS,
            "order": _RUNTIME_BASE_ORDER + int(meta.uploaded_at or 0),
            "kind": "permission",
            "route": meta.path,
            "source": "runtime",
            "visible": True,
        })
    return nodes


def _base_nodes(data_dir: Path) -> list[dict[str, Any]]:
    return sorted(
        [
            *rbac.menu_nodes(),
            *source_page_nodes(),
            *_analysis_nodes(data_dir),
            *_runtime_nodes(data_dir),
        ],
        key=lambda n: (int(n.get("order") or 0), str(n.get("key") or "")),
    )


def _overrides_path(data_dir: Path) -> Path:
    return data_dir / _OVERRIDES_FILE


def load_overrides(data_dir: Path) -> dict[str, Any]:
    try:
        raw = json.loads(_overrides_path(data_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _apply_overrides(
    nodes: list[dict[str, Any]], overrides: dict[str, Any]
) -> list[dict[str, Any]]:
    groups = overrides.get("groups") if isinstance(overrides.get("groups"), dict) else {}
    pages = overrides.get("pages") if isinstance(overrides.get("pages"), dict) else {}
    by_key = {str(node["key"]): node for node in nodes}
    for key, spec in groups.items():
        if not isinstance(spec, dict):
            continue
        node = by_key.get(str(key))
        if node is None:
            if not str(key).startswith("menu.custom."):
                continue
            node = {
                "key": str(key),
                "label": str(spec.get("label") or "未命名分组"),
                "group": rbac.GROUP_PAGE,
                "description": "自定义菜单分组",
                "parent": "",
                "order": int(spec.get("order") or 0),
                "kind": "group",
                "route": "",
                "source": "custom",
                "visible": True,
            }
            nodes.append(node)
            by_key[str(key)] = node
        if spec.get("label"):
            node["label"] = str(spec["label"])
        if spec.get("order") is not None:
            node["order"] = int(spec["order"])
    group_keys = {str(node["key"]) for node in nodes if node.get("kind") == "group"}
    for key, spec in pages.items():
        if not isinstance(spec, dict):
            continue
        node = by_key.get(str(key))
        if node is None or node.get("kind") != "permission":
            continue
        parent = spec.get("parent")
        parent = "" if parent is None else str(parent)
        if parent and parent not in group_keys:
            continue
        if spec.get("order") is not None:
            node["order"] = int(spec["order"])
        node["parent"] = parent
    return sorted(
        nodes, key=lambda n: (int(n.get("order") or 0), str(n.get("key") or ""))
    )


def menu_nodes(data_dir: Path) -> list[dict[str, Any]]:
    """静态菜单树 + 源码扩展页清单 + 动态分析菜单 + 运行时扩展页 + 人工覆盖。"""
    return _apply_overrides(_base_nodes(data_dir), load_overrides(data_dir))


def save_layout(data_dir: Path, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """保存页面设置里的分组/排序，写后端统一菜单树。

    items 是前端页面设置的顶层节点：
      {"type":"page","route":"/assistant"}
      {"type":"group","key":"menu.trading","label":"交易与复盘","children":[{"route":"..."}]}
    新分组 key 可为 `group:<uuid>`，落库为 `menu.custom.<slug>`。
    """
    nodes = _base_nodes(data_dir)
    by_route = {
        str(node["route"]): node for node in nodes
        if node.get("kind") == "permission" and node.get("route")
    }
    existing_groups = {
        str(node["key"]) for node in nodes if node.get("kind") == "group"
    }
    old = load_overrides(data_dir)
    old_groups = old.get("groups") if isinstance(old.get("groups"), dict) else {}
    groups: dict[str, dict[str, Any]] = {}
    pages: dict[str, dict[str, Any]] = {}
    seen_routes: set[str] = set()

    def page_key(item: dict[str, Any]) -> str:
        route = str(item.get("route") or "")
        if route in seen_routes:
            raise ValueError(f"页面重复: {route}")
        node = by_route.get(route)
        if node is None:
            raise ValueError(f"页面不存在或未注册: {route}")
        seen_routes.add(route)
        return str(node["key"])

    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError("菜单节点格式错误")
        kind = item.get("type")
        if kind == "page":
            pages[page_key(item)] = {"parent": "", "order": index * 10}
            continue
        if kind != "group":
            raise ValueError(f"未知菜单节点类型: {kind}")
        key = str(item.get("key") or "")
        label = str(item.get("label") or "").strip() or "未命名分组"
        if key.startswith("group:"):
            slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", key[6:]).strip("-") or "new"
            key = f"menu.custom.{slug}"
        elif key not in existing_groups and not key.startswith("menu.custom."):
            raise ValueError(f"分组不存在: {key}")
        groups[key] = {"label": label, "order": index * 10}
        children = item.get("children")
        if not isinstance(children, list):
            raise ValueError(f"分组 {label} 缺少 children")
        for child_index, child in enumerate(children):
            if not isinstance(child, dict) or child.get("type") != "page":
                raise ValueError(f"分组 {label} 只能包含页面")
            pages[page_key(child)] = {"parent": key, "order": child_index * 10}

    # 保留未提交的页面覆盖（例如未出现在页面设置中的扩展页），但丢弃已删除的自定义分组。
    for key, spec in (old.get("pages") or {}).items():
        if isinstance(spec, dict) and str(key) not in pages:
            node = next((n for n in nodes if n.get("key") == key), None)
            if node is not None and node.get("kind") == "permission":
                pages[str(key)] = {
                    "parent": spec.get("parent") or "",
                    "order": int(spec.get("order") or node.get("order") or 0),
                }
    for key in old_groups:
        if str(key).startswith("menu.custom.") and str(key) not in groups:
            continue

    data = {"version": 1, "groups": groups, "pages": pages}
    path = _overrides_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return menu_nodes(data_dir)


def permission_catalog(data_dir: Path) -> list[dict[str, Any]]:
    """权限叶子目录（不含纯分组节点），供权限组矩阵使用。"""
    return [node for node in menu_nodes(data_dir) if node.get("kind") == "permission"]


def dynamic_page_permission_keys(data_dir: Path) -> list[str]:
    """当前实际存在的扩展/分析页面权限码，用于旧 page.analysis 迁移。"""
    keys: list[str] = []
    for node in menu_nodes(data_dir):
        if node.get("kind") != "permission":
            continue
        key = str(node.get("key") or "")
        if key.startswith(rbac.PAGE_EXT_PREFIX) or key.startswith(rbac.PAGE_ANALYSIS_PREFIX):
            keys.append(key)
    return keys
