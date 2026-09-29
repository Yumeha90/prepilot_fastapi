import apiClient from './client'

export interface PermissionRead {
  code: string
  module: string
  description: string
}

export interface RoleRead {
  id: number
  code: string
  name_key: string
  description: string
  is_system: boolean
  permissions: string[]
  scopes: Record<string, string>
}

export interface RoleMatrixResponse {
  roles: RoleRead[]
  permission_catalog: PermissionRead[]
}

/** 角色 × 权限矩阵（本期只读） */
export async function fetchRoleMatrix(): Promise<RoleMatrixResponse> {
  const { data } = await apiClient.get<RoleMatrixResponse>('/api/admin/roles')
  return data
}
