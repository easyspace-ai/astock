from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Annotated
from urllib.parse import urlsplit

import yaml
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel

from app.api.accounts import _client_ip
from app.data_providers.custom import packages
from app.services import user_store


def _admin_user_session(request: Request) -> bool:
    """邮箱账号会话, 且持有「页面插件管理」权限。

    插件安装是代码执行级操作, 只放行有 admin.pages_manage 的账号。
    """
    token = request.cookies.get("tf_user_session", "")
    if not token:
        return False
    from app.services import rbac
    user = user_store.get_user_by_token(token)
    if not user:
        return False
    return rbac.ADMIN_PAGES_MANAGE in user_store.user_permissions(user)


def require_management(request: Request):
    # Unlike read-only APIs, uploading Python is code execution. LAN access without
    # a password is not enough. No API-token scope grants this permission.
    if request.headers.get("authorization"):
        raise HTTPException(403, "插件安装仅供面板管理会话使用")
    origin = request.headers.get("origin")
    if origin and urlsplit(origin).netloc != request.headers.get("host"):
        raise HTTPException(403, "插件管理需要同源访问")
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "拒绝跨站插件管理请求")
    if not user_store.has_users():
        if _client_ip(request) not in ("127.0.0.1", "::1", "localhost") or request.url.hostname not in ("127.0.0.1", "::1", "localhost"):
            raise HTTPException(403, "请先注册管理员并登录，或通过本机访问安装插件")
        return
    if not _admin_user_session(request):
        raise HTTPException(401, "请先登录")


@asynccontextmanager
async def lifespan(_app):
    try:
        yield
    finally:
        from app.data_providers import custom
        for plugin in custom.list_plugins():
            if plugin.get("origin") == "external":
                custom.get_provider(plugin["name"]).close()


router = APIRouter(lifespan=lifespan, prefix="/api/custom/source-packages", tags=["source-packages"], dependencies=[Depends(require_management)])


async def read_upload(file: UploadFile):
    try:
        data = await file.read(packages.MAX_UPLOAD + 1)
        if len(data) > packages.MAX_UPLOAD:
            raise HTTPException(413, "插件包超过 20 MiB")
        return data
    finally:
        await file.close()


@router.post("/inspect")
async def inspect(file: Annotated[UploadFile, File()]):
    data = await read_upload(file)
    from starlette.concurrency import run_in_threadpool
    try:
        preview, _ = await run_in_threadpool(packages.inspect_archive, data)
        return preview
    except (ValueError, OSError, TypeError, yaml.YAMLError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/install")
async def install(file: Annotated[UploadFile, File()], digest: str = Form(...), revision: str | None = Form(None)):
    from starlette.concurrency import run_in_threadpool
    data = await read_upload(file)
    try:
        return await run_in_threadpool(packages.install, data, digest, revision)
    except (ValueError, OSError, TypeError, yaml.YAMLError) as exc:
        raise HTTPException(400, str(exc)) from exc


class Action(BaseModel):
    action: str
    reset_routes: bool = False


@router.post("/{name}/manage")
def manage(name: str, body: Action, request: Request):
    try:
        result = packages.manage(name, body.action, reset_routes=body.reset_routes)
    except (ValueError, OSError, TypeError, yaml.YAMLError) as exc:
        raise HTTPException(400, str(exc)) from exc
    from app.tickflow.policy import detect_capabilities
    request.app.state.capabilities = detect_capabilities()
    return result
