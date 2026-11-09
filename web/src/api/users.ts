// users.ts — 当前登录用户（/v1/me）与用户管理（/v1/users）的类型封装。
import { request } from './client';

// SystemRole 是平台权限档位，与后端 auth_service._ROLES 一一对应。
//   admin     = 全部系统功能 + 平台配置 + 用户管理
//   developer = 使用全部业务功能（会话、可写工具）
//   viewer    = 只读 + 受限会话（LLM 只拿到只读工具集）
export type SystemRole = 'admin' | 'developer' | 'viewer';
export type UserStatus = 'active' | 'disabled';

// OrgMembership — me.memberships[] 用，描述当前用户在某个组织里的角色。
export type OrgMembership = {
  org_id: number;
  org_name: string;
  role: 'org_admin' | 'member' | 'viewer';
};

// Me — /v1/me 返回；前端用来分歧 admin-only UI / 默认组织拼接。
export type Me = {
  id: number;
  email: string;
  display_name: string;
  phone: string;
  role: SystemRole;
  status: UserStatus;
  memberships: OrgMembership[];
};

export function getMe(): Promise<Me> {
  return request<Me>('GET', '/me');
}

// ManagedUser — /v1/users 列表项；角色与状态和后端表一一对应。
export type ManagedUser = {
  id: number;
  email: string;
  display_name: string;
  role: SystemRole;
  status: UserStatus;
};

export function listUsers(): Promise<ManagedUser[]> {
  return request<ManagedUser[]>('GET', '/users');
}

export function createUser(input: {
  email: string;
  display_name: string;
  password: string;
  role: SystemRole;
}): Promise<ManagedUser> {
  return request<ManagedUser>('POST', '/users', input);
}

export function setUserStatus(id: number, status: UserStatus): Promise<ManagedUser> {
  return request<ManagedUser>('POST', `/users/${encodeURIComponent(String(id))}/status`, { status });
}

export function updateUserRole(id: number, role: SystemRole): Promise<ManagedUser> {
  return request<ManagedUser>('PATCH', `/users/${encodeURIComponent(String(id))}/role`, { role });
}

// 重置密码会让后端吊销该用户全部 Token；调用方需提示重新登录。
export function resetUserPassword(id: number, password: string): Promise<void> {
  return request<void>('POST', `/users/${encodeURIComponent(String(id))}/password`, { password });
}

export function deleteUser(id: number): Promise<void> {
  return request<void>('DELETE', `/users/${encodeURIComponent(String(id))}`);
}
