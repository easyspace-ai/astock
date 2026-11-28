"""前端资源使用固定 MIME，避免 Windows 注册表中的文件关联影响页面执行。"""
from __future__ import annotations

import os
from pathlib import Path

from starlette.datastructures import Headers
from starlette.responses import FileResponse, Response
from starlette.staticfiles import NotModifiedResponse, StaticFiles
from starlette.types import Scope

_MIME_TYPES = {
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".css": "text/css",
    ".wasm": "application/wasm",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
}


def frontend_file_response(
    path: str | os.PathLike[str], *, status_code: int = 200,
    stat_result: os.stat_result | None = None,
) -> FileResponse:
    return FileResponse(
        path, media_type=_MIME_TYPES.get(Path(path).suffix.lower()),
        status_code=status_code, stat_result=stat_result,
    )


class FrontendStaticFiles(StaticFiles):
    def file_response(
        self, full_path: str | os.PathLike[str], stat_result: os.stat_result,
        scope: Scope, status_code: int = 200,
    ) -> Response:
        response = frontend_file_response(
            full_path, status_code=status_code, stat_result=stat_result,
        )
        if self.is_not_modified(response.headers, Headers(scope=scope)):
            return NotModifiedResponse(response.headers)
        return response


class ManualStaticFiles(StaticFiles):
    """交付说明书静态站（VitePress 产物）。

    VitePress 开了 cleanUrls：页面文件是 `access.html`，但站内链接写的是 `/manual/access`。
    静态服务按路径找不到文件会落到 404.html，这里在查表阶段对无扩展名的路径回退
    `<path>.html`，既保住整洁 URL，也不用在部署时做 rewrite。
    注意必须在 lookup_path 处理：StaticFiles(html=True) 找不到文件时会返回 404.html
    （状态 404、不抛异常），放到 get_response 里捕异常是捕不到的。
    """

    def lookup_path(self, path: str):
        full_path, stat_result = super().lookup_path(path)
        if stat_result is None and path and not Path(path).suffix and not path.endswith("/"):
            return super().lookup_path(path + ".html")
        return full_path, stat_result

    def file_response(self, full_path, stat_result, scope, status_code: int = 200):
        response = super().file_response(full_path, stat_result, scope, status_code)
        # 文档站的 HTML/SVG 用固定文件名，必须回源校验，否则改完文档刷新还是旧内容
        # （logo 换图后线上仍显示旧图标就是这么来的）。只有带内容哈希的 /assets/ 可以长缓存。
        if "/assets/" in str(full_path).replace("\\", "/"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            response.headers["Cache-Control"] = "no-cache"
        return response
