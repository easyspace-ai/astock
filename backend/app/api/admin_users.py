"""管理后台 — 用户账号与权限组 API。

端点（全部走权限码依赖, 未挂权限依赖的路由由 tests/test_rbac.py 拦截）:
  GET  /api/admin/permission-catalog   — 权限目录 + 权限组 (渲染矩阵用)
  GET  /api/admin/users               — 用户列表（分页, 不含密码）
  PATCH /api/admin/users/{id}         — 改权限组 / 启禁用
  GET  /api/admin/role-groups         — 权限组列表
  PUT  /api/admin/role-groups/{key}   — 改权限组名称/描述/权限矩阵
  GET  /api/admin/permission-audit    — 权限变更审计

安全约束（fail-closed）:
  - 只有超级管理员能改权限组、能把别人设为超级管理员。
  - 不能改自己的权限组与状态；不能移除/禁用最后一个超级管理员。
  - 未注册的权限码整批 400, 不静默丢弃。
  - 审计只记身份与 before/after, 不记密码/token。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.api.permissions import current_user, require_permission
from app.services import rbac, user_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin"])


class UserAdminPatch(BaseModel):
    perm_role: str | None = Field(default=None, description="权限组 key")
    is_active: bool | None = Field(default=None, description="是否启用")


class RoleGroupPatch(BaseModel):
    name: str | None = Field(default=None, max_length=40)
    description: str | None = Field(default=None, max_length=200)
    permissions: list[str] | None = Field(default=None, description="权限码全量集合")


def _is_super(actor: dict | None) -> bool:
    """无账号会话(API Token/内部调用)等价超级管理员。"""
    return actor is None or user_store.effective_perm_role(actor) == rbac.SUPER_ADMIN


@router.get("/permission-catalog", dependencies=[Depends(require_permission(rbac.ADMIN_ACCESS))])
def permission_catalog() -> dict:
    """权限目录 + 三组矩阵。前端不硬编码权限清单。"""
    return {
        "permissions": rbac.catalog(),
        "groups": rbac.GROUP_LABELS,
        "perm_roles": [
            {"key": key, "label": rbac.PERM_ROLE_LABELS[key]} for key in rbac.PERM_ROLES
        ],
        "role_groups": user_store.list_role_groups(),
    }


@router.get("/role-groups", dependencies=[Depends(require_permission(rbac.ADMIN_ACCESS))])
def role_groups() -> dict:
    return {"groups": user_store.list_role_groups()}


@router.put(
    "/role-groups/{key}",
    dependencies=[Depends(require_permission(rbac.ADMIN_ROLES_MANAGE))],
)
def update_role_group(key: str, body: RoleGroupPatch, request: Request) -> dict:
    actor = current_user(request)
    if not _is_super(actor):
        # 硬约束: 即使被授予 admin.roles_manage, 普通管理员也不能改权限组（防自提权）。
        raise HTTPException(status_code=403, detail="仅超级管理员可配置权限组")
    before = user_store.get_role_group(key)
    if before is None:
        raise HTTPException(status_code=404, detail="权限组不存在")
    try:
        updated = user_store.update_role_group(
            key,
            name=body.name,
            description=body.description,
            permissions=body.permissions,
            actor=actor,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    user_store.record_audit(
        actor=actor,
        action="group.update",
        target=key,
        detail=str({
            "before": before.get("permissions"),
            "after": updated.get("permissions"),
        }),
    )
    return {"group": updated}


@router.get("/users", dependencies=[Depends(require_permission(rbac.ADMIN_USERS_MANAGE))])
def users(
    q: str = Query(default="", description="邮箱模糊搜索"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
) -> dict:
    return user_store.list_users_admin(q=q, page=page, page_size=page_size)


@router.patch(
    "/users/{user_id}",
    dependencies=[Depends(require_permission(rbac.ADMIN_USERS_MANAGE))],
)
def update_user(user_id: int, body: UserAdminPatch, request: Request) -> dict:
    actor = current_user(request)
    if actor is not None and actor["id"] == user_id:
        raise HTTPException(status_code=409, detail="不能修改自己的权限组或启用状态")
    if body.perm_role is None and body.is_active is None:
        raise HTTPException(status_code=400, detail="没有需要修改的字段")
    try:
        updated = user_store.apply_user_admin_update(
            user_id,
            perm_role=body.perm_role,
            is_active=body.is_active,
            actor=actor,
        )
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return {"user": user_store.public_user(updated)}


@router.get(
    "/permission-audit",
    dependencies=[Depends(require_permission(rbac.ADMIN_AUDIT_VIEW))],
)
def permission_audit(limit: int = Query(default=50, ge=1, le=500)) -> dict:
    return {"items": user_store.list_audit(limit=limit)}
