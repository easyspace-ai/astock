"""同步编排 — 拉取 → 归一 → 落盘 → 状态记录。

六个数据块独立软失败: profile/shareholders(巨潮前五大)/holders_top10(东财十大)/
executives(东财高管详情)/announcements(巨潮)/news(扶摇) 任一块失败只记录该块
错误状态, 其他块照常写入, 已有文件保留(fail-closed, 不跨块拼凑半数据)。
东财挂了巨潮前五大兜底、反之亦然——消费方按两份数据各自存在与否降级使用。
所有函数接收 DATA_DIR(运行数据目录), store 层负责拼 graph/ 子目录。
骨架期同步为请求内同步执行; 接入每日调度属于后续步骤。
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import store
from .client import CninfoClient, CninfoError
from .em_client import EastmoneyClient, EastmoneyError
from .news_client import FuyaoNewsClient, FuyaoNewsError, default_api_key

logger = logging.getLogger(__name__)

SYMBOL_RE = re.compile(r"^\d{6}\.(SH|SZ|BJ)$")

_ANN_DEFAULT_WINDOW_DAYS = 120
_ANN_MAX_PAGES = 8
_ANN_PAGE_SIZE = 30
_BJT = timezone(timedelta(hours=8))


def _expected_market(code: str) -> str | None:
    """6 位代码 → 应有交易所后缀; 未知段返回 None(不擅自拒绝)。"""
    if code.startswith(("60", "68")):
        return "SH"
    if code.startswith(("00", "30")):
        return "SZ"
    if code.startswith(("43", "83", "87", "88", "92")):
        return "BJ"
    return None


def normalize_symbol(value: str) -> str:
    """校验/规范化标准代码; 非法输入抛 ValueError。"""
    symbol = str(value or "").strip().upper()
    if not SYMBOL_RE.match(symbol):
        raise ValueError(f"非法标的代码: {value!r} (需形如 000019.SZ)")
    code, market = symbol.split(".", 1)
    expected = _expected_market(code)
    if expected and market != expected:
        raise ValueError(f"代码与交易所不匹配: {symbol} ({code} 应使用 .{expected})")
    return symbol


def code_of(symbol: str) -> str:
    """标准代码 → 巨潮裸 6 位代码。"""
    return symbol.split(".", 1)[0]


def column_of(symbol: str) -> str:
    """交易所 → 巨潮公告检索 column。上交所 sse; 深交所/北交所 szse。"""
    return "sse" if symbol.endswith(".SH") else "szse"


def _top5_from_research(research: dict) -> dict:
    """东财十大股东 → 巨潮前五大同形数据; HOLD_NUM 股 → shares_mn 百万股。"""
    rows = research.get("top10") or []
    holders = []
    for row in rows[:5]:
        shares = row.get("shares")
        holders.append(
            {
                "name": row.get("name"),
                "shares_mn": shares / 1_000_000 if isinstance(shares, (int, float)) else None,
                "holding_pct_percent": row.get("pct_percent"),
                "report_date": row.get("report_date"),
            }
        )
    if not holders:
        raise ValueError("东财十大股东为空, 无法生成前五大")
    report_date = max(
        (h["report_date"] for h in holders if h.get("report_date")), default=None
    )
    return {"report_date": report_date, "holders": holders}


def sync_company(
    data_dir: Path,
    symbol: str,
    *,
    client: CninfoClient | None = None,
    em_client: EastmoneyClient | None = None,
    news_client: FuyaoNewsClient | None = None,
    with_announcements: bool = True,
    with_news: bool = True,
    announcement_window_days: int = _ANN_DEFAULT_WINDOW_DAYS,
) -> dict:
    """同步一只公司的全部数据块。返回 {"symbol", "parts": {块名: {ok, at?, error?, rows?}}}。

    `client`/`em_client`/`news_client` 供测试注入; 未传的按需创建并在结束时关闭。
    news_client 未传时用 default_api_key() 建; Key 缺失 → news 块报错, 其他块不受影响。
    """
    symbol = normalize_symbol(symbol)
    data_dir = Path(data_dir)
    cn = client or CninfoClient()
    em = em_client or EastmoneyClient()
    own_news = news_client is None
    if news_client is None:
        try:
            news_client = FuyaoNewsClient(api_key=default_api_key())
        except FuyaoNewsError as e:
            news_client = None
            logger.info("cninfo sync %s news 跳过: %s", symbol, e)
    code = code_of(symbol)
    now_iso = datetime.now(_BJT).isoformat(timespec="seconds")
    holder_research: dict | None = None

    status: dict[str, dict] = {}

    def run_part(name: str, fn) -> None:
        try:
            extra = fn()
            status[name] = {"ok": True, "at": now_iso, **(extra or {})}
        except Exception as e:  # noqa: BLE001 — 分块软失败: 单块异常不影响其他块
            status[name] = {"ok": False, "error": str(e)}
            logger.warning("cninfo sync %s %s 失败: %s", symbol, name, e)

    def _holder_research() -> dict:
        """东财股东研究只拉一次: 前五大兜底与十大股东共用。"""
        nonlocal holder_research
        if holder_research is None:
            holder_research = em.fetch_holder_research(symbol)
        return holder_research

    try:
        # ---- 公司档案 (巨潮优先, 沪市接口常返回空 → 东财兜底) ----
        def _profile() -> dict:
            try:
                profile = cn.fetch_company_profile(code)
            except CninfoError as cn_err:
                logger.info("cninfo profile %s 不可用, 转东财: %s", symbol, cn_err)
                try:
                    profile = em.fetch_company_profile(symbol)
                except EastmoneyError as em_err:
                    raise CninfoError(
                        f"巨潮公司档案不可用({cn_err}); 东财兜底也失败({em_err})"
                    ) from em_err
            profile["symbol"] = symbol
            store.save_part(data_dir, symbol, store.PROFILE, profile)
            return {}

        run_part(store.PROFILE, _profile)

        # ---- 前五大股东 (巨潮优先; 沪市空响应时由东财十大取前五) ----
        def _top5() -> dict:
            try:
                holders = cn.fetch_top_shareholders(code)
                source = "cninfo"
            except CninfoError as cn_err:
                logger.info("cninfo 前五大 %s 不可用, 转东财: %s", symbol, cn_err)
                try:
                    holders = _top5_from_research(_holder_research())
                except EastmoneyError as em_err:
                    raise CninfoError(
                        f"巨潮前五大不可用({cn_err}); 东财兜底也失败({em_err})"
                    ) from em_err
                source = "eastmoney"
            store.save_part(data_dir, symbol, store.SHAREHOLDERS, holders)
            return {"rows": len(holders.get("holders") or []), "source": source}

        run_part(store.SHAREHOLDERS, _top5)

        # ---- 十大股东/户数/实控人 (东财) ----
        def _top10() -> dict:
            research = _holder_research()
            store.save_part(data_dir, symbol, store.TOP10, research)
            return {"rows": len(research.get("top10") or [])}

        run_part(store.TOP10, _top10)

        # ---- 高管详情 (东财) ----
        def _executives() -> dict:
            detail = em.fetch_executives(symbol)
            store.save_part(data_dir, symbol, store.EXECUTIVES, detail)
            return {"rows": len(detail.get("persons") or [])}

        run_part(store.EXECUTIVES, _executives)

        # ---- 公告列表 (巨潮) ----
        def _announcements() -> dict:
            org_id = _ensure_org_id(data_dir, symbol, cn)
            if not org_id:
                raise ValueError("orgId 解析失败: 巨潮未收录该代码")
            end = date.today()
            start = end - timedelta(days=announcement_window_days)
            anns = cn.fetch_announcements(
                code,
                org_id,
                column=column_of(symbol),
                se_date=f"{start.isoformat()}~{end.isoformat()}",
                max_pages=_ANN_MAX_PAGES,
                page_size=_ANN_PAGE_SIZE,
            )
            merged = store.merge_announcements(
                store.load_part(data_dir, symbol, store.ANNOUNCEMENTS), anns
            )
            store.save_part(data_dir, symbol, store.ANNOUNCEMENTS, merged)
            return {"rows": len(merged)}

        if with_announcements:
            run_part(store.ANNOUNCEMENTS, _announcements)

        # ---- 个股新闻 (扶摇) ----
        def _news() -> dict:
            if news_client is None:
                raise ValueError("fuyao api key 未配置, 新闻块跳过")
            items = news_client.fetch_latest(symbol)
            merged = store.merge_news(store.load_part(data_dir, symbol, store.NEWS), items)
            store.save_part(data_dir, symbol, store.NEWS, merged)
            return {"rows": len(merged)}

        if with_news:
            run_part(store.NEWS, _news)
    finally:
        if client is None:
            cn.close()
        if em_client is None:
            em.close()
        if own_news and news_client is not None:
            news_client.close()

    state_entry = store.update_symbol_state(data_dir, symbol, {"last_sync": now_iso, "parts": status})
    org_id = state_entry.get("org_id") or (store.load_part(data_dir, symbol, store.PROFILE) or {}).get("org_id")
    if org_id:
        store.update_symbol_state(data_dir, symbol, {"org_id": org_id})
    return {"symbol": symbol, "parts": status}


def _ensure_org_id(data_dir: Path, symbol: str, cli: CninfoClient) -> str | None:
    """orgId: 已存 profile / state 里找, 找不到再请求 topSearch 并写入 state。"""
    profile = store.load_part(data_dir, symbol, store.PROFILE) or {}
    org_id = profile.get("org_id")
    if org_id:
        return org_id
    state_entry = store.load_state(data_dir).get(symbol) or {}
    org_id = state_entry.get("org_id")
    if org_id:
        return org_id
    resolved = cli.resolve_org_id(code_of(symbol))
    if resolved:
        store.update_symbol_state(data_dir, symbol, {"org_id": resolved})
    return resolved


def overview(data_dir: Path) -> dict:
    """清单 + 同步状态 + 已存公司列表。"""
    return {
        "tracked": store.load_tracked(data_dir),
        "stored": store.list_companies(data_dir),
        "state": store.load_state(data_dir),
    }


def company_detail(data_dir: Path, symbol: str) -> dict | None:
    """聚合读取一只公司; 无档案时返回 None。"""
    symbol = normalize_symbol(symbol)
    detail = store.load_company(data_dir, symbol)
    if detail is None:
        return None
    detail["state"] = store.load_state(data_dir).get(symbol) or {}
    return detail
