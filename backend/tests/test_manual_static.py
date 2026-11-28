"""交付说明书静态站（/manual）的 cleanUrls 回退。"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.frontend_static import ManualStaticFiles


def _client(tmp_path) -> TestClient:
    (tmp_path / "index.html").write_text("<h1>manual</h1>", encoding="utf-8")
    (tmp_path / "access.html").write_text("<h1>access</h1>", encoding="utf-8")
    # VitePress 产物自带 404.html，StaticFiles(html=True) 找不到文件会返回它而不是抛异常
    (tmp_path / "404.html").write_text("<h1>404</h1>", encoding="utf-8")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    app = FastAPI()
    app.mount("/manual", ManualStaticFiles(directory=tmp_path, html=True), name="manual")
    return TestClient(app)


def test_manual_serves_pages_and_clean_urls(tmp_path):
    client = _client(tmp_path)

    index = client.get("/manual/")
    assert index.status_code == 200
    assert "manual" in index.text

    # VitePress 链接是 /manual/access，磁盘上是 access.html
    page = client.get("/manual/access")
    assert page.status_code == 200
    assert "access" in page.text

    asset = client.get("/manual/assets/app.js")
    assert asset.status_code == 200
    assert "javascript" in asset.headers["content-type"]

    # 固定文件名必须回源校验（改文档/换 logo 能立即生效），哈希资源才可长缓存
    assert page.headers["cache-control"] == "no-cache"
    assert "immutable" in asset.headers["cache-control"]

    assert client.get("/manual/not-a-page").status_code == 404
