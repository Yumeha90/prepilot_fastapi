"""RBAC Schema（超管查看角色权限矩阵用）。"""
from pydantic import BaseModel, ConfigDict


class PermissionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    module: str
    description: str


class RoleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name_key: str
    description: str
    is_system: bool
    permissions: list[str]
    scopes: dict[str, str]


class RoleMatrixResponse(BaseModel):
    """角色 × 权限矩阵（本期只读）。"""

    roles: list[RoleRead]
    permission_catalog: list[PermissionRead]
