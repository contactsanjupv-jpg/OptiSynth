import { apiRequest } from "@/lib/api/http";
import type { MembershipRole } from "@/types";

export interface Member {
  user_id: number;
  email: string;
  display_name: string | null;
  role: MembershipRole;
  is_active: boolean;
  member_since: string;
}

export function listMembers() {
  return apiRequest<Member[]>("/api/team/members");
}

export interface Invitation {
  id: number;
  email: string;
  role: MembershipRole;
  status: "pending" | "accepted" | "revoked";
  created_at: string;
  expires_at: string;
  invite_url: string;
}

export function inviteMember(email: string, role: MembershipRole = "member") {
  return apiRequest<Invitation>("/api/team/invitations", {
    method: "POST",
    json: { email, role },
  });
}

export function listPendingInvitations() {
  return apiRequest<Invitation[]>("/api/team/invitations");
}

export function revokeInvitation(invitationId: number) {
  return apiRequest<{ ok: boolean }>(`/api/team/invitations/${invitationId}`, { method: "DELETE" });
}

export function removeMember(userId: number) {
  return apiRequest<{ ok: boolean }>(`/api/team/members/${userId}`, { method: "DELETE" });
}

export function updateMemberRole(userId: number, role: MembershipRole) {
  return apiRequest<{ ok: boolean }>(`/api/team/members/${userId}/role`, {
    method: "PATCH",
    json: { role },
  });
}

export interface InvitationPreview {
  organization_name: string;
  email: string;
  role: MembershipRole;
}

export function previewInvitation(token: string) {
  return apiRequest<InvitationPreview>(`/api/invitations/${token}`);
}