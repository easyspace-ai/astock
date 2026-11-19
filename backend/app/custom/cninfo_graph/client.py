"""巨潮资讯网 HTTP 客户端 — 公司档案 / 董监高 / 前五大股东 / 公告列表。

职责: 封装 HTTP、JSONP 解包与字段归一; 不知道存储层与 API 层。
接口口径(2026-11 实测, 全部无需登录):

- 公司快照页聚合: GET /data/yellowpages/getIndexData?jsonpCallback=<cb>&scode=<6位代码>&type=1
  返回 JSONP: {cninfo5025Data: 董监高, snapshot5015Data: 公司资料,
               cninfo5023Data: 业务概况/亮点/风险 + DECLAREDATE, codeInfo: 简称等}
- 数据块选择: GET /data/yellowpages/singleStockData?...&mergerMark=shareHoldersData → 前五大股东
- 代码→orgId: POST /new/information/topSearch/query (form: keyWord, maxNum)
- 公告检索:   POST /new/hisAnnouncement/query (form: stock="<code>,<orgId>", column=szse|sse, ...)
- PDF 直链:   https://static.cninfo.com.cn/<adjunctUrl>

单位与日期口径(与 CONTRIBUTING §3 精神一致, 字段名显式带单位):
- announcementTime 为北京时间零点 epoch ms(与 fuyao 的上海零点戳同口径), 统一换算北京日期。
- 股东 shares 单位为**百万股**(接口原始值), holding_pct 为**百分数**(63.79 = 63.79%)。
  本数据仅用于图谱展示与实体解析, 不参与金融计算, 故保留原单位不换算。
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://www.cninfo.com.cn"
STATIC_BASE_URL = "https://static.cninfo.com.cn"

_JSONP_RE = re.compile(r"^\s*[\w$.]+\((.*)\)\s*;?\s*$", re.DOTALL)
_TITLE_TAG_RE = re.compile(r"</?em>")
_BJT = timezone(timedelta(hours=8))  # 北京时间; 接口时间戳均为北京零点口径

_REQUEST_INTERVAL_S = 0.3  # 巨潮有频控, 连续请求间保守间隔


class CninfoError(Exception):
    """巨潮接口错误(网络失败 / 响应结构不可解析)。"""


def beijing_date_of_ms(ms: int | float | None) -> date | None:
    """北京零点 epoch ms → 北京日期; 非法输入返回 None(不伪造)。"""
    try:
        ts = float(ms)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if ts <= 0:
        return None
    return datetime.fromtimestamp(ts / 1000.0, tz=_BJT).date()


def _unwrap_jsonp(text: str) -> Any:
    """解包 JSONP (jQueryxxx({...})) 或直接解析纯 JSON。"""
    m = _JSONP_RE.match(text)
    payload = m.group(1) if m else text
    try:
        return json.loads(payload)
    except ValueError as e:
        raise CninfoError(f"响应不是合法 JSON/JSONP: {e}") from e


class CninfoClient:
    """巨潮 REST 客户端 (支持 with 上下文与显式 close)。"""

    def __init__(self, timeout: float = 20.0, transport: httpx.BaseTransport | None = None) -> None:
        self._http = httpx.Client(
            base_url=BASE_URL,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
                ),
                "Referer": f"{BASE_URL}/",
            },
            timeout=timeout,
            transport=transport,
        )
        self._last_request_ts = 0.0

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> CninfoClient:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---- 内部 ----
    def _throttle(self) -> None:
        now = time.monotonic()
        wait = self._last_request_ts + _REQUEST_INTERVAL_S - now
        if wait > 0:
            time.sleep(wait)
        self._last_request_ts = time.monotonic()

    def _get_jsonp(self, path: str, params: dict) -> Any:
        self._throttle()
        try:
            resp = self._http.get(path, params=params)
        except httpx.HTTPError as e:
            raise CninfoError(f"网络请求失败: {e}") from e
        if resp.status_code != 200:
            raise CninfoError(f"HTTP {resp.status_code}: {path}")
        return _unwrap_jsonp(resp.text)

    def _post_form(self, path: str, data: dict) -> Any:
        self._throttle()
        try:
            resp = self._http.post(path, data=data)
        except httpx.HTTPError as e:
            raise CninfoError(f"网络请求失败: {e}") from e
        if resp.status_code != 200:
            raise CninfoError(f"HTTP {resp.status_code}: {path}")
        try:
            return resp.json()
        except ValueError as e:
            raise CninfoError(f"响应不是 JSON: {path}") from e

    # ---- 代码 → orgId ----
    def resolve_org_id(self, code: str) -> str | None:
        """6 位代码 → 巨潮 orgId。取 A 股类别的第一条; 找不到返回 None。"""
        payload = self._post_form("/new/information/topSearch/query", {"keyWord": code, "maxNum": 10})
        rows = payload if isinstance(payload, list) else (payload.get("data") or [])
        for row in rows:
            if not isinstance(row, dict):
                continue
            if str(row.get("code") or "") != code:
                continue
            category = str(row.get("category") or "")
            if "A股" in category or not category:
                return str(row.get("orgId") or "") or None
        return None

    # ---- 公司档案 ----
    def fetch_company_profile(self, code: str) -> dict:
        """公司档案归一: 公司资料 + 业务概况/亮点/风险 + 董监高。

        响应结构整体缺失时抛 CninfoError(不做部分数据的静默拼凑)。
        """
        payload = self._get_jsonp(
            "/data/yellowpages/getIndexData", {"jsonpCallback": "cb", "scode": code, "type": 1}
        )
        if not isinstance(payload, dict) or not payload:
            raise CninfoError("getIndexData 响应为空或结构异常")

        info_rows = payload.get("snapshot5015Data") or []
        if not info_rows or not isinstance(info_rows, list):
            raise CninfoError("snapshot5015Data 缺失: 公司资料不可用")
        info = info_rows[0] if isinstance(info_rows[0], dict) else {}

        text = (payload.get("cninfo5023Data") or [{}])[0] if isinstance(payload.get("cninfo5023Data"), list) else {}
        exec_rows = payload.get("cninfo5025Data") or []

        profile: dict[str, Any] = {
            "code": code,
            "org_id": None,
            "name": info.get("ORGNAME") or None,
            "short_name": (payload.get("codeInfo") or {}).get("SECNAME") or None,
            "isin": info.get("F001V") or None,
            "founded_date": info.get("F002V") or None,
            "listed_date": info.get("F003V") or None,
            "website": info.get("F004V") or None,
            "reg_address": info.get("F005V") or None,
            "office_address": info.get("F006V") or None,
            "email": info.get("F007V") or None,
            "phone": info.get("F008V") or None,
            "fax": info.get("F009V") or None,
            "industry_l1": info.get("F010V") or None,
            "industry_l2": info.get("F011V") or None,
            "market": info.get("F012V") or None,
            "declare_date": text.get("DECLAREDATE") or None,
            "business_summary": text.get("F001V") or None,
            "highlights": text.get("F002V") or None,
            "risks": text.get("F003V") or None,
            "executives": [],
            "board_members": [],
            "fetched_at": datetime.now(_BJT).isoformat(timespec="seconds"),
        }

        # 董监高: F001V=董事长 F002V=总经理 F003V=财务总监 F004V=董秘 (空值角色跳过)
        role_fields = (("董事长", "F001V"), ("总经理", "F002V"), ("财务总监", "F003V"), ("董事会秘书", "F004V"))
        for row in exec_rows:
            if not isinstance(row, dict):
                continue
            for role, field in role_fields:
                name = str(row.get(field) or "").strip()
                if name:
                    profile["executives"].append({"role": role, "name": name})
            members = str(row.get("F005V") or "").strip()
            if members:
                for name in re.split("[,，、;；]", members):
                    name = name.strip()
                    if name and name not in profile["board_members"]:
                        profile["board_members"].append(name)

        # 声明记录行数, 供结构变化告警 (不做启发式补全)
        if not profile["name"]:
            logger.warning("cninfo profile: %s 公司名为空, 快照结构可能已变化", code)
        return profile

    # ---- 前五大股东 ----
    def fetch_top_shareholders(self, code: str) -> dict:
        """前五大股东。shares 单位百万股, holding_pct 单位百分数(63.79 = 63.79%)。"""
        payload = self._get_jsonp(
            "/data/yellowpages/singleStockData",
            {"jsonpCallback": "cb", "scode": code, "sign": 1, "type": 1, "mergerMark": "shareHoldersData"},
        )
        rows = payload.get("shareHoldersData") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise CninfoError("shareHoldersData 缺失: 股东数据不可用")
        holders = []
        report_dates: list[str] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = str(row.get("F002V") or "").strip()
            if not name:
                continue
            report_date = str(row.get("F001D") or "") or None
            if report_date:
                report_dates.append(report_date)
            holders.append(
                {
                    "name": name,
                    "shares_mn": _optional_float(row.get("F003N")),  # 百万股
                    "holding_pct_percent": _optional_float(row.get("F004N")),  # 百分数
                    "report_date": report_date,
                }
            )
        report_date = max(report_dates) if report_dates else None  # 同批取最晚截止日
        return {"report_date": report_date, "holders": holders}

    # ---- 公告列表 ----
    def fetch_announcements(
        self,
        code: str,
        org_id: str,
        *,
        column: str = "szse",
        se_date: str | None = None,
        max_pages: int = 8,
        page_size: int = 30,
    ) -> list[dict]:
        """公告检索(带分页上限)。返回归一列表, 日期为北京日期 ISO 字符串。

        column: 深交所/北交所为 szse, 上交所为 sse; se_date 形如 "2026-08-01~2026-11-15"。
        """
        out: list[dict] = []
        seen_ids: set[str] = set()
        total = None
        for page in range(1, max(max_pages, 1) + 1):
            data = self._post_form(
                "/new/hisAnnouncement/query",
                {
                    "pageNum": page,
                    "pageSize": page_size,
                    "column": column,
                    "tabName": "fulltext",
                    "plate": "",
                    "stock": f"{code},{org_id}",
                    "searchkey": "",
                    "secid": "",
                    "category": "",
                    "trade": "",
                    "seDate": se_date or "",
                    "sortName": "",
                    "sortType": "",
                    "isHLtitle": "true",
                },
            )
            rows = data.get("announcements") or []
            if total is None:
                try:
                    total = int(data.get("totalAnnouncement") or 0)
                except (TypeError, ValueError):
                    total = 0
            if not rows:
                break
            for row in rows:
                if not isinstance(row, dict):
                    continue
                ann_id = str(row.get("announcementId") or "") or None
                if ann_id and ann_id in seen_ids:
                    continue
                if ann_id:
                    seen_ids.add(ann_id)
                adjunct = str(row.get("adjunctUrl") or "") or None
                out.append(
                    {
                        "announcement_id": ann_id,
                        "title": _TITLE_TAG_RE.sub("", str(row.get("announcementTitle") or "")) or None,
                        "date": beijing_date_of_ms(row.get("announcementTime")).isoformat()
                        if beijing_date_of_ms(row.get("announcementTime"))
                        else None,
                        "pdf_url": f"{STATIC_BASE_URL}/{adjunct}" if adjunct else None,
                        "column_ids": [p for p in str(row.get("columnId") or "").split("||") if p],
                        "types": [p for p in str(row.get("announcementType") or "").split("||") if p],
                    }
                )
            if total and len(out) >= total:
                break
        return out


def _optional_float(value: Any) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
