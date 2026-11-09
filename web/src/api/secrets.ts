import { request } from './client';

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

export function listSecrets() {
  return request<{ items: SecretView[] }>('GET', '/secrets');
}

export function listCredentialTypes() {
  return request<{ items: CredType[] }>('GET', '/credential-types');
}

export function createSecret(input: { name: string; type: string; description?: string; fields: Record<string, string> }) {
  return request<SecretView>('POST', '/secrets', input);
}

export function deleteSecret(id: number) {
    return request<{ ok: boolean }>('DELETE', `/secrets/${id}`);
}
