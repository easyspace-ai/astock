"""多用户账号 API — 邮箱注册 + 密码登录 + 三级权限。

端点:
  GET  /api/accounts/status        — 系统初始化状态 + 当前登录用户
  POST /api/accounts/register      — 邮箱注册（首个用户自动成为管理员）
  POST /api/accounts/login         — 邮箱 + 密码登录（HttpOnly cookie 会话）
  POST /api/accounts/logout        — 注销当前会话
  GET  /api/accounts/me            — 当前用户信息（含角色 / 订阅 / 配额）
  POST /api/accounts/change-password — 修改密码（需已登录）
  POST /api/accounts/send-verify   — 发送邮箱验证码（需已登录）
  POST /api/accounts/verify-email  — 提交验证码完成邮箱验证（需已登录）
  POST /api/accounts/forgot-password — 忘记密码：发送重置验证码（已验证邮箱）
  POST /api/accounts/reset-password  — 验证码 + 新密码完成重置

安全:
  - 密码经 PBKDF2 哈希存储（见 services/user_store.py），不落明文。
  - 登录限流：同 IP 连续失败 5 次锁 5 分钟（内存计数）。
  - 会话 token 通过 HttpOnly cookie 下发（SESSION_TTL=30 天）。
  - 验证码 6 位数字、15 分钟有效、单次使用；发送接口按用户/IP 限流。
  - 忘记密码接口统一返回成功，避免邮箱枚举。
"""
from __future__ import annotations

import logging
import re
import time
from collections import defaultdict
from threading import Lock

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app import secrets_store
from app.services import email_adapter, preferences, user_store
from app.services.api_gateway import is_local_network as _is_local_network

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/accounts", tags=["accounts"])

COOKIE_NAME = "tf_user_session"
_COOKIE_MAX_AGE = user_store.SESSION_TTL

# 登录限流: { ip: (fail_count, lock_until_ts) }
_fail_counter: dict[str, tuple[int, float]] = defaultdict(lambda: (0, 0.0))
_fail_lock = Lock()
_MAX_FAILS = 5
_LOCK_SECONDS = 300

# 验证码发送限流: { key: last_send_ts }（key = email 或 ip:email）
_code_send_lock = Lock()
_CODE_SEND_MIN_INTERVAL = 60  # 同目标 60 秒内只允许发一次

_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


def _client_ip(request: Request) -> str:
    """取真实客户端 IP。

    安全关键: 仅当直连 peer 本身是回环/内网地址 (请求确实经过可信反代) 时,
    才采信 X-Forwarded-For; 否则公网请求可伪造内网 IP 绕过登录限流。
    """
    direct = request.client.host if request.client else ""
    xff = request.headers.get("x-forwarded-for")
    if xff and direct and _is_local_network(direct):
        return xff.split(",")[0].strip()
    return direct or "unknown"


def _check_rate_limit(ip: str) -> None:
    with _fail_lock:
        fails, lock_until = _fail_counter[ip]
        if lock_until and time.time() < lock_until:
            remain = int(lock_until - time.time())
            raise HTTPException(status_code=429, detail=f"尝试过于频繁，请 {remain} 秒后重试")
        if fails >= _MAX_FAILS:
            lock_until = time.time() + _LOCK_SECONDS
            _fail_counter[ip] = (0, lock_until)
            raise HTTPException(status_code=429, detail=f"失败次数过多，已锁定 {_LOCK_SECONDS // 60} 分钟")


def _record_fail(ip: str) -> None:
    with _fail_lock:
        fails, _ = _fail_counter[ip]
        _fail_counter[ip] = (fails + 1, 0.0)


def _record_success(ip: str) -> None:
    with _fail_lock:
        _fail_counter.pop(ip, None)


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=_COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        path="/",
    )


# ── 请求/响应模型 ────────────────────────────────────────────
class RegisterRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=254, description="注册邮箱")
    password: str = Field(..., min_length=6, max_length=128, description="密码(至少6位)")


class LoginRequest(BaseModel):
    email: str = Field(..., description="登录邮箱")
    password: str = Field(..., description="密码")


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(..., description="当前密码")
    new_password: str = Field(..., min_length=6, max_length=128, description="新密码(至少6位)")


class SendVerifyRequest(BaseModel):
    pass


class VerifyEmailRequest(BaseModel):
    code: str = Field(..., min_length=4, max_length=8, description="邮箱验证码")


class ForgotPasswordRequest(BaseModel):
    email: str = Field(..., description="注册邮箱")


class ResetPasswordRequest(BaseModel):
    email: str = Field(..., description="注册邮箱")
    code: str = Field(..., min_length=4, max_length=8, description="重置验证码")
    new_password: str = Field(..., min_length=6, max_length=128, description="新密码(至少6位)")


# ── 邮件发送 ────────────────────────────────────────────────────
def _send_account_email(to_email: str, subject: str, body: str) -> bool:
    """通过后台配置的 SMTP 通道向单个用户邮箱发信。

    复用「设置 → 邮件推送」中的 SMTP 配置（host/port/security/username/from +
    secrets_store 中的密码）。未配置或发送失败返回 False（由调用方决定降级）。
    """
    config = preferences.get_email_smtp_config()
    if not config.get("host") or not config.get("from_address"):
        return False
    smtp_password = secrets_store.get_email_smtp_password()
    return email_adapter.send_email(
        {**config, "to_addresses": [to_email]},
        smtp_password,
        subject,
        body,
    )


def _code_email_body(code: str, purpose: str) -> str:
    if purpose == user_store.RESET_PURPOSE:
        head = "你正在重置「Tick Stock Panel」账号密码"
        tip = "若并非你本人操作，请忽略此邮件。"
    else:
        head = "欢迎使用「Tick Stock Panel」"
        tip = "验证邮箱后可使用「忘记密码」自助找回账号。"
    return (
        f"{head}\n\n"
        f"你的验证码是：{code}\n\n"
        f"验证码 15 分钟内有效，请勿泄露给他人。\n{tip}"
    )


# ── 端点 ─────────────────────────────────────────────────────
@router.get("/status")
def status(request: Request) -> dict:
    """初始化状态 + 当前登录用户（前端据此决定显示注册/登录/直接进入）。"""
    user = _current_user(request)
    initialized = user_store.has_users()
    return {
        "initialized": initialized,
        "authenticated": user is not None,
        "user": user_store.public_user(user) if user else None,
    }


@router.post("/register", status_code=201)
def register(body: RegisterRequest, response: Response, request: Request) -> dict:
    """邮箱注册。系统首个注册用户自动成为管理员；其余默认免费用户。

    安全: 首个账号 = 管理员, 只允许本机/内网注册, 防止公网陌生人抢先注册
    拿下管理员权限。
    """
    email = body.email.strip().lower()
    if not _EMAIL_RE.match(email):
        raise HTTPException(status_code=422, detail="邮箱格式不正确")
    if not user_store.has_users() and not _is_local_network(_client_ip(request)):
        raise HTTPException(
            status_code=403,
            detail="首次注册管理员仅限本机或内网访问,请通过 SSH/本地浏览器操作",
        )
    try:
        user = user_store.create_user(email, body.password)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    token = user_store.create_session(user["id"])
    _set_session_cookie(response, token)
    return {"user": user_store.public_user(user)}


@router.post("/login")
def login(body: LoginRequest, response: Response, request: Request) -> dict:
    """邮箱 + 密码登录。失败限流：同 IP 错 5 次锁 5 分钟。"""
    ip = _client_ip(request)
    _check_rate_limit(ip)
    user = user_store.get_user_by_email(body.email)
    if (
        user is None
        or not user["is_active"]
        or not user_store.verify_password(body.password, user["password_salt"], user["password_hash"])
    ):
        _record_fail(ip)
        raise HTTPException(status_code=401, detail="邮箱或密码错误")
    _record_success(ip)
    user_store.touch_last_login(user["id"])
    token = user_store.create_session(user["id"])
    _set_session_cookie(response, token)
    return {"user": user_store.public_user(user)}


@router.post("/logout")
def logout(request: Request, response: Response) -> dict:
    token = request.cookies.get(COOKIE_NAME)
    if token:
        user_store.revoke_session(token)
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/me")
def me(request: Request) -> dict:
    user = _current_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail="未登录或会话已过期")
    return {"user": user_store.public_user(user)}


@router.post("/change-password")
def change_password(body: ChangePasswordRequest, request: Request) -> dict:
    user = _current_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail="未登录或会话已过期")
    if not user_store.verify_password(body.old_password, user["password_salt"], user["password_hash"]):
        raise HTTPException(status_code=400, detail="当前密码不正确")
    try:
        user_store.set_user_password(user["id"], body.new_password)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return {"ok": True}


# ── 邮箱验证 / 找回密码 ─────────────────────────────────────────
def _rate_limit_code_send(key: str) -> None:
    with _code_send_lock:
        last = _code_send_times.get(key, 0.0)
        now = time.time()
        if now - last < _CODE_SEND_MIN_INTERVAL:
            remain = int(_CODE_SEND_MIN_INTERVAL - (now - last))
            raise HTTPException(status_code=429, detail=f"发送过于频繁，请 {remain} 秒后重试")
        _code_send_times[key] = now


_code_send_times: dict[str, float] = {}


def _issue_code(user_id: int, purpose: str, to_email: str, subject: str) -> dict:
    """生成验证码并尝试邮件发送。

    Returns:
        {"sent": True}  — SMTP 已发出
        {"sent": False, "dev_code": "<6位>"} — 未配置 SMTP 时降级为开发模式，
            把验证码直接返回给调用方（仅限本机/内网演示；生产请配置邮件推送）。
    """
    code = user_store.create_verification_code(user_id, purpose)
    ok = _send_account_email(to_email, subject, _code_email_body(code, purpose))
    if ok:
        return {"sent": True}
    logger.warning("验证码邮件未配置/发送失败, 降级 dev_code (purpose=%s, user=%s)", purpose, user_id)
    return {"sent": False, "dev_code": code}


@router.post("/send-verify")
def send_verify(request: Request) -> dict:
    """发送邮箱验证码（需已登录）。"""
    user = _current_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail="未登录或会话已过期")
    if user_store.is_email_verified(user):
        return {"ok": True, "already_verified": True}
    _rate_limit_code_send(f"verify:{user['id']}")
    result = _issue_code(user["id"], user_store.VERIFY_PURPOSE, user["email"], "【Tick Stock Panel】邮箱验证码")
    return {"ok": True, **result}


@router.post("/verify-email")
def verify_email(body: VerifyEmailRequest, request: Request) -> dict:
    """提交验证码，完成邮箱验证（需已登录）。"""
    user = _current_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail="未登录或会话已过期")
    if user_store.is_email_verified(user):
        return {"ok": True, "already_verified": True}
    if not user_store.consume_verification_code(user["id"], user_store.VERIFY_PURPOSE, body.code):
        raise HTTPException(status_code=400, detail="验证码不正确或已过期")
    user_store.set_email_verified(user["id"])
    return {"ok": True}


@router.post("/forgot-password")
def forgot_password(body: ForgotPasswordRequest, request: Request) -> dict:
    """忘记密码：向已验证邮箱发送重置验证码。

    无论邮箱是否存在/已验证，都返回 ok（防枚举）。SMTP 未配置时仅在本机/内网
    请求下返回 dev_code（开发模式）—— 公网绝不能把重置码回给请求方，否则等于
    任意人可重置他人密码。
    """
    email = body.email.strip().lower()
    if not _EMAIL_RE.match(email):
        raise HTTPException(status_code=422, detail="邮箱格式不正确")
    user = user_store.get_user_by_email(email)
    dev_code: str | None = None
    if user is not None and user_store.is_email_verified(user):
        _rate_limit_code_send(f"reset:{email}")
        result = _issue_code(user["id"], user_store.RESET_PURPOSE, email, "【Tick Stock Panel】重置密码验证码")
        if not result["sent"] and _is_local_network(_client_ip(request)):
            dev_code = result.get("dev_code")
    return {"ok": True, "dev_code": dev_code}


@router.post("/reset-password")
def reset_password(body: ResetPasswordRequest) -> dict:
    """验证码 + 新密码完成重置。成功后吊销该用户全部会话。"""
    email = body.email.strip().lower()
    if not _EMAIL_RE.match(email):
        raise HTTPException(status_code=422, detail="邮箱格式不正确")
    user = user_store.get_user_by_email(email)
    if user is None:
        raise HTTPException(status_code=400, detail="验证码不正确或已过期")
    if not user_store.consume_verification_code(user["id"], user_store.RESET_PURPOSE, body.code):
        raise HTTPException(status_code=400, detail="验证码不正确或已过期")
    try:
        user_store.set_user_password(user["id"], body.new_password)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return {"ok": True}


# ── 内部工具 ─────────────────────────────────────────────────
def _current_user(request: Request) -> dict | None:
    """从 cookie 会话解析当前用户（供本模块端点使用）。

    中间件注入的 request.state.user 优先（避免重复查库）。
    """
    state_user = getattr(request.state, "user", None)
    if state_user is not None:
        return state_user
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    return user_store.get_user_by_token(token)
