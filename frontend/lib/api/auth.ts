import { apiRequest } from "@/lib/api/http";
import type { MembershipRole, UserPublic } from "@/types";

export interface SignupInput {
  email: string;
  password: string;
  // Exactly one of these two: organization_name for creating a brand new
  // org, invite_token for joining one via a team invite.
  organization_name?: string;
  invite_token?: string;
  display_name?: string;
}

export interface SignupResponse {
  user: UserPublic;
  organization: { id: number; name: string; created_at: string };
}

/** Maps to POST /api/auth/signup. On success the backend also sets the
 * session cookie (Set-Cookie, httpOnly) -- there's nothing further for the
 * frontend to store. */
export function signup(input: SignupInput) {
  return apiRequest<SignupResponse>("/api/auth/signup", { method: "POST", json: input });
}

export interface LoginInput {
  email: string;
  password: string;
}

export interface LoginResponse {
  user: UserPublic;
  memberships: { organization_id: number; organization_name: string; role: MembershipRole }[];
}

export function login(input: LoginInput) {
  return apiRequest<LoginResponse>("/api/auth/login", { method: "POST", json: input });
}

export function logout() {
  return apiRequest<{ ok: boolean }>("/api/auth/logout", { method: "POST" });
}

export interface MeResponse {
  user: UserPublic;
  organization_id: number;
  role: MembershipRole;
}

export function getMe() {
  return apiRequest<MeResponse>("/api/auth/me");
}
