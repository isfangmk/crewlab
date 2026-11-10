import { request, requestFile } from './client';

// 凭据 API：/v1/secrets/*、/v1/credential-types。
// 凭据按类型声明字段；custom 为自由键值。列表只回 field_keys，不回明文。

export interface SecretView {
  id: number;
  name: string;
  type: string;
  description: string;
  field_keys: string[];
  created_at: string;
  updated_at: string;
}

export interface CredField {
  key: string;
  label: string;
  secret: boolean;
}

export interface CredType {
  name: string;
  label: string;
  fields: CredField[];
  inject_env?: Record<string, string>;
  builtin: boolean;
}

// 健康自检：ok=false 表示当前 CREDENTIAL_ENCRYPTION_KEY 解不开这条密文。
export interface SecretHealthItem {
  id: number;
  name: string;
  type: string;
  ok: boolean;
  error: string | null;
}

// 影响面：正在引用这条凭据的 MCP Server。
export interface SecretUsageItem {
  id: number;
  name: string;
  enabled: boolean;
  status: string;
}

export interface SecretVerifyItem {
  id: number;
  name: string;
  ok: boolean;
  skipped: boolean;
  tool_count: number;
  error: string | null;
}

export function listSecrets() {
  return request<{ items: SecretView[] }>('GET', '/secrets');
}

export function listCredentialTypes() {
  return request<{ items: CredType[] }>('GET', '/credential-types');
}

export function getSecretsHealth() {
  return request<{ items: SecretHealthItem[]; total: number; broken: number }>(
    'GET',
    '/secrets/health',
  );
}

export function getSecretUsage(id: number) {
  return request<{ items: SecretUsageItem[]; total: number }>(
    'GET',
    `/secrets/${encodeURIComponent(String(id))}/usage`,
  );
}

export function createSecret(input: {
  name: string;
  type: string;
  description?: string;
  fields: Record<string, string>;
}) {
  return request<SecretView>('POST', '/secrets', input);
}

// 轮换：只提交要换的字段即可（旧密文可解密时未提交字段沿用旧值）。
export function rotateSecret(id: number, fields: Record<string, string>) {
  return request<SecretView>('POST', `/secrets/${encodeURIComponent(String(id))}/rotate`, {
    fields,
  });
}

export function verifySecretUsage(id: number) {
  return request<{ items: SecretVerifyItem[]; total: number; failed: number }>(
    'POST',
    `/secrets/${encodeURIComponent(String(id))}/usage/verify`,
  );
}

// 清单导出只含元数据，密文与明文都不出库。
export function exportSecrets(): Promise<{ blob: Blob; filename: string }> {
  return requestFile('/secrets/export');
}

export function deleteSecret(id: number) {
  return request<{ ok: boolean }>('DELETE', `/secrets/${encodeURIComponent(String(id))}`);
}
