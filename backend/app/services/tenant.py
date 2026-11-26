"""租户上下文 (Tenant Context)。

多用户二开: 认证/配额已按用户隔离 (user_store), 本模块把「用户私有业务数据」
(自选/监控规则/持仓/自定义信号/AI 报告/偏好设置等) 按 user_id 分区存储:

    全局模式(面板密码会话/无租户): data_dir/user_data/<file>   ← 兼容旧数据
    租户模式(邮箱账号登录)       : data_dir/user_data/users/<uid>/<file>

实现方式: contextvars.ContextVar 由 auth_middleware 在请求级注入, 服务层通过
priv_base() 取「私有数据根目录」。行情数据 (kline/enriched/因子矩阵/行情池)
不在 user_data 下, 保持全局共享, 不受本模块影响。
"""
from __future__ import annotations

import contextvars
from contextlib import contextmanager
from pathlib import Path

from app.config import settings

_UID: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "tsp_tenant_uid", default=None
)


def set_current_user(user_id: int | None) -> None:
    """请求级注入当前用户 id (None = 全局/面板密码模式)。"""
    _UID.set(user_id)


def current_user_id() -> int | None:
    return _UID.get()


@contextmanager
def user_scope(user_id: int | None):
    """临时进入某个用户的租户上下文 (后台任务按账号迭代时使用)。

    退出时复位到进入前的 uid —— 嵌套调用安全 (token.reset 而非 set(None)),
    避免内层把外层账号上下文清掉。线程各自的 ContextVar 互相独立, 因此
    ``ThreadPoolExecutor`` 里的任务仍需在任务内显式进入本上下文。
    """
    token = _UID.set(user_id)
    try:
        yield
    finally:
        _UID.reset(token)


def is_tenant() -> bool:
    return _UID.get() is not None


def _root(data_dir: Path | None = None) -> Path:
    """数据根: 调用方显式传入 (测试/多实例) 优先, 否则用全局 settings。"""
    return Path(data_dir) if data_dir is not None else settings.data_dir


def user_priv_dir(data_dir: Path | None = None) -> Path | None:
    """当前用户私有数据目录 (不含 user_data 前缀); 无租户返回 None。"""
    uid = _UID.get()
    if uid is None:
        return None
    d = _root(data_dir) / "user_data" / "users" / str(uid)
    d.mkdir(parents=True, exist_ok=True)
    return d


def priv_base(data_dir: Path | None = None) -> Path:
    """私有数据根: 租户模式 → 用户目录; 全局模式 → user_data (兼容旧数据)。"""
    d = user_priv_dir(data_dir)
    return d if d is not None else (_root(data_dir) / "user_data")


def priv_path(name: str, data_dir: Path | None = None) -> Path:
    """私有数据文件路径 (相对私有根), 如 priv_path('watchlist.parquet')。"""
    p = priv_base(data_dir) / name
    p.parent.mkdir(parents=True, exist_ok=True)
    return p
