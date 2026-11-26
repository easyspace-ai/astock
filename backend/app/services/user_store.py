"""多用户账号存储（SQLite）— 邮箱注册 + 账号等级 + 权限组。

字段语义必须分清（混用会导致越权或配额错乱）:
  - `role` = **账号等级 / 订阅**, 取值 admin / pro / free, 只决定配额与订阅降级。
  - `perm_role` = **权限组**, 取值 super_admin / admin / editor, 只决定能访问什么。
  权限判断一律走 `user_permissions()`, 不要再拿 `role == 'admin'` 当管理员判据。

设计：
  - 系统唯一认证体系（旧单密码 auth.json 模式已下线），多用户数据存
    data/user_data/users.db（SQLite，WAL 模式），互不干扰。
  - 密码用 PBKDF2-HMAC-SHA256 哈希（标准库 hashlib，参数与 auth.py 一致），
    加随机 salt，恒定时间比较防时序攻击。
  - 会话：随机 token（token_urlsafe），存 sessions 表（含过期时间），
    由 accounts API 下发 HttpOnly cookie；中间件按 token 查出当前用户。
  - 权限组：三个内置组, 组权限存 role_groups.permissions(JSON), 单行读取;
    变更走 audit 表留痕。未知组返回空权限集（fail-closed）。
  - 配额：ai_daily（AI 助手次数）、backtest_monthly（回测次数），
    按日/月自动重置；admin 等级配额为 -1（不限）。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets as _secrets
import sqlite3
import threading
import time
from datetime import date
from pathlib import Path
from typing import Any

from app.services import rbac

logger = logging.getLogger(__name__)

# ── 角色常量 ────────────────────────────────────────────────
ROLE_ADMIN = "admin"
ROLE_PRO = "pro"
ROLE_FREE = "free"
ROLES = (ROLE_ADMIN, ROLE_PRO, ROLE_FREE)

# ── PBKDF2 参数 ───────────────────────────────────────────
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
                backtest_monthly_reset TEXT,
                perm_role TEXT,
                last_login_at REAL
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
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS role_groups (
                key          TEXT PRIMARY KEY,
                name         TEXT NOT NULL,
                description  TEXT NOT NULL DEFAULT '',
                permissions  TEXT NOT NULL,
                is_builtin   INTEGER NOT NULL DEFAULT 1,
                updated_at   REAL NOT NULL,
                updated_by   INTEGER
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS permission_audit (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                actor_id    INTEGER,
                actor_email TEXT,
                action      TEXT NOT NULL,
                target      TEXT NOT NULL,
                detail      TEXT NOT NULL DEFAULT '',
                created_at  REAL NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_permission_audit_time ON permission_audit(created_at)"
        )
    _migrate_schema()


def _migrate_schema() -> None:
    """列级迁移：补历史列、播种权限组、回填 perm_role。

    幂等: 每次启动都会执行; 已有的组权限不会被默认值覆盖（管理员改过的矩阵优先）。
    """
    with _connect() as conn:
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
        if "email_verified" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN email_verified INTEGER NOT NULL DEFAULT 0")
        if "perm_role" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN perm_role TEXT")
        if "last_login_at" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN last_login_at REAL")
        scols = {row["name"] for row in conn.execute("PRAGMA table_info(sessions)").fetchall()}
        if "created_at" not in scols:
            conn.execute("ALTER TABLE sessions ADD COLUMN created_at REAL NOT NULL DEFAULT 0")
            # 历史行按 expires_at - TTL 回填近似创建时间
            conn.execute("UPDATE sessions SET created_at = expires_at - ? WHERE created_at = 0", (SESSION_TTL,))

        # 播种内置权限组: 只插入缺失的 key, 不覆盖已调整过的矩阵。
        now = time.time()
        for key, group in rbac.DEFAULT_GROUPS.items():
            conn.execute(
                "INSERT OR IGNORE INTO role_groups"
                " (key, name, description, permissions, is_builtin, updated_at)"
                " VALUES (?, ?, ?, ?, 1, ?)",
                (
                    key,
                    str(group["name"]),
                    str(group["description"]),
                    json.dumps(group["permissions"], ensure_ascii=False),
                    now,
                ),
            )

        # 回填: 历史 admin 账号 -> 超级管理员, 其余 -> 编辑者。
        conn.execute(
            "UPDATE users SET perm_role = ? WHERE perm_role IS NULL AND role = ?",
            (rbac.SUPER_ADMIN, ROLE_ADMIN),
        )
        conn.execute(
            "UPDATE users SET perm_role = ? WHERE perm_role IS NULL",
            (rbac.DEFAULT_PERM_ROLE,),
        )

        _migrate_legacy_analysis_permissions(conn)


def _migrate_legacy_analysis_permissions(conn: sqlite3.Connection) -> None:
    """把旧 `page.analysis`（分析中心/扩展页一把梭）展开成当前具体页面权限。

    只在旧码仍存在时执行；迁移后新增的分析菜单/扩展页默认不授权，由管理员显式勾选。
    """
    from app.config import settings
    from app.services import menu_registry

    target = menu_registry.dynamic_page_permission_keys(settings.data_dir)
    if not target:
        return
    changed = 0
    for row in conn.execute("SELECT key, permissions FROM role_groups").fetchall():
        try:
            perms = json.loads(row["permissions"] or "[]")
        except (TypeError, ValueError):
            continue
        if not isinstance(perms, list) or rbac.PAGE_ANALYSIS not in perms:
            continue
        expanded = [p for p in perms if p != rbac.PAGE_ANALYSIS]
        for key in target:
            if key not in expanded:
                expanded.append(key)
        conn.execute(
            "UPDATE role_groups SET permissions = ? WHERE key = ?",
            (json.dumps(expanded, ensure_ascii=False), row["key"]),
        )
        changed += 1
    if changed:
        logger.info("已迁移旧 page.analysis 权限组: %s 个", changed)


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


def health_check() -> None:
    """readiness 轻量探针: 用户库存在且可读; 不存在视为首次启动合法状态。

    不调用 _init_schema/_connect: 避免健康检查每 30 秒触发 WAL/建表写操作,
    阻塞正在登录/改权限的请求。库损坏时 SELECT 会抛异常, 由调用方判定未就绪。
    """
    path = _db_path()
    if not path.exists():
        return
    conn = sqlite3.connect(str(path), timeout=2)
    try:
        conn.execute("SELECT 1 FROM users LIMIT 1").fetchone()
    finally:
        conn.close()


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
    perm_role = rbac.default_perm_role_for_account_role(role)
    with _connect() as conn:
        try:
            cur = conn.execute(
                """
                INSERT INTO users
                    (email, password_hash, password_salt, role, perm_role, created_at,
                     ai_daily_limit, backtest_monthly_limit)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (email, hash_hex, salt_hex, role, perm_role, time.time(),
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
# 权限组 / 权限判定
# ================================================================
def _row_to_group(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    d = dict(row)
    try:
        perms = json.loads(d.get("permissions") or "[]")
    except (TypeError, ValueError):
        # 脏数据不静默当全权限: 记日志并按空集处理 (fail-closed)
        logger.warning("role_groups.permissions 非法 JSON (key=%s)", d.get("key"))
        perms = []
    d["permissions"] = [p for p in perms if isinstance(p, str) and rbac.is_valid_permission(p)] if isinstance(perms, list) else []
    d["is_builtin"] = bool(d.get("is_builtin"))
    return d


def list_role_groups() -> list[dict[str, Any]]:
    _init_schema()
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM role_groups ORDER BY key ASC").fetchall()
    return [g for g in (_row_to_group(r) for r in rows) if g is not None]


def get_role_group(key: str) -> dict[str, Any] | None:
    _init_schema()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM role_groups WHERE key = ?", (key,)).fetchone()
    return _row_to_group(row)


def permissions_for_group(key: str) -> frozenset[str]:
    """权限组 -> 权限码集合。未知组返回空集 (fail-closed)。"""
    group = get_role_group(key)
    return frozenset(group["permissions"]) if group else frozenset()


def effective_perm_role(user: dict[str, Any]) -> str:
    """用户实际生效的权限组。

    有值时按值解析（未知组名会解析成空权限, fail-closed）；只有字段为空的历史数据
    才按账号等级兜底。迁移会把历史数据回填成非空, 此处兜底只是防御。
    """
    perm_role = user.get("perm_role")
    if perm_role:
        return str(perm_role)
    return rbac.default_perm_role_for_account_role(user.get("role"))


def user_permissions(user: dict[str, Any] | None) -> frozenset[str]:
    """当前用户拥有的权限码。

    `user is None` 只有两种情况: 无账号会话的 API Token/内部调用, 或系统
    尚未初始化时的本机请求。两者都是面板主人, 按全权限处理, 与改造前一致。
    """
    if user is None:
        return rbac.all_permissions()
    if not user.get("is_active", True):
        return frozenset()
    return permissions_for_group(effective_perm_role(user))


def update_role_group(
    key: str,
    *,
    name: str | None = None,
    description: str | None = None,
    permissions: list[str] | None = None,
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """更新内置权限组。非法权限码整批拒绝; 超管组必须保留关键权限。"""
    _init_schema()
    group = get_role_group(key)
    if group is None:
        raise ValueError("权限组不存在")
    if permissions is not None:
        checked = rbac.validate_permissions(permissions)
        if key == rbac.SUPER_ADMIN:
            missing = [k for k in rbac.SUPER_ADMIN_REQUIRED if k not in checked]
            if missing:
                raise ValueError(f"超级管理员组不能移除: {', '.join(missing)}")
    else:
        checked = None
    sets: list[str] = []
    params: list[Any] = []
    if name is not None:
        sets.append("name = ?")
        params.append(name.strip() or group["name"])
    if description is not None:
        sets.append("description = ?")
        params.append(description.strip())
    if checked is not None:
        sets.append("permissions = ?")
        params.append(json.dumps(checked, ensure_ascii=False))
    sets.append("updated_at = ?")
    params.append(time.time())
    sets.append("updated_by = ?")
    params.append(actor["id"] if actor else None)
    params.append(key)
    with _connect() as conn:
        conn.execute(f"UPDATE role_groups SET {', '.join(sets)} WHERE key = ?", params)
    updated = get_role_group(key)
    if updated is None:
        raise ValueError("权限组不存在")
    return updated


def _snapshot(user: dict[str, Any] | None) -> dict[str, Any]:
    if not user:
        return {}
    return {
        "email": user.get("email"),
        "role": user.get("role"),
        "perm_role": effective_perm_role(user),
        "is_active": bool(user.get("is_active", True)),
    }


def record_audit(
    *,
    actor: dict[str, Any] | None,
    action: str,
    target: str,
    detail: str = "",
) -> None:
    """权限变更审计。只记身份与 before/after, 绝不记密码/token。"""
    _init_schema()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO permission_audit"
            " (actor_id, actor_email, action, target, detail, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                actor["id"] if actor else None,
                actor.get("email") if actor else None,
                action,
                target,
                detail,
                time.time(),
            ),
        )


def list_audit(limit: int = 100) -> list[dict[str, Any]]:
    _init_schema()
    limit = max(1, min(int(limit), 500))
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM permission_audit ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def count_super_admins() -> int:
    _init_schema()
    with _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM users WHERE perm_role = ? AND is_active = 1",
            (rbac.SUPER_ADMIN,),
        ).fetchone()
    return int(row["n"] if row else 0)


def apply_user_admin_update(
    user_id: int,
    *,
    perm_role: str | None = None,
    is_active: bool | None = None,
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """管理员改用户权限组/启禁用。

    约束在这里一次性校验(持有锁, 避免并发把最后一个超管改没):
      - 不能改自己 (由 API 层拦, 这里也兜底? 不, 这里只保证不变空);
      - 不能移除/禁用最后一个超级管理员;
      - 目标当前是超管或要设为超管, 需要 actor 是超级管理员。
    违反抛 ValueError(调用方映射 409/403)。
    """
    _init_schema()
    actor_is_super = actor is None or effective_perm_role(actor) == rbac.SUPER_ADMIN
    with _lock:
        user = get_user(user_id)
        if user is None:
            raise KeyError("用户不存在")
        current_perm = effective_perm_role(user)
        if perm_role is not None and perm_role not in rbac.PERM_ROLES:
            raise ValueError(f"非法权限组: {perm_role}")
        touches_super = current_perm == rbac.SUPER_ADMIN or perm_role == rbac.SUPER_ADMIN
        if touches_super and not actor_is_super:
            raise ValueError("只有超级管理员可以授予或调整超级管理员")
        losing_super = (
            current_perm == rbac.SUPER_ADMIN
            and ((perm_role is not None and perm_role != rbac.SUPER_ADMIN)
                 or is_active is False)
        )
        if losing_super and count_super_admins() <= 1:
            raise ValueError("系统必须保留至少一个启用的超级管理员")
        sets: list[str] = []
        params: list[Any] = []
        if perm_role is not None:
            sets.append("perm_role = ?")
            params.append(perm_role)
        if is_active is not None:
            sets.append("is_active = ?")
            params.append(1 if is_active else 0)
        if sets:
            params.append(user_id)
            with _connect() as conn:
                conn.execute(f"UPDATE users SET {', '.join(sets)} WHERE id = ?", params)
    updated = get_user(user_id)
    if updated is None:
        raise KeyError("用户不存在")
    record_audit(
        actor=actor,
        action="user.admin_update",
        target=str(user_id),
        detail=json.dumps(
            {"before": _snapshot(user), "after": _snapshot(updated)}, ensure_ascii=False
        ),
    )
    return updated


def list_users_admin(q: str = "", page: int = 1, page_size: int = 20) -> dict[str, Any]:
    """管理后台用户列表(分页, 不含密码字段)。"""
    _init_schema()
    page = max(1, int(page))
    page_size = max(1, min(int(page_size), 200))
    where = ""
    params: list[Any] = []
    q = (q or "").strip().lower()
    if q:
        where = "WHERE email LIKE ?"
        params.append(f"%{q}%")
    with _connect() as conn:
        total = int(conn.execute(f"SELECT COUNT(*) AS n FROM users {where}", params).fetchone()["n"])
        rows = conn.execute(
            f"SELECT * FROM users {where} ORDER BY id ASC LIMIT ? OFFSET ?",
            [*params, page_size, (page - 1) * page_size],
        ).fetchall()
    users = [_row_to_user(r) for r in rows]
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [
            {
                "id": u["id"],
                "email": u["email"],
                "role": effective_role(u),
                "perm_role": effective_perm_role(u),
                "perm_role_name": rbac.PERM_ROLE_LABELS.get(effective_perm_role(u), ""),
                "is_active": bool(u.get("is_active", True)),
                "email_verified": bool(u.get("email_verified")),
                "created_at": u.get("created_at"),
                "last_login_at": u.get("last_login_at"),
            }
            for u in users
            if u is not None
        ],
    }


def touch_last_login(user_id: int) -> None:
    _init_schema()
    with _connect() as conn:
        conn.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (time.time(), user_id))


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
    perm_role = effective_perm_role(user)
    perms = user_permissions(user)
    return {
        "id": user["id"],
        "email": user["email"],
        "role": effective_role(user),
        "perm_role": perm_role,
        "perm_role_name": rbac.PERM_ROLE_LABELS.get(perm_role, perm_role),
        "permissions": sorted(perms),
        "is_active": bool(user.get("is_active", True)),
        "email_verified": bool(user.get("email_verified")),
        "subscription_expires_at": user.get("subscription_expires_at"),
        "created_at": user.get("created_at"),
        "is_admin": rbac.ADMIN_ACCESS in perms,
        "quotas": get_quotas(user),
    }


def run_schema_migrations() -> None:
    """应用启动时调用：建表 + 未来字段迁移入口。"""
    _init_schema()
    _migrate_schema()
