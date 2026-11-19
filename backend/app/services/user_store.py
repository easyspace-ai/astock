"""多用户账号存储（SQLite）— 邮箱注册 + 三级权限（admin / pro / free）骨架。

设计：
  - 独立于 TSP 现有单密码体系（services/auth.py，auth.json），多用户数据存
    data/user_data/users.db（SQLite，WAL 模式），互不干扰。
  - 密码用 PBKDF2-HMAC-SHA256 哈希（标准库 hashlib，参数与 auth.py 一致），
    加随机 salt，恒定时间比较防时序攻击。
  - 会话：随机 token（token_urlsafe），存 sessions 表（含过期时间），
    由 accounts API 下发 HttpOnly cookie；中间件按 token 查出当前用户。
  - 角色：admin（系统内置，全权限）/ pro（付费订阅，全功能按配额）/
    free（注册默认，基础功能按配额）。pro 到期后查询时自动按 free 处理。
  - 配额：ai_daily（AI 助手次数）、backtest_monthly（回测次数），
    按日/月自动重置；admin 配额为 -1（不限）。
"""
from __future__ import annotations

import hashlib
import logging
import os
import secrets as _secrets
import sqlite3
import threading
import time
from datetime import date
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── 角色常量 ────────────────────────────────────────────────
ROLE_ADMIN = "admin"
ROLE_PRO = "pro"
ROLE_FREE = "free"
ROLES = (ROLE_ADMIN, ROLE_PRO, ROLE_FREE)

# ── PBKDF2 参数（与 services/auth.py 一致）──────────────────
_PBKDF2_ITER = 200_000
_SALT_LEN = 16
_TOKEN_BYTES = 32
SESSION_TTL = 30 * 24 * 3600  # 30 天

# ── 配额默认值（可按需调整）─────────────────────────────────
QUOTA_DEFAULTS: dict[str, dict[str, int]] = {
    ROLE_FREE: {"ai_daily": 10, "backtest_monthly": 10},
    ROLE_PRO: {"ai_daily": 100, "backtest_monthly": 200},
    ROLE_ADMIN: {"ai_daily": -1, "backtest_monthly": -1},  # -1 = 不限
}

_lock = threading.Lock()


def _db_path() -> Path:
    from app.config import settings
    p = settings.data_dir / "user_data" / "users.db"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path(), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _init_schema() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                password_salt TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'free',
                is_active INTEGER NOT NULL DEFAULT 1,
                subscription_expires_at REAL,
                created_at REAL NOT NULL,
                ai_daily_used INTEGER NOT NULL DEFAULT 0,
                ai_daily_limit INTEGER NOT NULL DEFAULT 10,
                ai_daily_reset TEXT,
                backtest_monthly_used INTEGER NOT NULL DEFAULT 0,
                backtest_monthly_limit INTEGER NOT NULL DEFAULT 10,
                backtest_monthly_reset TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                expires_at REAL NOT NULL,
                created_at REAL NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS verification_codes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                purpose TEXT NOT NULL,
                code TEXT NOT NULL,
                expires_at REAL NOT NULL,
                used INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_vcodes_user ON verification_codes(user_id, purpose)"
        )
    _migrate_schema()


def _migrate_schema() -> None:
    """列级迁移：为历史库补 email_verified / sessions.created_at 列。"""
    with _connect() as conn:
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
        if "email_verified" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN email_verified INTEGER NOT NULL DEFAULT 0")
        scols = {row["name"] for row in conn.execute("PRAGMA table_info(sessions)").fetchall()}
        if "created_at" not in scols:
            conn.execute("ALTER TABLE sessions ADD COLUMN created_at REAL NOT NULL DEFAULT 0")
            # 历史行按 expires_at - TTL 回填近似创建时间
            conn.execute("UPDATE sessions SET created_at = expires_at - ? WHERE created_at = 0", (SESSION_TTL,))


# ================================================================
# 密码工具
# ================================================================
def hash_password(password: str) -> tuple[str, str]:
    salt = os.urandom(_SALT_LEN)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITER)
    return salt.hex(), dk.hex()


def verify_password(password: str, salt_hex: str, hash_hex: str) -> bool:
    try:
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except ValueError:
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITER)
    return _secrets.compare_digest(actual, expected)


# ================================================================
# 用户 CRUD
# ================================================================
def has_users() -> bool:
    _init_schema()
    with _connect() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()
        return bool(row["n"])


def create_user(email: str, password: str, role: str = ROLE_FREE) -> dict[str, Any]:
    """创建用户。返回用户 dict；邮箱重复抛 ValueError。"""
    _init_schema()
    email = email.strip().lower()
    if len(password) < 6:
        raise ValueError("密码至少 6 位")
    salt_hex, hash_hex = hash_password(password)
    # 首个用户自动成为管理员（仅当系统中还没有任何用户时）
    if not has_users():
        role = ROLE_ADMIN
        logger.info("first user registered as admin: %s", email)
    with _connect() as conn:
        try:
            cur = conn.execute(
                """
                INSERT INTO users
                    (email, password_hash, password_salt, role, created_at,
                     ai_daily_limit, backtest_monthly_limit)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (email, hash_hex, salt_hex, role, time.time(),
                 QUOTA_DEFAULTS[role]["ai_daily"], QUOTA_DEFAULTS[role]["backtest_monthly"]),
            )
            user_id = cur.lastrowid
        except sqlite3.IntegrityError:
            raise ValueError("该邮箱已注册") from None
    return get_user(user_id)


def get_user(user_id: int) -> dict[str, Any] | None:
    _init_schema()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return _row_to_user(row)


def get_user_by_email(email: str) -> dict[str, Any] | None:
    _init_schema()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email.strip().lower(),)).fetchone()
    return _row_to_user(row)


def list_users() -> list[dict[str, Any]]:
    _init_schema()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM users ORDER BY id ASC"
        ).fetchall()
    return [_row_to_user(r) for r in rows]


def _row_to_user(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    d = dict(row)
    d["is_active"] = bool(d.get("is_active"))
    return d


def update_user(
    user_id: int,
    *,
    role: str | None = None,
    is_active: bool | None = None,
    subscription_expires_at: float | None = None,
) -> dict[str, Any] | None:
    """管理员更新用户字段。role 变更时同步刷新配额上限。"""
    _init_schema()
    sets: list[str] = []
    params: list[Any] = []
    if role is not None:
        if role not in ROLES:
            raise ValueError(f"非法角色: {role}")
        sets.append("role = ?")
        params.append(role)
        q = QUOTA_DEFAULTS.get(role, QUOTA_DEFAULTS[ROLE_FREE])
        sets.append("ai_daily_limit = ?")
        params.append(q["ai_daily"])
        sets.append("backtest_monthly_limit = ?")
        params.append(q["backtest_monthly"])
    if is_active is not None:
        sets.append("is_active = ?")
        params.append(1 if is_active else 0)
    if subscription_expires_at is not None:
        sets.append("subscription_expires_at = ?")
        params.append(subscription_expires_at)
    if not sets:
        return get_user(user_id)
    params.append(user_id)
    with _connect() as conn:
        conn.execute(f"UPDATE users SET {', '.join(sets)} WHERE id = ?", params)
    return get_user(user_id)


def set_user_password(user_id: int, new_password: str) -> None:
    _init_schema()
    if len(new_password) < 6:
        raise ValueError("密码至少 6 位")
    salt_hex, hash_hex = hash_password(new_password)
    with _connect() as conn:
        conn.execute(
            "UPDATE users SET password_hash = ?, password_salt = ? WHERE id = ?",
            (hash_hex, salt_hex, user_id),
        )
    # 改密码后吊销该用户全部会话
    with _connect() as conn:
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))


# ================================================================
# 角色 / 订阅判断
# ================================================================
def effective_role(user: dict[str, Any]) -> str:
    """实际生效角色：pro 订阅到期后按 free 处理；禁用用户按 free 收紧。"""
    if not user.get("is_active", True):
        return ROLE_FREE
    role = user.get("role")
    if role == ROLE_PRO:
        exp = user.get("subscription_expires_at")
        if exp is not None and time.time() > exp:
            return ROLE_FREE  # 已到期，自动降级
    return role or ROLE_FREE


def grant_pro(user_id: int, days: int) -> dict[str, Any] | None:
    """开通专业版：设 role=pro 并从当前时刻起延长订阅到期日。"""
    _init_schema()
    user = get_user(user_id)
    if user is None:
        return None
    base = user.get("subscription_expires_at") or 0.0
    now = time.time()
    start = max(base, now)  # 未到期则续期
    new_exp = start + days * 86400
    return update_user(user_id, role=ROLE_PRO, subscription_expires_at=new_exp)


# ================================================================
# 会话
# ================================================================
def create_session(user_id: int) -> str:
    _init_schema()
    token = _secrets.token_urlsafe(_TOKEN_BYTES)
    expire = time.time() + SESSION_TTL
    with _connect() as conn:
        conn.execute(
            "INSERT INTO sessions (token, user_id, expires_at, created_at) VALUES (?, ?, ?, ?)",
            (token, user_id, expire, time.time()),
        )
    return token


def count_active_users(days: int = 7) -> int:
    """近 N 天登录过的去重用户数（按 session 创建时间）。"""
    _init_schema()
    cutoff = time.time() - days * 86400
    with _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(DISTINCT user_id) AS n FROM sessions WHERE created_at >= ?",
            (cutoff,),
        ).fetchone()
        return int(row["n"] if row else 0)


def get_user_by_token(token: str) -> dict[str, Any] | None:
    """按会话 token 返回用户（过期清理，禁用/删除返回 None）。"""
    if not token:
        return None
    _init_schema()
    now = time.time()
    with _connect() as conn:
        row = conn.execute(
            "SELECT user_id, expires_at FROM sessions WHERE token = ?", (token,)
        ).fetchone()
        if row is None:
            return None
        if now > row["expires_at"]:
            conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
            return None
    user = get_user(row["user_id"])
    if user is None or not user.get("is_active", True):
        return None
    return user


def revoke_session(token: str) -> None:
    _init_schema()
    with _connect() as conn:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))


def revoke_all_sessions(user_id: int) -> None:
    _init_schema()
    with _connect() as conn:
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))


# ================================================================
# 邮箱验证 / 验证码
# ================================================================
VERIFY_PURPOSE = "verify_email"
RESET_PURPOSE = "reset_password"
_VERIFY_CODE_TTL = 15 * 60  # 15 分钟
_VERIFY_CODE_LEN = 6


def create_verification_code(user_id: int, purpose: str, ttl: int = _VERIFY_CODE_TTL) -> str:
    """生成 6 位数字验证码并入库（同用户同用途旧码作废）。"""
    _init_schema()
    code = "".join(str(_secrets.randbelow(10)) for _ in range(_VERIFY_CODE_LEN))
    now = time.time()
    with _connect() as conn:
        conn.execute(
            "DELETE FROM verification_codes WHERE user_id = ? AND purpose = ?",
            (user_id, purpose),
        )
        conn.execute(
            "INSERT INTO verification_codes (user_id, purpose, code, expires_at, used, created_at)"
            " VALUES (?, ?, ?, ?, 0, ?)",
            (user_id, purpose, code, now + ttl, now),
        )
    return code


def consume_verification_code(user_id: int, purpose: str, code: str) -> bool:
    """校验并单次消费验证码。成功返回 True（已标记 used）。"""
    _init_schema()
    code = (code or "").strip()
    if not code:
        return False
    with _connect() as conn:
        row = conn.execute(
            "SELECT id, expires_at, used FROM verification_codes"
            " WHERE user_id = ? AND purpose = ? AND code = ?",
            (user_id, purpose, code),
        ).fetchone()
        if row is None or row["used"] or time.time() > row["expires_at"]:
            return False
        conn.execute("UPDATE verification_codes SET used = 1 WHERE id = ?", (row["id"],))
    return True


def is_email_verified(user: dict[str, Any]) -> bool:
    return bool(user.get("email_verified"))


def set_email_verified(user_id: int) -> None:
    _init_schema()
    with _connect() as conn:
        conn.execute("UPDATE users SET email_verified = 1 WHERE id = ?", (user_id,))


# ================================================================
# 配额
# ================================================================
def _today() -> str:
    return date.today().isoformat()


def _month() -> str:
    return date.today().strftime("%Y-%m")


def _reset_if_needed(conn: sqlite3.Connection, user: dict[str, Any]) -> None:
    """跨日/跨月自动重置配额计数。"""
    updates: list[tuple[Any, int]] = []
    if user.get("ai_daily_reset") != _today():
        updates.append((_today(), user["id"]))
        conn.execute("UPDATE users SET ai_daily_used = 0 WHERE id = ?", (user["id"],))
    if user.get("backtest_monthly_reset") != _month():
        conn.execute("UPDATE users SET backtest_monthly_used = 0 WHERE id = ?", (user["id"],))
    for col, val in (("ai_daily_reset", _today()), ("backtest_monthly_reset", _month())):
        conn.execute(f"UPDATE users SET {col} = ? WHERE id = ?", (val, user["id"]))


def get_quotas(user: dict[str, Any]) -> dict[str, dict[str, int]]:
    """返回生效配额（含自动重置与到期降级后的限值）。"""
    _init_schema()
    with _connect() as conn:
        _reset_if_needed(conn, user)
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone()
    u = _row_to_user(row) or user
    role = effective_role(u)
    base = QUOTA_DEFAULTS.get(role, QUOTA_DEFAULTS[ROLE_FREE])
    return {
        "ai_daily": {
            "limit": u.get("ai_daily_limit") if role != ROLE_FREE or u.get("ai_daily_limit") else base["ai_daily"],
            "used": u.get("ai_daily_used", 0),
            "unlimited": (u.get("ai_daily_limit") or 0) < 0,
        },
        "backtest_monthly": {
            "limit": u.get("backtest_monthly_limit") if u.get("backtest_monthly_limit") is not None else base["backtest_monthly"],
            "used": u.get("backtest_monthly_used", 0),
            "unlimited": (u.get("backtest_monthly_limit") or 0) < 0,
        },
    }


def consume_quota(user: dict[str, Any], kind: str) -> tuple[bool, str]:
    """消耗一次配额。kind: ai_daily | backtest_monthly。

    Returns:
        (ok, reason) — ok=False 时 reason 说明原因（超出配额/非法类型）。
    """
    _init_schema()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone()
        u = _row_to_user(row)
        if u is None:
            return False, "用户不存在"
        _reset_if_needed(conn, u)
        role = effective_role(u)
        base = QUOTA_DEFAULTS.get(role, QUOTA_DEFAULTS[ROLE_FREE])
        if kind == "ai_daily":
            limit = u.get("ai_daily_limit") if u.get("ai_daily_limit") is not None else base["ai_daily"]
            used = u.get("ai_daily_used", 0)
        elif kind == "backtest_monthly":
            limit = u.get("backtest_monthly_limit") if u.get("backtest_monthly_limit") is not None else base["backtest_monthly"]
            used = u.get("backtest_monthly_used", 0)
        else:
            return False, f"未知配额类型: {kind}"
        if limit is not None and limit < 0:
            return True, ""  # 不限
        if used >= limit:
            return False, f"超出配额（{kind} {used}/{limit}），请联系管理员升级"
        col = "ai_daily_used" if kind == "ai_daily" else "backtest_monthly_used"
        conn.execute(f"UPDATE users SET {col} = {col} + 1 WHERE id = ?", (u["id"],))
    return True, ""


def public_user(user: dict[str, Any]) -> dict[str, Any]:
    """对外暴露的用户信息（绝不包含密码字段）。"""
    return {
        "id": user["id"],
        "email": user["email"],
        "role": effective_role(user),
        "is_active": bool(user.get("is_active", True)),
        "email_verified": bool(user.get("email_verified")),
        "subscription_expires_at": user.get("subscription_expires_at"),
        "created_at": user.get("created_at"),
        "is_admin": effective_role(user) == ROLE_ADMIN,
        "quotas": get_quotas(user),
    }


def run_schema_migrations() -> None:
    """应用启动时调用：建表 + 未来字段迁移入口。"""
    _init_schema()
    _migrate_schema()
