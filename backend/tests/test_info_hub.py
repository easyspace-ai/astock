from __future__ import annotations

import json
from datetime import timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.custom.info_hub import announcements, config, models, service, store
from app.custom.info_hub import routes as info_routes
from app.market_time import cn_now
from app.services import rbac


def _cn(**over):
    row = {
        "announcement_id": "CN-1", "sec_code": "600519", "sec_name": "贵州茅台",
        "org_id": "gssh0600519", "ann_date": "2026-11-13", "ann_time": None,
        "market": "sh", "title": "贵州茅台:2026年第三季度报告",
        "ann_type_raw": "定期报告", "ann_type_codes": ["01010503"],
        "pdf_url": "https://static.cninfo.com.cn/a.pdf", "detail_url": "https://www.cninfo.com.cn/a",
    }
    row.update(over)
    return row


def _em(**over):
    row = {
        "art_code": "AN202611130001", "codes": [{"stock_code": "600519", "short_name": "贵州茅台"}],
        "title": "贵州茅台:2026年第三季度报告", "notice_date": "2026-11-13",
        "display_time": "2026-11-13 20:09:08:691",
        "columns": [{"column_name": "定期报告", "column_code": "01010503"}],
    }
    row.update(over)
    return row


def test_merge_keeps_cninfo_only_em_only_and_variants():
    cn = _cn()
    em = _em()
    rows, report = announcements.merge_day([cn], [em], "2026-11-13")
    assert report["merged"] == 1
    event = rows[0]
    assert event["sources"] == ["cninfo", "em"]
    assert event["match_status"] == "both"
    assert event["match_note"] == "exact"
    assert event["disclosed_at"] == "2026-11-13T20:09:08"
    assert event["time_precision"] == "sec"
    assert {v["source"] for v in event["variants"]} == {"cninfo", "em"}
    assert event["ids"]["cninfo"] == "CN-1"
    assert event["ids"]["em"] == "AN202611130001"


def test_merge_does_not_drop_unmatched_sources():
    cn = _cn(announcement_id="CN-2", title="贵州茅台:关于回购股份的公告")
    em = _em(art_code="EM-2", title="贵州茅台:关于回购股份的公告", display_time="2026-11-13 10:00:01:001")
    cn_extra = _cn(announcement_id="CN-3", title="贵州茅台:独立董事候选人声明")
    em_extra = _em(art_code="EM-3", title="贵州茅台:董事会决议公告", display_time="2026-11-13 11:00:00:000")
    rows, report = announcements.merge_day([cn, cn_extra], [em, em_extra], "2026-11-13")
    assert report["matched"] == 1
    assert report["cninfo_only"] == 1
    assert report["em_only"] == 1
    assert len(rows) == 3


def test_merge_fuzzy_and_manual_override():
    cn = _cn(announcement_id="CN-4", title="贵州茅台:关于回购公司股份的进展公告")
    em = _em(art_code="EM-4", title="贵州茅台:关于回购公司股份进展的公告", display_time="2026-11-13 20:09:08:691")
    rows, report = announcements.merge_day([cn], [em], "2026-11-13")
    assert report["matched"] == 1
    assert rows[0]["match_note"] == "fuzzy"


def test_news_cross_source_merge_keeps_both_sources():
    rows = [
        {"news_id": "ths:1", "source": "ths", "news_kind": "news", "source_rank": 1, "code": "600519", "name": "贵州茅台",
         "title": "贵州茅台发布三季度业绩", "summary": "a", "publish_time": "2026-11-13 10:00:00", "publish_date": "2026-11-13",
         "url": "u1"},
        {"news_id": "em:2", "source": "em", "news_kind": "news", "source_rank": 2, "code": "600519", "name": "贵州茅台",
         "title": "贵州茅台发布三季度业绩", "summary": "b", "publish_time": "2026-11-13 10:30:00", "publish_date": "2026-11-13",
         "url": "u2"},
    ]
    events = models.merge_news_rows(rows)
    assert len(events) == 1
    assert set(events[0]["sources"]) == {"ths", "em"}
    assert len(events[0]["variants"]) == 2


def test_watchlist_default_and_persistence(tmp_path):
    data = config.load_watchlist(tmp_path)
    assert len(data["sectors"]) == 3
    assert sum(len(v) for v in data["sectors"].values()) == 29
    saved = config.save_watchlist(tmp_path, {"sectors": {"测试": [{"code": "1", "name": "x"}]}})
    assert saved["sectors"]["测试"][0]["code"] == "000001"


def test_service_filters_and_paginates(tmp_path):
    p = config.ensure_layout(tmp_path)
    rows = [
        models.ann_from_cninfo(_cn(announcement_id=f"CN-{i}", sec_code="600519", title=f"贵州茅台:公告{i}"))
        for i in range(3)
    ]
    rows.append(models.ann_from_cninfo(_cn(announcement_id="CN-9", sec_code="000001", sec_name="平安银行", title="平安银行:公告")))
    store.write_jsonl(p.unified / "announcements" / "dt=2026-11-13.jsonl", rows)
    svc = service.service_for(tmp_path)
    result = svc.announcements({"code": "600519.SH", "page": 1, "size": 2})
    assert result["total"] == 3
    assert len(result["items"]) == 2
    assert all(item["symbol"] == "600519.SH" for item in result["items"])
    assert svc.announcements({"source": "only_cninfo"})["total"] == 4


def test_api_announcements_and_reload(tmp_path):
    p = config.ensure_layout(tmp_path)
    store.write_jsonl(p.unified / "announcements" / "dt=2026-11-13.jsonl", [models.ann_from_cninfo(_cn())])
    app = FastAPI()
    app.state.data_dir = str(tmp_path)
    app.include_router(__import__("app.custom.info_hub.routes", fromlist=["router"]).router)
    client = TestClient(app)
    resp = client.get("/api/info/announcements", params={"code": "600519.SH"})
    assert resp.status_code == 200
    assert resp.json()["total"] == 1
    assert client.post("/api/info/reload").json()["ok"] is True
    health = client.get("/api/info/health").json()
    assert health["counts"]["announcements"] == 1


def test_timeline_uses_by_code_history(tmp_path):
    p = config.ensure_layout(tmp_path)
    rows = [models.ann_from_cninfo(_cn(announcement_id="CN-H", ann_date="2026-01-02"))]
    store.write_jsonl(p.unified / "announcements" / "dt=2026-01-02.jsonl", rows)
    store.write_jsonl(p.by_code / "announcements" / "600519.jsonl", rows)
    svc = service.service_for(tmp_path)
    result = svc.timeline("600519.SH", days=400)
    assert result["counts"].get("announcement") == 1
    assert json.dumps(result, ensure_ascii=False).find("贵州茅台") >= 0


# ================================================================
# 手动抓取: 页面权限放行 + 参数分档 + 单飞/冷却
# ================================================================


def _info_app(tmp_path, *, user: dict | None = None, perms: list[str] | None = None):
    app = FastAPI()
    app.state.data_dir = str(tmp_path)
    if user is not None:
        allowed = frozenset(perms or [])

        @app.middleware("http")
        async def _fake_account(request, call_next):
            request.state.user = user
            request.state.permissions = allowed
            return await call_next(request)

    app.include_router(info_routes.router)
    return TestClient(app)


@pytest.fixture(autouse=True)
def _reset_trigger_gap():
    """冷却表是进程级; 用例之间必须清干净, 否则后一个用例会吃到 429。"""
    info_routes._LAST_TRIGGER.clear()
    yield
    info_routes._LAST_TRIGGER.clear()


def test_manual_sync_allows_page_holder_with_recent_date(tmp_path):
    today = cn_now().date()
    client = _info_app(
        tmp_path,
        user={"email": "editor@example.com", "is_active": True, "perm_role": "editor"},
        perms=[rbac.PAGE_EXT_INFO_ANNOUNCEMENTS],
    )
    resp = client.post("/api/info/sync", params={"kinds": "announcements", "date": today.isoformat(), "live": "false"})
    assert resp.status_code == 200, resp.text
    runs = store.recent_runs(tmp_path, 5)
    # 运行日志带触发者, 便于定位"谁把源打挂"
    assert runs[0]["actor"] == "editor@example.com"


def test_manual_sync_blocks_heavy_operations_for_non_admin(tmp_path):
    client = _info_app(
        tmp_path,
        user={"email": "editor@example.com", "is_active": True, "perm_role": "editor"},
        perms=[rbac.PAGE_EXT_INFO_ANNOUNCEMENTS],
    )
    far = (cn_now().date() - timedelta(days=30)).isoformat()
    assert client.post("/api/info/sync", params={"kinds": "announcements", "date": far}).status_code == 403
    assert client.post("/api/info/sync", params={"kinds": "announcements", "force": "true"}).status_code == 403
    assert client.post("/api/info/sync", params={"kinds": "announcements", "legacy_root": "/tmp/x"}).status_code == 403


def test_manual_sync_is_cooled_down_per_account(tmp_path):
    client = _info_app(
        tmp_path,
        user={"email": "editor@example.com", "is_active": True, "perm_role": "editor"},
        perms=[rbac.PAGE_EXT_INFO_ANNOUNCEMENTS],
    )
    assert client.post("/api/info/sync", params={"kinds": "announcements"}).status_code == 200
    second = client.post("/api/info/sync", params={"kinds": "announcements"})
    assert second.status_code == 429
    assert int(second.headers["Retry-After"]) > 0


def test_manual_sync_returns_409_while_another_run_holds_lock(tmp_path):
    client = _info_app(
        tmp_path,
        user={"email": "editor@example.com", "is_active": True, "perm_role": "editor"},
        perms=[rbac.PAGE_EXT_INFO_ANNOUNCEMENTS],
    )
    lock = service._sync_lock(tmp_path)
    lock.acquire()
    try:
        resp = client.post("/api/info/sync", params={"kinds": "announcements"})
        assert resp.status_code == 409
    finally:
        lock.release()
    # 没真正跑起来的触发不该占用冷却名额
    assert client.post("/api/info/sync", params={"kinds": "announcements"}).status_code == 200


def test_sync_is_serialized_for_service_callers(tmp_path, monkeypatch):
    """定时任务走 wait=True: 不会因为用户手动抓取而丢任务, 只是排队。"""
    import threading

    release = threading.Event()
    started = threading.Event()

    def slow_fetch(data_dir, date=None):
        started.set()
        release.wait(2)
        return {"date": date or "2026-11-26", "cninfo": 0, "em": 0, "errors": []}

    from app.custom.info_hub import sources

    monkeypatch.setattr(sources, "fetch_announcements", slow_fetch)
    svc = service.service_for(tmp_path)
    worker = threading.Thread(target=lambda: svc.sync(["announcements"], live=True, date="2026-11-26"))
    worker.start()
    assert started.wait(2)
    with pytest.raises(service.SyncBusyError):
        svc.sync(["announcements"], live=True, date="2026-11-26", wait=False)
    release.set()
    worker.join(3)
    # 锁释放后可以继续排队执行
    assert svc.sync(["announcements"], live=True, date="2026-11-26")["kinds"] == ["announcements"]
