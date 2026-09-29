"""系统管理：角色权限矩阵（本期只读，PRD 3.1.3）。"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi import APIRouter, Depends

from app.core.deps import get_db, require_perm
from app.models.rbac import Permission, Role, RoleDataScope
from app.schemas.rbac import RoleMatrixResponse

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/roles", response_model=RoleMatrixResponse)
async def role_matrix(db: AsyncSession = Depends(get_db), _=Depends(require_perm("system:role_view"))) -> RoleMatrixResponse:
    roles = list(await db.scalars(select(Role).order_by(Role.id)))
    scopes_by_role: dict[int, dict[str, str]] = {}
    for row in await db.scalars(select(RoleDataScope)):
        scopes_by_role.setdefault(row.role_id, {})[row.resource] = row.scope

    return RoleMatrixResponse(
        roles=[
            {
                "id": role.id,
                "code": role.code,
                "name_key": role.name_key,
                "description": role.description,
                "is_system": role.is_system,
                "permissions": sorted(p.code for p in role.permissions),
                "scopes": scopes_by_role.get(role.id, {}),
            }
            for role in roles
        ],
        permission_catalog=[
            {"code": p.code, "module": p.module, "description": p.description}
            for p in await db.scalars(select(Permission).order_by(Permission.id))
        ],
    )
