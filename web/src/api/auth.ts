import { request } from './client';

export type LoginResponse = {
  access_token: string;
  token_type?: string;
  role?: string;
  email?: string;
};

export function login(email: string, password: string) {
  return request<LoginResponse>('POST', '/auth/login', { email, password }, { noAuth: true });
}
