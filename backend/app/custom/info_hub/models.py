"""公告 / 新闻 / 快讯统一事件模型。

只做字段归一与跨源合并，不读写磁盘，不发起网络请求。
时间统一北京时间；代码对外同时保留 6 位 code 与带交易所后缀的 symbol。
"""
from __future__ import annotations

import difflib
import hashlib
import re
import unicodedata
from datetime import datetime, timedelta
from typing import Any

EM_TYPE_MAP = {
    "回购预案": "回购", "回购进展": "回购",
    "增减持": "增减持", "股东增减持": "增减持",
    "股权质押": "股权质押", "股份质押、冻结": "股权质押", "质押、冻结": "股权质押",
    "业绩预告": "业绩预告", "业绩快报": "业绩预告", "月度经营情况": "业绩预告",
    "定期报告": "定期报告", "年报": "定期报告", "半年报": "定期报告", "季报": "定期报告",
    "三季度报告全文": "定期报告", "年度报告全文": "定期报告", "半年度报告全文": "定期报告",
    "分红送配": "分红送转", "权益分派": "分红送转", "利润分配": "分红送转",
    "股东大会决议公告": "例行公告", "董事会决议公告": "例行公告", "监事会决议公告": "例行公告",
    "召开股东大会通知": "例行公告", "公司章程": "例行公告", "公司章程修订": "例行公告",
    "法律意见书": "例行公告", "保荐/核查意见": "例行公告", "专项说明/独立意见": "例行公告",
    "审计报告": "例行公告", "调研活动": "例行公告", "投资者关系活动": "例行公告",
    "高管人员任职变动": "人事变动", "人事变动": "人事变动", "法定代表人变更": "人事变动",
    "重组": "重组并购", "重大资产重组": "重组并购", "收购出售资产": "重组并购",
    "股权转让": "股权变动", "权益变动报告书": "股权变动", "要约收购": "股权变动",
    "增发": "融资事项", "增发预案": "融资事项", "增发提示性公告": "融资事项",
    "其他增发事项公告": "融资事项", "可转债": "融资事项", "配股": "融资事项",
    "募集资金使用情况报告": "融资事项", "上市保荐书": "融资事项", "上市公告书": "融资事项",
    "对外担保": "对外担保", "提供/对外担保公告": "对外担保",
    "对外投资": "对外投资", "设立子公司": "对外投资",
    "重大合同": "合同业务", "签订协议": "合同业务", "中标": "合同业务",
    "诉讼仲裁": "风险事项", "处罚": "风险事项", "立案调查": "风险事项",
    "监管措施": "风险事项", "风险警示": "风险警示", "退市风险警示": "风险警示",
    "停牌": "停复牌", "复牌": "停复牌",
    "股权激励": "股权激励", "员工持股计划": "股权激励",
    "减值": "减值事项", "商誉减值": "减值事项",
    "问询函": "监管问询", "关注函": "监管问询", "澄清公告": "监管问询",
    "更正公告": "例行公告", "补充公告": "例行公告",
}

DOMAIN_MAP = {
    "定期报告": "finance", "业绩预告": "finance", "分红送转": "finance", "减值事项": "finance",
    "重组并购": "biz", "合同业务": "biz", "对外投资": "biz", "对外担保": "biz",
    "融资事项": "biz", "股权变动": "biz", "增减持": "biz", "回购": "biz",
    "股权质押": "biz", "股权激励": "biz",
    "风险事项": "legal", "风险警示": "legal", "监管问询": "legal",
}

IMPORTANT_PATTERNS = [
    (r"业绩预告|业绩快报|业绩预增|业绩预减|业绩预亏", "业绩预告"),
    (r"半年度报告|年度报告|第一季度报告|第三季度报告|半年报|年度报|一季报|三季报", "定期报告"),
    (r"利润分配|分红|送转|权益分派|派息|现金分红", "分红送转"),
    (r"减持|增持", "增减持"),
    (r"回购", "回购"),
    (r"质押|解除质押|补充质押", "股权质押"),
    (r"重组|重大资产|吸收合并|收购|出售资产|资产置换", "重组并购"),
    (r"重大合同|中标|战略合作|框架协议|签订.{0,6}协议|重大订单", "合同业务"),
    (r"立案|处罚|警示函|监管函|行政监管措施|纪律处分|诉讼|仲裁|冻结", "风险事项"),
    (r"退市风险|退市整理|摘帽|撤销风险警示|实施其他风险警示", "风险警示"),
    (r"非公开发行|向特定对象发行|定向增发|可转债|配股|发行股份|募集资金使用", "融资事项"),
    (r"停牌|复牌", "停复牌"),
    (r"要约收购|控制权|实际控制人|权益变动|股份转让|股权转让", "股权变动"),
    (r"对外投资|设立.{0,4}公司|增资|参股", "对外投资"),
    (r"担保", "对外担保"),
    (r"减值|商誉", "减值事项"),
    (r"股权激励|员工持股", "股权激励"),
    (r"澄清|说明公告|问询函|关注函|回复函", "监管问询"),
]
CATEGORY_PATTERNS = [(r"董事长|总经理|辞职|换届|高管.{0,4}变动", "人事变动")]
NOISE_PATTERNS = [
    r"会议通知|会议资料|会议议程|会议决议",
    r"投资者关系|业绩说明会|集体接待|网上接待",
    r"公司章程|制度|议事规则|管理办法|实施细则|工作细则",
    r"募集资金.*理财|现金管理|闲置募集资金|理财产品",
    r"审计报告|内部控制|鉴证报告|专项说明|专项核查|审核报告",
    r"保荐|持续督导|跟踪报告|核查意见|受托管理事务报告",
    r"法律意见书|独立董事|独立意见|事前认可意见",
    r"上市公告书|发行结果|中签率|中签|路演|招股说明书|募集说明书|发行保荐",
    r"更正公告|补充公告",
]
EXCLUDE_PATTERNS = [r"符合.{0,60}(?:规定|规则|办法|要求)", r"不构成.{0,30}"]

_TITLE_PREFIX = re.compile(r"^[^:：]{1,12}[:：]\s*")
_VERSION_PAREN = re.compile(r"[（(]\s*20\d{2}\s*[年\-/]\s*\d{1,2}\s*[月\-/]?\s*\d{0,2}\s*[日]?\s*[）)]")
_PUNCT = re.compile(r"[\s\u3000·,，。、；;：:!！?？\"“”'‘’《》<>()（）\[\]【】\-—_/+＋*＊#＃|｜]")
_TITLE_PREFIX_NEWS = re.compile(r"^[^:：]{1,12}[:：]\s*")
_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})(?:[ T](\d{2}:\d{2})(?::(\d{2}))?)?")


def norm_title(title: str) -> str:
    t = unicodedata.normalize("NFKC", (title or "").strip())
    for _ in range(3):
        stripped = _TITLE_PREFIX.sub("", t)
        if stripped == t:
            break
        t = stripped
    t = _VERSION_PAREN.sub("", t)
    t = re.sub(r"的公告$", "", t)
    t = _PUNCT.sub("", t)
    return t.lower()


def norm_news_title(title: str) -> str:
    t = unicodedata.normalize("NFKC", (title or "").strip()).lower()
    for _ in range(3):
        stripped = _TITLE_PREFIX_NEWS.sub("", t)
        if stripped == t:
            break
        t = stripped
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", t)


def title_sim(a: str, b: str) -> float:
    ka, kb = norm_news_title(a), norm_news_title(b)
    if not ka or not kb:
        return 0.0
    if ka == kb:
        return 1.0
    return difflib.SequenceMatcher(None, ka, kb).ratio()


def classify(title: str) -> tuple[str, bool]:
    title = title or ""
    for pat in EXCLUDE_PATTERNS:
        if re.search(pat, title):
            return "例行公告", False
    for pat, name in IMPORTANT_PATTERNS:
        if re.search(pat, title):
            return name, True
    for pat, name in CATEGORY_PATTERNS:
        if re.search(pat, title):
            return name, False
    for pat in NOISE_PATTERNS:
        if re.search(pat, title):
            return "例行公告", False
    return "其他", False


def em_type(column_name: str, title: str) -> str:
    if column_name in EM_TYPE_MAP:
        return EM_TYPE_MAP[column_name]
    if column_name:
        for key, label in EM_TYPE_MAP.items():
            if key in column_name:
                return label
    return classify(title)[0]


def market_of(code: str) -> str:
    code = str(code).zfill(6)
    if code.startswith("920"):
        return "bj"
    if code.startswith("6"):
        return "sh"
    if code.startswith(("0", "3", "2")):
        return "sz"
    if code.startswith(("4", "8")):
        return "bj"
    return "other"


def symbol_of(code: str) -> str:
    code = str(code).zfill(6)
    market = market_of(code)
    suffix = {"sh": "SH", "sz": "SZ", "bj": "BJ"}.get(market)
    return f"{code}.{suffix}" if suffix else code


def symbol_from_symbol(symbol: str) -> str:
    return symbol.split(".")[0].zfill(6) if symbol else ""


def normalize_symbol(raw: str) -> str:
    text = str(raw or "").strip().upper()
    if not text:
        raise ValueError("代码不能为空")
    if "." in text:
        code, suffix = text.split(".", 1)
    else:
        code, suffix = text, ""
    code = code.zfill(6)
    if not code.isdigit() or len(code) != 6:
        raise ValueError(f"非法代码: {raw}")
    expected = market_of(code)
    suffix_map = {"sh": "SH", "sz": "SZ", "bj": "BJ"}
    if suffix and suffix != suffix_map.get(expected, suffix):
        raise ValueError(f"交易所后缀不匹配: {raw}")
    return f"{code}.{suffix_map.get(expected, '')}" if expected in suffix_map else code


def fingerprint(code: str, norm: str, biz_date: str) -> str:
    return hashlib.sha1(f"{code}|{norm}|{biz_date}".encode()).hexdigest()[:16]


def session_of(hhmm: str | None) -> str:
    if not hhmm:
        return "unknown"
    try:
        h, m = hhmm.split(":")[:2]
        minutes = int(h) * 60 + int(m)
    except (ValueError, AttributeError):
        return "unknown"
    if minutes < 9 * 60 + 30:
        return "pre"
    if minutes <= 15 * 60:
        return "open"
    return "post"


def is_correction(title: str) -> bool:
    return bool(re.search(r"更正|补充更正|修订版|废止|撤销", title or ""))


def parse_datetime(value: Any) -> tuple[str, str, str]:
    """源时间字符串 -> (publish_time, publish_date, precision)。"""
    text = str(value or "").strip()
    m = _DATE_RE.search(text)
    if not m:
        return "", "", "unknown"
    date = m.group(1)
    time = m.group(2) or ""
    sec = m.group(3) or ""
    if time:
        return f"{date}T{time}:{sec or '00'}", date, "sec" if sec else "min"
    return "", date, "day"


def ann_from_cninfo(row: dict[str, Any], industry: str | None = None) -> dict[str, Any]:
    title = str(row.get("title") or "").strip()
    norm = norm_title(title)
    code = str(row.get("sec_code") or "").zfill(6)
    ann_date = str(row.get("ann_date") or "")[:10]
    ann_time = str(row.get("ann_time") or "") or None
    if ann_time in ("00:00", "None"):
        ann_time = None
    precision = "min" if ann_time else "day"
    disclosed_at = f"{ann_date}T{ann_time}:00" if ann_time and ann_date else None
    biz_date = ann_date
    type_primary, is_material = classify(title)
    ann_id = str(row.get("announcement_id") or "")
    event_id = f"ann:{symbol_of(code)}:{biz_date}:{fingerprint(code, norm, biz_date)}"
    return {
        "event_id": event_id,
        "event_kind": "announcement",
        "fp": fingerprint(code, norm, biz_date),
        "ann_id": ann_id,
        "ids": {"cninfo": ann_id},
        "source": "cninfo",
        "sources": ["cninfo"],
        "match_status": "cninfo_only",
        "match_score": 0.0,
        "match_note": "",
        "symbol": symbol_of(code),
        "code": code,
        "name": str(row.get("sec_name") or ""),
        "org_id": str(row.get("org_id") or ""),
        "market": str(row.get("market") or market_of(code)),
        "title": title,
        "title_norm": norm,
        "ann_date": ann_date,
        "ann_time": ann_time,
        "disclosed_at": disclosed_at,
        "time_precision": precision,
        "biz_date": biz_date,
        "session": session_of(ann_time),
        "type_primary": type_primary,
        "type_source": str(row.get("ann_type_raw") or row.get("category_name") or ""),
        "type_codes": row.get("ann_type_codes") or [],
        "type_domain": DOMAIN_MAP.get(type_primary),
        "is_correction": bool(row.get("is_correction")) or is_correction(title),
        "is_material": is_material,
        "summary": None,
        "content": "",
        "pdf_url": str(row.get("pdf_url") or ""),
        "pdf_source": "origin",
        "pdf_size_kb": row.get("adjunct_size_kb"),
        "detail_url": str(row.get("detail_url") or ""),
        "industry_l1": industry,
        "industry_as_of": None,
        "variants": [{
            "source": "cninfo", "id": ann_id, "title": title,
            "url": str(row.get("detail_url") or row.get("pdf_url") or ""),
            "time": disclosed_at or ann_date,
        }],
        "dup_of": None,
        "degraded": False,
        "fetched_at": None,
    }


def ann_from_eastmoney(item: dict[str, Any], industry: str | None = None) -> dict[str, Any] | None:
    codes = item.get("codes") or []
    if not codes:
        return None
    code = str(codes[0].get("stock_code") or "").zfill(6)
    name = str(codes[0].get("short_name") or "")
    title = str(item.get("title") or "").strip()
    norm = norm_title(title)
    display = str(item.get("display_time") or "")
    m = re.match(r"(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}):(\d{2})", display)
    disclosed_at = None
    ann_time = None
    precision = "day"
    if m:
        ann_time = m.group(2)
        disclosed_at = f"{m.group(1)}T{m.group(2)}:{m.group(3)}"
        precision = "sec"
    biz_date = str(item.get("notice_date") or "")[:10] or (display or "")[:10]
    if not biz_date:
        return None
    col = (item.get("columns") or [{}])[0]
    type_source = str(col.get("column_name") or "")
    type_primary = em_type(type_source, title)
    art = str(item.get("art_code") or "")
    event_id = f"ann:{symbol_of(code)}:{biz_date}:{fingerprint(code, norm, biz_date)}"
    return {
        "event_id": event_id,
        "event_kind": "announcement",
        "fp": fingerprint(code, norm, biz_date),
        "ann_id": art,
        "ids": {"em": art},
        "source": "em",
        "sources": ["em"],
        "match_status": "em_only",
        "match_score": 0.0,
        "match_note": "",
        "symbol": symbol_of(code),
        "code": code,
        "name": name,
        "org_id": "",
        "market": market_of(code),
        "title": title,
        "title_norm": norm,
        "ann_date": str(item.get("notice_date") or "")[:10] or biz_date,
        "ann_time": ann_time,
        "disclosed_at": disclosed_at,
        "time_precision": precision,
        "biz_date": biz_date,
        "session": session_of(ann_time),
        "type_primary": type_primary,
        "type_source": type_source,
        "type_codes": [col.get("column_code")] if col.get("column_code") else [],
        "type_domain": DOMAIN_MAP.get(type_primary),
        "is_correction": is_correction(title),
        "is_material": classify(title)[1],
        "summary": None,
        "content": "",
        "pdf_url": f"https://pdf.dfcfw.com/pdf/H2_{art}_1.pdf" if art else "",
        "pdf_source": "mirror",
        "pdf_size_kb": None,
        "detail_url": f"https://data.eastmoney.com/notices/detail/{code}/{art}.html" if art else "",
        "industry_l1": industry,
        "industry_as_of": None,
        "variants": [{
            "source": "em", "id": art, "title": title,
            "url": f"https://data.eastmoney.com/notices/detail/{code}/{art}.html" if art else "",
            "time": disclosed_at or biz_date,
        }],
        "dup_of": None,
        "degraded": False,
        "fetched_at": None,
    }


def merge_ann_pair(cn: dict[str, Any], em: dict[str, Any], score: float = 1.0, note: str = "exact") -> dict[str, Any]:
    out = dict(cn)
    out["sources"] = ["cninfo", "em"]
    out["match_status"] = "both"
    out["match_score"] = round(score, 3)
    out["match_note"] = note
    out["ids"] = {"cninfo": cn["ids"].get("cninfo", ""), "em": em["ids"].get("em", "")}
    out["ann_time"] = em.get("ann_time") or cn.get("ann_time")
    out["disclosed_at"] = em.get("disclosed_at") or cn.get("disclosed_at")
    out["time_precision"] = em.get("time_precision") or cn.get("time_precision")
    out["biz_date"] = em.get("biz_date") or cn.get("biz_date")
    out["session"] = session_of(out.get("ann_time"))
    if em.get("type_source"):
        out["type_source"] = em["type_source"]
    if em.get("type_codes"):
        out["type_codes"] = em["type_codes"]
    if em.get("type_primary") and cn.get("type_primary") in ("其他", "例行公告"):
        out["type_primary"] = em["type_primary"]
        out["type_domain"] = DOMAIN_MAP.get(em["type_primary"])
    out["fp"] = fingerprint(out["code"], out["title_norm"], out["biz_date"])
    out["event_id"] = f"ann:{out['symbol']}:{out['biz_date']}:{out['fp']}"
    out["ann_date_shift"] = (cn.get("ann_date") or "") != out["biz_date"]
    out["time_filled"] = cn.get("time_precision") == "day" and em.get("time_precision") == "sec"
    out["degraded"] = False
    out["variants"] = [*cn.get("variants", []), *em.get("variants", [])]
    return out


def news_event(row: dict[str, Any], sector: str | None = None) -> dict[str, Any]:
    code = str(row.get("code") or "").zfill(6)
    title = str(row.get("title") or "").strip()
    source = str(row.get("source") or "")
    news_id = str(row.get("news_id") or "")
    kind = str(row.get("news_kind") or "news")
    event_id = f"{'report' if kind == 'report' else 'news'}:{symbol_of(code)}:{row.get('publish_date') or ''}:{news_id}"
    return {
        "event_id": event_id,
        "event_kind": "report" if kind == "report" else "news",
        "news_kind": kind,
        "symbol": symbol_of(code),
        "code": code,
        "name": str(row.get("name") or ""),
        "sec_codes": [code] if code else [],
        "sector": sector,
        "title": title,
        "title_norm": norm_news_title(title),
        "summary": str(row.get("summary") or ""),
        "content": str(row.get("content") or ""),
        "media": str(row.get("media") or ""),
        "publish_time": str(row.get("publish_time") or ""),
        "publish_date": str(row.get("publish_date") or ""),
        "time_precision": "sec" if str(row.get("publish_time") or "").count(":") == 2 else "day",
        "url": str(row.get("url") or ""),
        "match_type": str(row.get("match_type") or "source"),
        "source_rank": int(row.get("source_rank") or 1),
        "heat": int(row.get("heat") or 0),
        "sources": [source] if source else [],
        "ids": {source: news_id} if source else {},
        "source_primary": source,
        "match_status": "single",
        "match_score": 0.0,
        "variants": [{
            "source": source, "id": news_id, "title": title,
            "url": str(row.get("url") or ""), "media": str(row.get("media") or ""),
            "time": str(row.get("publish_time") or ""),
        }],
        "dup_of": row.get("dup_of") or None,
        "fetched_at": str(row.get("fetched_at") or ""),
    }


def flash_event(row: dict[str, Any], sector: str | None = None) -> dict[str, Any]:
    code = str(row.get("code") or "").zfill(6) if row.get("code") else ""
    title = str(row.get("title") or "").strip()
    flash_id = str(row.get("flash_id") or "")
    event_id = f"flash:{row.get('publish_date') or ''}:{flash_id}"
    return {
        "event_id": event_id,
        "event_kind": "flash",
        "news_kind": "flash",
        "symbol": symbol_of(code) if code else "",
        "code": code,
        "name": str(row.get("name") or ""),
        "sec_codes": [str(c).zfill(6) for c in (row.get("sec_codes") or []) if str(c)],
        "sec_names": row.get("sec_names") or [],
        "sector": sector,
        "title": title,
        "title_norm": norm_news_title(title),
        "summary": str(row.get("summary") or ""),
        "content": str(row.get("content") or ""),
        "media": str(row.get("media") or "同花顺7x24快讯"),
        "publish_time": str(row.get("publish_time") or ""),
        "publish_date": str(row.get("publish_date") or ""),
        "time_precision": "sec",
        "url": str(row.get("url") or ""),
        "encoded_seq": str(row.get("encoded_seq") or ""),
        "tags": row.get("tags") or [],
        "tag_id": row.get("tag_id"),
        "match_type": str(row.get("match_type") or "market"),
        "source_rank": int(row.get("source_rank") or 1),
        "heat": int(row.get("heat") or 0),
        "sources": ["ths_flash"],
        "ids": {"ths_flash": flash_id},
        "source_primary": "ths_flash",
        "match_status": "single",
        "match_score": 0.0,
        "variants": [{
            "source": "ths_flash", "id": flash_id, "title": title,
            "url": str(row.get("url") or ""), "time": str(row.get("publish_time") or ""),
        }],
        "dup_of": None,
        "fetched_at": str(row.get("fetched_at") or ""),
    }


def merge_news_rows(rows: list[dict[str, Any]], threshold: float = 0.82, window_hours: float = 6) -> list[dict[str, Any]]:
    """跨源合并新闻；同一来源的 news_id 仍然保留为 variant。"""
    events = [news_event(r) for r in rows]
    kept: list[dict[str, Any]] = []
    for event in sorted(events, key=lambda x: x.get("publish_time") or "", reverse=True):
        matched = None
        for candidate in kept:
            if candidate["code"] != event["code"]:
                continue
            if candidate["sources"] == event["sources"]:
                continue
            if _hour_gap(candidate.get("publish_time"), event.get("publish_time")) > window_hours:
                continue
            if title_sim(candidate.get("title"), event.get("title")) >= threshold:
                matched = candidate
                break
        if matched is None:
            kept.append(event)
            continue
        matched["sources"] = list(dict.fromkeys([*matched["sources"], *event["sources"]]))
        matched["ids"] = {**matched["ids"], **event["ids"]}
        matched["variants"] = [*matched["variants"], *event["variants"]]
        matched["match_status"] = "both" if len(matched["sources"]) > 1 else "single"
        matched["match_score"] = 1.0
        if len(event.get("content") or event.get("summary") or "") > len(matched.get("content") or matched.get("summary") or ""):
            for key in ("title", "summary", "content", "media", "url", "publish_time", "publish_date", "source_primary"):
                if event.get(key):
                    matched[key] = event[key]
            matched["dup_of"] = matched.get("dup_of") or event["event_id"]
    return kept


def _hour_gap(a: str | None, b: str | None) -> float:
    try:
        ta = datetime.fromisoformat((a or "").replace(" ", "T"))
        tb = datetime.fromisoformat((b or "").replace(" ", "T"))
        return abs((ta - tb).total_seconds()) / 3600.0
    except ValueError:
        return 24.0


def date_range(from_date: str, to_date: str) -> list[str]:
    d0 = datetime.fromisoformat(from_date).date()
    d1 = datetime.fromisoformat(to_date).date()
    out = []
    while d0 <= d1:
        out.append(d0.isoformat())
        d0 += timedelta(days=1)
    return out
