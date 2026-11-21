import { apiRequest } from "@/lib/api/http";

export function getAuthConfig() {
  return apiRequest<{ public_signup: boolean }>("/api/auth/config");
}

export interface OrganizationProfile {
  id: number; name: string; industry: string | null; website: string | null;
  created_at: string; updated_at: string | null; industries: string[];
}
export const getOrganization = () => apiRequest<OrganizationProfile>("/api/organization");
export const updateOrganization = (input: { name: string; industry: string; website: string }) =>
  apiRequest<OrganizationProfile>("/api/organization", { method: "PATCH", json: input });

export const updateProfile = (displayName: string) =>
  apiRequest<{ user: { display_name: string | null } }>("/api/auth/profile", { method: "PATCH", json: { display_name: displayName } });
export const changePassword = (currentPassword: string, newPassword: string) =>
  apiRequest<{ ok: boolean; other_sessions_ended: number }>("/api/auth/password", {
    method: "POST", json: { current_password: currentPassword, new_password: newPassword },
  });

export interface SessionRow { id: number; device: string; address: string | null; created_at: string; expires_at: string; current: boolean }
export const listSessions = () => apiRequest<SessionRow[]>("/api/auth/sessions");
export const revokeSession = (id: number) => apiRequest<{ ok: boolean }>(`/api/auth/sessions/${id}`, { method: "DELETE" });
export const revokeOtherSessions = () => apiRequest<{ ended: number }>("/api/auth/sessions/revoke-others", { method: "POST" });
export const signOutEverywhere = () => apiRequest<{ ended: number }>("/api/auth/logout-all", { method: "POST" });

export interface SecurityEvent { id: number; label: string; detail: string | null; created_at: string }
export const listSecurityEvents = () => apiRequest<SecurityEvent[]>("/api/security/events");

export interface AuditEvent {
  id: number; action: string; label: string; actor: string | null; actor_id: number | null;
  program_id: number | null; program_name: string | null; detail: string | null; created_at: string;
}
export interface AuditPage {
  total: number; limit: number; offset: number; events: AuditEvent[];
  actions: { action: string; label: string }[]; actors: { id: number; name: string | null }[];
}
export interface AuditFilter { user_id?: string; action?: string; q?: string; date_from?: string; date_to?: string; offset?: number }
export function listAuditEvents(f: AuditFilter) {
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(f)) if (v !== undefined && v !== "") params.set(k, String(v));
  params.set("limit", "25");
  return apiRequest<AuditPage>(`/api/audit/events?${params.toString()}`);
}

export interface UploadInventory {
  files: { id: number; program_id: number; program_name: string; filename: string; rows: number | null; size_bytes: number | null; uploaded_by: string | null; uploaded_at: string }[];
  total_bytes: number;
}
export const getUploadInventory = () => apiRequest<UploadInventory>("/api/data/uploads");
export const exportData = (password: string) =>
  apiRequest<{ blob: Blob; filename: string | null }>("/api/data/export", { method: "POST", json: { password }, expectFile: true });

export interface NotificationItem { id: number; kind: string; title: string; body: string | null; link: string | null; created_at: string; read_at: string | null }
export const getNotifications = (unreadOnly = false) =>
  apiRequest<{ unread: number; items: NotificationItem[] }>(`/api/notifications${unreadOnly ? "?unread_only=true" : ""}`);
export const markNotificationRead = (id: number) => apiRequest<{ ok: boolean }>(`/api/notifications/${id}/read`, { method: "POST" });
export const markAllNotificationsRead = () => apiRequest<{ marked: number }>("/api/notifications/read-all", { method: "POST" });
