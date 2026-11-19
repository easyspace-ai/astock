"""cninfo_graph 扩展契约测试。

不依赖真实网络: httpx.MockTransport 注入预置响应, 验证 JSONP 解包、字段归一、
北京零点戳日期换算、公告分页与去重、orgId 解析复用、分块软失败(单块失败不
影响其他块且已存文件保留)、原子落盘与 API 路由(清单读写、未同步 404)。
"""

from __future__ import annotations

import json
from datetime import date

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.custom.cninfo_graph import client as cc
from app.custom.cninfo_graph import em_client as ce
from app.custom.cninfo_graph import news_client as cn
from app.custom.cninfo_graph import routes as cr
from app.custom.cninfo_graph import service as cs
from app.custom.cninfo_graph import store as cst

# ================================================================
# 预置响应 (2026-11 实测结构的快照)
# ================================================================

def _jsonp(payload: dict) -> str:
    return f'jQuery0627_0({json.dumps(payload, ensure_ascii=False)});'


_INDEX_DATA = {
    "cninfo5025Data": [
        {
            "F004V": "陈小华",
            "F003V": "卢雨禾",
            "F002V": None,
            "F001V": "王志楷",
            "F005V": "王志楷，卢雨禾，张国元，古成",
        }
    ],
    "snapshot5015Data": [
        {
            "ORGNAME": "深圳市深粮控股股份有限公司",
            "SECCODE": "000019",
            "F001V": "CNE000000354",
            "F002V": "1992-08-06",
            "F003V": "1992-10-12",
            "F004V": "www.slkg1949.com",
            "F010V": "批发和零售业",
            "F011V": "批发业",
            "F012V": "深交所主板",
        }
    ],
    "cninfo5023Data": [
        {
            "F001V": "公司主营业务为批发零售业务。",
            "F002V": "亮点文本",
            "F003V": "风险文本",
            "DECLAREDATE": "2026-06-15",
        }
    ],
    "codeInfo": {"SECNAME": "深粮控股", "SECCODE": "000019"},
}

_SHAREHOLDERS = {
    "shareHoldersData": [
        {"F001D": "2026-03-31", "F002V": "深圳农业与食品投资控股集团有限公司", "F003N": 735.24, "F004N": 63.79},
        {"F001D": "2026-03-31", "F002V": "孙慧明", "F003N": 3.24, "F004N": 0.28},
        {"F001D": "2026-03-31", "F002V": "", "F003N": 1.0, "F004N": 0.1},  # 空名行跳过
    ]
}


def _ann_page(rows: list[dict], total: int) -> dict:
    return {"totalAnnouncement": total, "announcements": rows}


def _ann_row(ann_id: str, time_ms: int, title: str) -> dict:
    return {
        "secCode": "000019",
        "orgId": "gssz0000019",
        "announcementId": ann_id,
        "announcementTitle": title,
        "announcementTime": time_ms,
        "adjunctUrl": f"finalpage/2026-11-10/{ann_id}.PDF",
        "columnId": "09020202||250101",
        "announcementType": "01010503",
    }


def _build_transport(pages: dict[str, list], *, fail_paths: set[str] | None = None) -> httpx.MockTransport:
    """按 URL 关键字返回预置页序列; fail_paths 中的路径返回 500。"""
    fail_paths = fail_paths or set()
    counters: dict[str, int] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        for key, seq in pages.items():
            if key not in url:
                continue
            if any(f in url for f in fail_paths):
                return httpx.Response(500)
            idx = counters.get(key, 0)
            counters[key] = idx + 1
            body = seq[min(idx, len(seq) - 1)]
            if isinstance(body, str):
                return httpx.Response(200, text=body)
            return httpx.Response(200, json=body)
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def _make_client(pages: dict[str, list], **kw) -> cc.CninfoClient:
    return cc.CninfoClient(transport=_build_transport(pages), **kw)


# ================================================================
# client 归一
# ================================================================

def test_jsonp_unwrap_and_profile_mapping(tmp_path):
    pages = {"getIndexData": [_jsonp(_INDEX_DATA)]}
    with _make_client(pages) as cli:
        profile = cli.fetch_company_profile("000019")

    assert profile["name"] == "深圳市深粮控股股份有限公司"
    assert profile["short_name"] == "深粮控股"
    assert profile["isin"] == "CNE000000354"
    assert profile["founded_date"] == "1992-08-06"
    assert profile["industry_l1"] == "批发和零售业"
    assert profile["market"] == "深交所主板"
    assert profile["business_summary"] == "公司主营业务为批发零售业务。"
    assert profile["declare_date"] == "2026-06-15"
    # 空值角色跳过: 总经理 F002V=None 不出现
    assert profile["executives"] == [
        {"role": "董事长", "name": "王志楷"},
        {"role": "财务总监", "name": "卢雨禾"},
        {"role": "董事会秘书", "name": "陈小华"},
    ]
    assert profile["board_members"] == ["王志楷", "卢雨禾", "张国元", "古成"]


def test_profile_structure_broken_raises():
    pages = {"getIndexData": [_jsonp({"cninfo5025Data": []})]}
    with _make_client(pages) as cli, pytest.raises(cc.CninfoError, match="snapshot5015Data"):
        cli.fetch_company_profile("000019")


def test_shareholders_mapping_units_documented():
    pages = {"singleStockData": [_jsonp(_SHAREHOLDERS)]}
    with _make_client(pages) as cli:
        result = cli.fetch_top_shareholders("000019")

    assert result["report_date"] == "2026-03-31"
    assert len(result["holders"]) == 2  # 空名行跳过
    top = result["holders"][0]
    assert top["name"] == "深圳农业与食品投资控股集团有限公司"
    assert top["shares_mn"] == 735.24  # 百万股原值, 不换算
    assert top["holding_pct_percent"] == 63.79  # 百分数原值 (63.79 = 63.79%)


def test_announcements_pagination_dedupe_and_beijing_date():
    # 两页 + 一页空页终止; 重复 id 去重; 1794240000000 → 北京日期 2026-11-10
    pages = {
        "hisAnnouncement": [
            {
                "totalAnnouncement": 3,
                "announcements": [
                    _ann_row("100", 1794240000000, "公告<em>甲</em>"),
                    _ann_row("101", 1793030400000, "公告乙"),
                ],
            },
            {
                "totalAnnouncement": 3,
                "announcements": [
                    _ann_row("100", 1794240000000, "公告甲(重复id)"),
                    _ann_row("102", 1792425600000, "公告丙"),
                ],
            },
            {"totalAnnouncement": 3, "announcements": []},
        ]
    }
    with _make_client(pages) as cli:
        anns = cli.fetch_announcements("000019", "gssz0000019")

    assert [a["announcement_id"] for a in anns] == ["100", "101", "102"]
    assert anns[0]["title"] == "公告甲"  # <em> 标签剥离
    assert anns[0]["date"] == "2026-11-10"
    assert anns[1]["date"] == "2026-10-27"
    assert anns[0]["pdf_url"].endswith("static.cninfo.com.cn/finalpage/2026-11-10/100.PDF")


def test_resolve_org_id_prefers_a_share():
    # 注意页序列结构: 外层 list 是"响应页序列", 内层 list 才是接口返回的 rows 数组
    pages = {
        "topSearch": [
            [
                {
                    "code": "000019",
                    "orgId": "gssz0000019",
                    "zwjc": "深粮控股",
                    "category": "A股",
                },
                {"code": "000019", "orgId": "other000019", "category": "B股"},
            ]
        ]
    }
    with _make_client(pages) as cli:
        assert cli.resolve_org_id("000019") == "gssz0000019"


def test_beijing_date_of_ms_rejects_invalid():
    assert cc.beijing_date_of_ms(None) is None
    assert cc.beijing_date_of_ms(0) is None
    assert cc.beijing_date_of_ms("abc") is None
    assert cc.beijing_date_of_ms(1794240000000) == date(2026, 11, 10)


# ================================================================
# service 同步编排
# ================================================================

def _pages_for_sync() -> dict[str, list]:
    return {
        "getIndexData": [_jsonp(_INDEX_DATA)],
        "singleStockData": [_jsonp(_SHAREHOLDERS)],
        "hisAnnouncement": [
            {
                "totalAnnouncement": 2,
                "announcements": [_ann_row("100", 1794240000000, "公告甲"), _ann_row("101", 1793030400000, "公告乙")],
            }
        ],
        "topSearch": [[{"code": "000019", "orgId": "gssz0000019", "category": "A股"}]],
    }


# ================================================================
# 东财 (十大股东/高管) 与 扶摇 (新闻) 夹具 — 2026-11 实测结构快照
# ================================================================

_EM_HOLDER_PAGE = {
    "sdgd": [
        {
            "SECUCODE": "000019.SZ",
            "END_DATE": "2026-09-30 00:00:00",
            "HOLDER_RANK": 1,
            "HOLDER_NAME": "深圳农业与食品投资控股集团有限公司",
            "SHARES_TYPE": "流通A股,限售流通A股",
            "HOLD_NUM": 735237253,
            "HOLD_NUM_RATIO": 63.79,
            "HOLD_NUM_CHANGE": "不变",
            "CHANGE_RATIO": None,
        },
        {
            "SECUCODE": "000019.SZ",
            "END_DATE": "2026-09-30 00:00:00",
            "HOLDER_RANK": 2,
            "HOLDER_NAME": "深圳市农产品集团股份有限公司",
            "SHARES_TYPE": "A股",
            "HOLD_NUM": 94834100,
            "HOLD_NUM_RATIO": 8.22,
            "HOLD_NUM_CHANGE": -1200000,
            "CHANGE_RATIO": -1.25,
        },
        {"END_DATE": "2026-09-30 00:00:00", "HOLDER_RANK": 3, "HOLDER_NAME": "", "HOLD_NUM": 1},  # 空名跳过
    ],
    "sdltgd": [
        {
            "SECUCODE": "000019.SZ",
            "END_DATE": "2026-09-30 00:00:00",
            "HOLDER_RANK": 1,
            "HOLDER_NAME": "深圳市农产品集团股份有限公司",
            "SHARES_TYPE": "A股",
            "HOLD_NUM": 79447462,
            "FREE_HOLDNUM_RATIO": 16.98,
            "HOLD_NUM_CHANGE": "不变",
            "CHANGE_RATIO": None,
        }
    ],
    "gdrs": [
        {
            "END_DATE": "2026-09-30 00:00:00",
            "HOLDER_TOTAL_NUM": 44008,
            "AVG_FREE_SHARES": 10633,
            "HOLD_FOCUS": "较分散",
            "AVG_HOLD_AMT": 74540.69,
        },
        {
            "END_DATE": "2026-06-30 00:00:00",
            "HOLDER_TOTAL_NUM": 42000,
            "AVG_FREE_SHARES": 11000,
            "HOLD_FOCUS": "较分散",
            "AVG_HOLD_AMT": 70000.0,
        },
    ],
    "sjkzr": [{"SECUCODE": "000019.SZ", "HOLDER_NAME": "深圳市人民政府国有资产监督管理委员会", "HOLD_RATIO": None}],
}

_EM_EXEC_PAGE = {
    "version": None,
    "success": True,
    "result": {
        "data": [
            {
                "PERSON_CODE": "30810211",
                "PERSON_NAME": "王志楷",
                "POSITION": "董事长,法定代表人",
                "POSITION_RANK": 1001,
                "SEX": "男",
                "AGE": "55",
                "HIGH_DEGREE": "硕士",
                "SALARY": 1282800,
                "HOLD_NUM": 0,
                "HOLD_RATIO": 0,
                "INCUMBENT_DATE": "2023-11-03 00:00:00",
                "INCUMBENT_TIME": "2023-11-03至今",
                "REPORT_DATE": "2025-12-31 00:00:00",
                "IS_DSH": "1",
                "IS_JSH": "0",
                "IS_GLC": "1",
                "RESUME": "王志楷先生:1971年出生,硕士研究生,现任深圳农业与食品投资控股集团有限公司董事长。",
            },
            {
                "PERSON_CODE": "30810212",
                "PERSON_NAME": "卢雨禾",
                "POSITION": "财务总监",
                "POSITION_RANK": 1003,
                "SEX": "女",
                "AGE": "41",
                "HIGH_DEGREE": "本科",
                "SALARY": 600000,
                "HOLD_NUM": 10000,
                "HOLD_RATIO": 0.01,
                "INCUMBENT_DATE": "2024-01-05 00:00:00",
                "INCUMBENT_TIME": "2024-01-05至今",
                "REPORT_DATE": "2025-12-31 00:00:00",
                "IS_DSH": "0",
                "IS_JSH": "0",
                "IS_GLC": "1",
                "RESUME": "卢雨禾女士:现任公司财务总监。",
            },
            {  # 旧报告期 → 被过滤
                "PERSON_CODE": "30810213",
                "PERSON_NAME": "离任人员",
                "POSITION": "前董事",
                "REPORT_DATE": "2024-12-31 00:00:00",
            },
        ]
    },
}

_EM_SURVEY_601595 = {
    "jbzl": [
        {
            "SECUCODE": "601595.SH",
            "SECURITY_CODE": "601595",
            "SECURITY_NAME_ABBR": "上海电影",
            "ORG_NAME": "上海电影股份有限公司",
            "SECURITY_TYPE": "上交所主板A股",
            "INDUSTRYCSRC1": "文化、体育和娱乐业-广播、电视、电影和录音制作业",
            "CHAIRMAN": "王隽",
            "PRESIDENT": "戴运",
            "SECRETARY": "刘林鹤",
            "ORG_TEL": "021-33391000",
            "ORG_EMAIL": "ir@sh-sfc.com",
            "ORG_WEB": "www.sh-sfc.com",
            "REG_ADDRESS": "上海市徐汇区漕溪北路595号",
            "ADDRESS": "上海市徐汇区漕溪北路595号C座14楼",
            "ORG_PROFILE": "上海电影股份有限公司主营业务涵盖电影发行及放映等。",
        }
    ],
    "fxxg": [{"FOUND_DATE": "1994-10-07 00:00:00", "LISTING_DATE": "2016-08-17 00:00:00"}],
}

_EM_HOLDER_601595 = {
    "sdgd": [
        {
            "SECUCODE": "601595.SH",
            "END_DATE": "2026-09-30 00:00:00",
            "HOLDER_RANK": 1,
            "HOLDER_NAME": "上海电影（集团）有限公司",
            "HOLD_NUM": 180000000,
            "HOLD_NUM_RATIO": 40.16,
            "HOLD_NUM_CHANGE": "不变",
        },
        {
            "SECUCODE": "601595.SH",
            "END_DATE": "2026-06-30 00:00:00",
            "HOLDER_RANK": 2,
            "HOLDER_NAME": "某某投资",
            "HOLD_NUM": 50000000,
            "HOLD_NUM_RATIO": 11.16,
            "HOLD_NUM_CHANGE": 0,
        },
    ],
    "sdltgd": [],
    "gdrs": [],
    "sjkzr": [{"HOLDER_NAME": "上海市国有资产监督管理委员会", "HOLD_RATIO": None}],
}

_FY_NEWS_PAGE1 = {
    "code": 0,
    "message": "success",
    "data": {
        "items": [
            {
                "id": "n100",
                "type": 1000,
                "title": "公司中标某项目",
                "source": "证券时报",
                "summary": "摘要甲",
                "publish_time_ms": 1794472446000,
                "jump_url": "https://example.org/n100",
                "pic_url": None,
            },
            {"id": "", "title": "无id跳过", "publish_time_ms": 1},
        ],
        "has_more": True,
        "offset": "next1",
    },
}

_FY_NEWS_PAGE2 = {
    "code": 0,
    "message": "success",
    "data": {
        "items": [
            {
                "id": "n101",
                "type": 1000,
                "title": "公司发布业绩预告",
                "source": "同花顺",
                "summary": "摘要乙",
                "publish_time_ms": 1794364731000,
                "jump_url": "https://example.org/n101",
                "pic_url": None,
            }
        ],
        "has_more": False,
        "offset": "",
    },
}


def _make_em_client(pages: dict[str, list]) -> ce.EastmoneyClient:
    return ce.EastmoneyClient(transport=_build_transport(pages))


def _make_news_client(pages: dict[str, list]) -> cn.FuyaoNewsClient:
    return cn.FuyaoNewsClient(api_key="test-key", transport=_build_transport(pages))


def test_sync_company_happy_path(tmp_path):
    pages = _pages_for_sync()
    calls: list[str] = []
    transport = _build_transport(pages)
    original_handler = transport.handler

    def counting_handler(request):
        calls.append(str(request.url.path))
        return original_handler(request)

    counting_transport = httpx.MockTransport(counting_handler)
    with (
        cc.CninfoClient(transport=counting_transport) as cli,
        _make_em_client({"ShareholderResearch": [_EM_HOLDER_PAGE], "MANAINTRO": [_EM_EXEC_PAGE]}) as em,
        _make_news_client({"timeline-list": [_FY_NEWS_PAGE1, _FY_NEWS_PAGE2]}) as news,
    ):
        result = cs.sync_company(tmp_path, "000019.SZ", client=cli, em_client=em, news_client=news)

    assert sorted(result["parts"]) == ["announcements", "executives", "holders_top10", "news", "profile", "shareholders"]
    assert all(result["parts"][p]["ok"] for p in result["parts"])
    # orgId 只解析一次, 第二次同步复用 (state 缓存)
    assert calls.count("/new/information/topSearch/query") == 1

    detail = cs.company_detail(tmp_path, "000019.SZ")
    assert detail["profile"]["symbol"] == "000019.SZ"
    assert detail["shareholders"]["report_date"] == "2026-03-31"
    assert len(detail["announcements"]) == 2
    assert detail["state"]["org_id"] == "gssz0000019"

    # 十大股东: 原值口径 + "不变" 宽容解析
    top10 = detail["holders_top10"]
    assert top10["report_date"] == "2026-09-30"
    assert top10["top10"][0]["name"] == "深圳农业与食品投资控股集团有限公司"
    assert top10["top10"][0]["shares"] == 735237253  # 股, 原值
    assert top10["top10"][0]["pct_percent"] == 63.79
    assert top10["top10"][0]["change_note"] == "不变"  # 非数值原文保留
    assert top10["top10"][1]["change_shares"] == -1200000
    assert top10["top10_float"][0]["float_pct_percent"] == 16.98
    assert top10["holder_count_history"][0]["holder_total_num"] == 44008
    assert top10["controller"]["name"] == "深圳市人民政府国有资产监督管理委员会"

    # 高管: 只保留最新报告期, 简历/薪酬/人标识
    execs = detail["executives"]
    assert execs["report_date"] == "2025-12-31"
    assert [p["name"] for p in execs["persons"]] == ["王志楷", "卢雨禾"]  # 旧报告期被过滤, 按职务排序
    assert execs["persons"][0]["person_code"] == "30810211"
    assert execs["persons"][0]["salary_yuan"] == 1282800
    assert "硕士研究生" in execs["persons"][0]["resume"]

    # 新闻: 空id跳过, 时间降序, 北京时间 ISO
    news_items = detail["news"]
    assert [n["id"] for n in news_items] == ["n100", "n101"]
    assert news_items[0]["date"].startswith("2026-")

    # 第二次同步: 公告/新闻重复 id 合并不翻倍; orgId 不再请求
    calls.clear()
    with (
        cc.CninfoClient(transport=counting_transport) as cli,
        _make_em_client({"ShareholderResearch": [_EM_HOLDER_PAGE], "MANAINTRO": [_EM_EXEC_PAGE]}) as em,
        _make_news_client({"timeline-list": [_FY_NEWS_PAGE1]}) as news,
    ):
        cs.sync_company(tmp_path, "000019.SZ", client=cli, em_client=em, news_client=news)
    assert calls.count("/new/information/topSearch/query") == 0
    detail2 = cs.company_detail(tmp_path, "000019.SZ")
    assert len(detail2["announcements"]) == 2
    assert len(detail2["news"]) == 2


def test_em_client_rejects_broken_structure():
    with (
        _make_em_client({"ShareholderResearch": [{"sdgd": []}]}) as em,
        pytest.raises(ce.EastmoneyError, match="sdgd"),
    ):
        em.fetch_holder_research("000019.SZ")
    with (
        _make_em_client({"MANAINTRO": [{"result": {"data": []}}]}) as em,
        pytest.raises(ce.EastmoneyError, match="MANAINTRO"),
    ):
        em.fetch_executives("000019.SZ")


def test_em_company_profile_mapping():
    with _make_em_client({"CompanySurvey": [_EM_SURVEY_601595]}) as em:
        profile = em.fetch_company_profile("601595.SH")
    assert profile["name"] == "上海电影股份有限公司"
    assert profile["short_name"] == "上海电影"
    assert profile["founded_date"] == "1994-10-07"
    assert profile["listed_date"] == "2016-08-17"
    assert profile["industry_l1"] == "文化、体育和娱乐业"
    assert profile["industry_l2"] == "广播、电视、电影和录音制作业"
    assert profile["market"] == "上交所主板A股"
    assert profile["reg_address"] == "上海市徐汇区漕溪北路595号"
    assert [e["role"] for e in profile["executives"]] == ["董事长", "总经理", "董事会秘书"]


def test_sync_company_sse_falls_back_to_eastmoney(tmp_path):
    """沪市巨潮档案/前五大为空: 东财档案与前五大兜底, 其余块照常。"""
    pages = {
        "getIndexData": [""],
        "singleStockData": [""],
        "hisAnnouncement": [
            {
                "totalAnnouncement": 1,
                "announcements": [_ann_row("200", 1794240000000, "沪市公告")],
            }
        ],
        "topSearch": [[{"code": "601595", "orgId": "9900024674", "category": "A股"}]],
    }
    with (
        cc.CninfoClient(transport=_build_transport(pages)) as cli,
        _make_em_client(
            {
                "CompanySurvey": [_EM_SURVEY_601595],
                "ShareholderResearch": [_EM_HOLDER_601595],
                "MANAINTRO": [_EM_EXEC_PAGE],
            }
        ) as em,
        _make_news_client({"timeline-list": [_FY_NEWS_PAGE1, _FY_NEWS_PAGE2]}) as news,
    ):
        result = cs.sync_company(tmp_path, "601595.SH", client=cli, em_client=em, news_client=news)

    assert all(result["parts"][p]["ok"] for p in result["parts"])
    assert result["parts"][cst.SHAREHOLDERS]["source"] == "eastmoney"

    detail = cs.company_detail(tmp_path, "601595.SH")
    assert detail["profile"]["name"] == "上海电影股份有限公司"
    assert detail["shareholders"]["report_date"] == "2026-09-30"
    assert detail["shareholders"]["holders"][0]["name"] == "上海电影（集团）有限公司"
    assert detail["shareholders"]["holders"][0]["shares_mn"] == 180.0
    assert detail["announcements"][0]["title"] == "沪市公告"


def test_news_client_requires_key_and_pagination():
    with pytest.raises(cn.FuyaoNewsError, match="key"):
        cn.FuyaoNewsClient(api_key="")
    with _make_news_client({"timeline-list": [_FY_NEWS_PAGE1, _FY_NEWS_PAGE2]}) as news:
        items = news.fetch_latest("000019.SZ")
    assert [n["id"] for n in items] == ["n100", "n101"]  # has_more=False 停止
    assert items[0]["url"] == "https://example.org/n100"


def test_sync_partial_failure_cross_source(tmp_path):
    """东财(十大/高管)与扶摇(新闻)全挂: 巨潮三块照常成功, 各块独立。"""
    transport = _build_transport(_pages_for_sync())
    with (
        cc.CninfoClient(transport=transport) as cli,
        _make_em_client({}) as em,  # 无匹配页 → 404
        _make_news_client({}) as news,
    ):
        result = cs.sync_company(tmp_path, "000019.SZ", client=cli, em_client=em, news_client=news)
    for name in ("holders_top10", "executives", "news"):
        assert result["parts"][name]["ok"] is False, name
        assert result["parts"][name]["error"]
    for name in ("profile", "shareholders", "announcements"):
        assert result["parts"][name]["ok"] is True, name
    detail = cs.company_detail(tmp_path, "000019.SZ")
    assert detail["shareholders"]["report_date"] == "2026-03-31"
    assert detail["holders_top10"] is None  # 失败块不写文件
    assert detail["news"] == []


def test_sync_partial_failure_preserves_existing(tmp_path):
    # 第一轮成功, 已存 shareholders
    with cc.CninfoClient(transport=_build_transport(_pages_for_sync())) as cli:
        cs.sync_company(tmp_path, "000019.SZ", client=cli)

    # 第二轮 shareholders 端点 500 且东财兜底也不可用: 该块失败, 其他块照常, 已有文件保留
    transport = _build_transport(_pages_for_sync(), fail_paths={"singleStockData"})
    with (
        cc.CninfoClient(transport=transport) as cli,
        _make_em_client({}) as em,
    ):
        result = cs.sync_company(
            tmp_path, "000019.SZ", client=cli, em_client=em, with_announcements=False
        )
    assert result["parts"][cst.SHAREHOLDERS]["ok"] is False
    assert result["parts"][cst.SHAREHOLDERS]["error"]
    assert result["parts"][cst.PROFILE]["ok"] is True
    # 已存的 shareholders 文件仍是上一轮成功内容
    detail = cs.company_detail(tmp_path, "000019.SZ")
    assert detail["shareholders"]["report_date"] == "2026-03-31"


def test_symbol_validation(tmp_path):
    with pytest.raises(ValueError):
        cs.sync_company(tmp_path, "000019", client=cc.CninfoClient(transport=httpx.MockTransport(lambda r: httpx.Response(500))))
    with pytest.raises(ValueError):
        cs.normalize_symbol("000019.XX")
    assert cs.normalize_symbol("000019.sz") == "000019.SZ"
    assert cs.normalize_symbol("601595.SH") == "601595.SH"
    assert cs.normalize_symbol("830001.BJ") == "830001.BJ"
    with pytest.raises(ValueError, match="交易所不匹配"):
        cs.normalize_symbol("601595.SZ")
    assert cs.code_of("000019.SZ") == "000019"
    assert cs.column_of("600519.SH") == "sse"
    assert cs.column_of("000019.SZ") == "szse"


# ================================================================
# API 路由
# ================================================================

@pytest.fixture()
def api(tmp_path, monkeypatch):
    """TestClient + 指向 tmp 的 data_dir; sync 路由的客户端注入替换为假客户端。"""
    app = FastAPI()
    app.state.repo = type("R", (), {})()
    app.state.repo.store = type("S", (), {})()
    app.state.repo.store.data_dir = tmp_path

    class _StubClient:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def close(self):
            pass

    monkeypatch.setattr(cr, "CninfoClient", _StubClient)
    app.include_router(cr.build_router())
    return TestClient(app)


def test_routes_tracked_and_detail(api):
    # 空清单
    r = api.get("/api/graph")
    assert r.status_code == 200
    assert r.json() == {"tracked": [], "stored": [], "state": {}}

    # 写清单: 校验 + 归一 + 去重 (裸代码如 "000019" 无法推断交易所, 拒绝 → 400)
    r = api.put("/api/graph", json={"symbols": ["000019.SZ", "000019.sz", "600519.SH"]})
    assert r.status_code == 200
    assert r.json()["tracked"] == ["000019.SZ", "600519.SH"]

    # 非法代码 400
    assert api.put("/api/graph", json={"symbols": ["BAD"]}).status_code == 400

    # 未同步公司 404
    assert api.get("/api/graph/000019.SZ").status_code == 404

    # 手工落盘后可读
    cst.save_part(api.app.state.repo.store.data_dir, "000019.SZ", cst.PROFILE, {"name": "深粮控股", "symbol": "000019.SZ"})
    r = api.get("/api/graph/000019.SZ")
    assert r.status_code == 200
    assert r.json()["profile"]["name"] == "深粮控股"
    assert r.json()["announcements"] == []
