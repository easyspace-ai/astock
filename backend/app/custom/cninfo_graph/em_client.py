"""东方财富 HTTP 客户端 — 十大股东 / 股东户数 / 实控人 / 董监高详情。

调研结论(2026-11 实测, 均无需登录):
- 股东研究聚合: GET https://emweb.securities.eastmoney.com/PC_HSF10/ShareholderResearch/PageAjax?code=<SZ|SH|BJ><6位>
  返回 JSON: {sdgd: 十大股东, sdltgd: 十大流通股东, gdrs: 股东户数历史,
              sjkzr: 实际控制人, sdgd_date: 报告期列表, ...}
- 董监高详情: GET https://datacenter.eastmoney.com/securities/api/data/v1/get
              ?reportName=RPT_F10_ORGINFO_MANAINTRO&columns=ALL&filter=(SECUCODE="<code.SZ>")
              &pageNumber=1&pageSize=100&source=SECURITIES&client=PC
  返回信封 {version, result: {data: [...]}, success} — 含完整简历/薪酬/持股/年龄学历,
  PERSON_CODE 为全局人标识, 可跨公司串联同一自然人(图谱人层的边)。

单位与日期口径(字段名显式带单位):
- 十大股东 HOLD_NUM 为**股**(原值, 与巨潮前五大的百万股不同); HOLD_NUM_RATIO 为百分数。
- HOLD_NUM_CHANGE 可能是数值或"不变"/"新进"等文本 → 数值入 shares_change/change_pct,
  非数值原文保留在 change_note(不丢弃、不猜测)。
- END_DATE/INCUMBENT_DATE 形如 "2026-09-30 00:00:00" → 取日期部分。
- SALARY 为**元/年**(原值)。
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from .client import _optional_float

logger = logging.getLogger(__name__)

EMWEB_BASE_URL = "https://emweb.securities.eastmoney.com"
DATACENTER_BASE_URL = "https://datacenter.eastmoney.com"

_SHAREHOLDER_PATH = "/PC_HSF10/ShareholderResearch/PageAjax"
_SURVEY_PATH = "/PC_HSF10/CompanySurvey/PageAjax"
_EXEC_PATH = "/securities/api/data/v1/get"

_REQUEST_INTERVAL_S = 0.2
_BJT = timezone(timedelta(hours=8))


class EastmoneyError(Exception):
    """东方财富接口错误(网络失败 / 响应结构不可解析)。"""


def date_of_ts(value: str | None) -> str | None:
    """"2026-09-30 00:00:00" → "2026-09-30"; 其他形状返回 None。"""
    if not value:
        return None
    text = str(value).strip()
    return text[:10] if len(text) >= 10 else None


def em_code(symbol: str) -> str:
    """标准代码 601595.SH → 东财 F10 入参 SH601595。"""
    return f"{symbol.split('.')[1]}{symbol.split('.')[0]}"


def _change_pair(raw: Any) -> tuple[float | None, str | None]:
    """HOLD_NUM_CHANGE 宽容解析: 数值 → (值, None); 文本 → (None, 原文)。"""
    num = _optional_float(raw)
    if num is not None:
        return num, None
    text = str(raw or "").strip()
    return (None, text) if text else (None, None)


def _flag(value: Any) -> bool:
    return str(value or "").strip() == "1"


class EastmoneyClient:
    """东财 REST 客户端。`transport` 供测试注入 (httpx.MockTransport)。"""

    def __init__(self, timeout: float = 20.0, transport: httpx.BaseTransport | None = None) -> None:
        self._http = httpx.Client(
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
                ),
                "Referer": f"{EMWEB_BASE_URL}/",
            },
            timeout=timeout,
            transport=transport,
        )
        self._last_request_ts = 0.0

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> EastmoneyClient:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _throttle(self) -> None:
        now = time.monotonic()
        wait = self._last_request_ts + _REQUEST_INTERVAL_S - now
        if wait > 0:
            time.sleep(wait)
        self._last_request_ts = time.monotonic()

    def _get_json(self, url: str, params: dict) -> Any:
        self._throttle()
        try:
            resp = self._http.get(url, params=params)
        except httpx.HTTPError as e:
            raise EastmoneyError(f"网络请求失败: {e}") from e
        if resp.status_code != 200:
            raise EastmoneyError(f"HTTP {resp.status_code}: {url}")
        try:
            return resp.json()
        except ValueError as e:
            raise EastmoneyError(f"响应不是 JSON: {url}") from e

    # ---- 公司档案 (巨潮沪市接口返回空时兜底) ----
    def fetch_company_profile(self, symbol: str) -> dict:
        """标准代码 → 公司档案归一; 字段口径与巨潮 profile 对齐, 缺失项留 null。"""
        payload = self._get_json(
            f"{EMWEB_BASE_URL}{_SURVEY_PATH}", {"code": em_code(symbol)}
        )
        if not isinstance(payload, dict) or not payload:
            raise EastmoneyError("CompanySurvey 响应为空或结构异常")
        rows = payload.get("jbzl") or []
        if not rows or not isinstance(rows[0], dict):
            raise EastmoneyError("jbzl 缺失: 公司档案不可用")
        info = rows[0]

        issue_rows = payload.get("fxxg") or []
        issue = issue_rows[0] if issue_rows and isinstance(issue_rows[0], dict) else {}

        industry = str(info.get("INDUSTRYCSRC1") or "").strip()
        industry_parts = industry.split("-", 1)
        industry_l1 = industry_parts[0] or None
        industry_l2 = industry_parts[1] if len(industry_parts) > 1 else None

        executives = []
        for role, field in (("董事长", "CHAIRMAN"), ("总经理", "PRESIDENT"), ("董事会秘书", "SECRETARY")):
            name = str(info.get(field) or "").strip()
            if name:
                executives.append({"role": role, "name": name})

        return {
            "code": symbol.split(".")[0],
            "org_id": None,
            "name": str(info.get("ORG_NAME") or "").strip() or None,
            "short_name": str(info.get("SECURITY_NAME_ABBR") or "").strip() or None,
            "isin": None,
            "founded_date": date_of_ts(issue.get("FOUND_DATE")),
            "listed_date": date_of_ts(issue.get("LISTING_DATE")),
            "website": str(info.get("ORG_WEB") or "").strip() or None,
            "reg_address": str(info.get("REG_ADDRESS") or "").strip() or None,
            "office_address": str(info.get("ADDRESS") or "").strip() or None,
            "email": str(info.get("ORG_EMAIL") or "").strip() or None,
            "phone": str(info.get("ORG_TEL") or "").strip() or None,
            "fax": str(info.get("ORG_FAX") or "").strip() or None,
            "industry_l1": industry_l1,
            "industry_l2": industry_l2,
            "market": str(info.get("SECURITY_TYPE") or "").strip() or None,
            "declare_date": None,
            "business_summary": str(info.get("ORG_PROFILE") or "").strip() or None,
            "highlights": None,
            "risks": None,
            "executives": executives,
            "board_members": [],
            "fetched_at": datetime.now(_BJT).isoformat(timespec="seconds"),
        }

    # ---- 股东研究聚合 (一次请求覆盖十大股东/十大流通/股东户数/实控人) ----
    def fetch_holder_research(self, symbol: str) -> dict:
        """标准代码(000019.SZ) → 股东研究聚合归一。shares 单位为**股**。"""
        code = f"{symbol.split('.')[1]}{symbol.split('.')[0]}"  # 000019.SZ → SZ000019
        payload = self._get_json(f"{EMWEB_BASE_URL}{_SHAREHOLDER_PATH}", {"code": code})
        if not isinstance(payload, dict) or not payload:
            raise EastmoneyError("PageAjax 响应为空或结构异常")

        top10 = self._holder_rows(payload.get("sdgd"), "HOLD_NUM_RATIO")
        if not top10:
            raise EastmoneyError("sdgd 缺失: 十大股东不可用")
        top10_float = self._holder_rows(payload.get("sdltgd"), "FREE_HOLDNUM_RATIO", float_only=True)

        report_dates = sorted(
            {r["report_date"] for r in top10 if r.get("report_date")}, reverse=True
        )
        result: dict[str, Any] = {
            "source": "eastmoney",
            "report_date": report_dates[0] if report_dates else None,
            "top10": top10,
            "top10_float": top10_float,
            "holder_count_history": self._gdrs_rows(payload.get("gdrs")),
            "controller": self._controller(payload.get("sjkzr")),
            "fetched_at": datetime.now(_BJT).isoformat(timespec="seconds"),
        }
        return result

    def _holder_rows(self, rows: Any, pct_field: str, *, float_only: bool = False) -> list[dict]:
        if not isinstance(rows, list):
            return []
        out: list[dict] = []
        for row in rows:
            if not isinstance(row, dict) or not str(row.get("HOLDER_NAME") or "").strip():
                continue
            change, note = _change_pair(row.get("HOLD_NUM_CHANGE"))
            item: dict[str, Any] = {
                "rank": row.get("HOLDER_RANK"),
                "name": str(row["HOLDER_NAME"]).strip(),
                "shares_type": str(row.get("SHARES_TYPE") or "") or None,
                "shares": _optional_float(row.get("HOLD_NUM")),  # 股
                "change_shares": change,
                "change_note": note,
                "change_pct": _optional_float(row.get("CHANGE_RATIO")),
                "report_date": date_of_ts(row.get("END_DATE")),
            }
            pct = _optional_float(row.get(pct_field))
            if float_only:
                item["float_pct_percent"] = pct  # 占流通股比例
            else:
                item["pct_percent"] = pct  # 占总股本比例
            out.append(item)
        out.sort(key=lambda r: r["rank"] if isinstance(r.get("rank"), (int, float)) else 99)
        return out

    def _gdrs_rows(self, rows: Any) -> list[dict]:
        """股东户数历史(最新在前, 保留 ≤10 行)。"""
        if not isinstance(rows, list):
            return []
        out = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            out.append(
                {
                    "end_date": date_of_ts(row.get("END_DATE")),
                    "holder_total_num": row.get("HOLDER_TOTAL_NUM"),  # 户
                    "avg_free_shares": _optional_float(row.get("AVG_FREE_SHARES")),  # 股/户
                    "hold_focus": row.get("HOLD_FOCUS") or None,
                    "avg_hold_amt_yuan": _optional_float(row.get("AVG_HOLD_AMT")),  # 元/户
                }
            )
        return out[:10]

    def _controller(self, rows: Any) -> dict | None:
        if not isinstance(rows, list) or not rows:
            return None
        row = rows[0] if isinstance(rows[0], dict) else None
        if not row or not str(row.get("HOLDER_NAME") or "").strip():
            return None
        return {
            "name": str(row["HOLDER_NAME"]).strip(),
            "pct_percent": _optional_float(row.get("HOLD_RATIO")),
        }

    # ---- 董监高详情 ----
    def fetch_executives(self, symbol: str) -> dict:
        """标准代码 → 董监高详情(取最新 REPORT_DATE 一期)。

        salary 单位**元/年**; hold_num 单位**股**; hold_ratio 百分数。
        """
        params = {
            "reportName": "RPT_F10_ORGINFO_MANAINTRO",
            "columns": "ALL",
            "filter": f'(SECUCODE="{symbol}")',
            "pageNumber": 1,
            "pageSize": 100,
            "source": "SECURITIES",
            "client": "PC",
        }
        payload = self._get_json(f"{DATACENTER_BASE_URL}{_EXEC_PATH}", params)
        rows = (payload or {}).get("result", {}).get("data") or []
        rows = [r for r in rows if isinstance(r, dict)]
        if not rows:
            raise EastmoneyError("MANAINTRO 无数据: 高管详情不可用")

        latest = max(date_of_ts(r.get("REPORT_DATE")) or "" for r in rows)
        persons = []
        for row in rows:
            if date_of_ts(row.get("REPORT_DATE")) != latest:
                continue
            name = str(row.get("PERSON_NAME") or "").strip()
            if not name:
                continue
            persons.append(
                {
                    "person_code": str(row.get("PERSON_CODE") or "") or None,
                    "name": name,
                    "position": str(row.get("POSITION") or "") or None,
                    "position_rank": row.get("POSITION_RANK"),
                    "sex": row.get("SEX") or None,
                    "age": row.get("AGE") or None,
                    "degree": row.get("HIGH_DEGREE") or None,
                    "salary_yuan": _optional_float(row.get("SALARY")),
                    "hold_num": _optional_float(row.get("HOLD_NUM")),  # 股
                    "hold_ratio_percent": _optional_float(row.get("HOLD_RATIO")),
                    "incumbent_date": date_of_ts(row.get("INCUMBENT_DATE")),
                    "incumbent_time": row.get("INCUMBENT_TIME") or None,
                    "is_dsh": _flag(row.get("IS_DSH")),  # 董事会
                    "is_jsh": _flag(row.get("IS_JSH")),  # 监事会
                    "is_glc": _flag(row.get("IS_GLC")),  # 管理层
                    "resume": str(row.get("RESUME") or "") or None,
                }
            )
        persons.sort(key=lambda p: p["position_rank"] if isinstance(p["position_rank"], (int, float)) else 99)
        return {
            "source": "eastmoney",
            "report_date": latest or None,
            "persons": persons,
            "fetched_at": datetime.now(_BJT).isoformat(timespec="seconds"),
        }
