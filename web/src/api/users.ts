// users.ts — 当前登录用户（/v1/me）的类型封装。
//
// 后端只提供单管理员的 /api/v1/me；没有用户列表 / 建号 / 改角色 / 改密码
// 等管理接口，因此这里不保留那些不存在的端点。
import { request } from './client';

// SystemRole 是平台权限档位。
//   admin = 全部系统功能 + 平台配置
//   user = 使用全部业务功能（会话、可写工具）
//   viewer = 只读 + 受限会话（LLM 只拿到只读工具集）
export type SystemRole = 'admin' | 'user' | 'viewer';
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
