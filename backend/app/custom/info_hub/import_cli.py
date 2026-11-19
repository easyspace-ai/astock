"""一次性导入 / 重建 CLI。

    cd backend
    python -m app.custom.info_hub.import_cli --legacy /path/to/legacy-data --data-dir /path/to/data

数据一次性导入后，TSP 的 <DATA_DIR>/info_hub 成为唯一生产源。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import announcements, config, news, store


def main() -> int:
    parser = argparse.ArgumentParser(description="导入公告/新闻/快讯历史数据并重建统一层")
    parser.add_argument("--legacy", required=True, help="包含 cninfo-crawler 与 stock-news 的父目录")
    parser.add_argument("--data-dir", required=True, help="TSP 的 DATA_DIR")
    parser.add_argument("--kinds", default="announcements,news,flash")
    parser.add_argument("--skip-import", action="store_true", help="只从已有 raw 重建")
    args = parser.parse_args()

    kinds = [k.strip() for k in args.kinds.split(",") if k.strip()]
    config.ensure_layout(args.data_dir)
    result: dict = {"kinds": kinds}
    if not args.skip_import:
        if "announcements" in kinds:
            result["announcements_import"] = announcements.import_legacy(args.data_dir, Path(args.legacy) / "cninfo-crawler/data")
        if "news" in kinds or "flash" in kinds:
            result["news_import"] = news.import_legacy(args.data_dir, Path(args.legacy) / "stock-news/data")
    if "announcements" in kinds:
        result["announcements"] = announcements.rebuild(args.data_dir)
    if "news" in kinds:
        result["news"] = news.rebuild(args.data_dir)
    if "flash" in kinds:
        result["flash"] = news.rebuild(args.data_dir)
    store.record_run(args.data_dir, {"action": "import", "ok": True, "summary": result})
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
