"use client";

import { useEffect, useState } from "react";
import { Copy, X } from "lucide-react";
import { AppShell } from "@/components/layout/AppShell";
import { Topbar } from "@/components/layout/Topbar";
import { Breadcrumbs } from "@/components/layout/Breadcrumbs";
import { PageHeader } from "@/components/layout/PageHeader";
import { Card, CardHeader, CardTitle } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";
import { ErrorCallout, Spinner } from "@/components/ui/Feedback";
import { useRequireAuth } from "@/lib/auth/useRequireAuth";
import { getMe, listMembers, listPendingInvitations, inviteMember, revokeInvitation, removeMember, updateMemberRole } from "@/lib/api";
import { ApiRequestError } from "@/lib/api/http";
import { formatRelativeTime } from "@/lib/utils/format";
import type { Member, Invitation } from "@/lib/api/team";
import type { MembershipRole } from "@/types";

export default function TeamPage() {
  const { checking } = useRequireAuth();
  const [members, setMembers] = useState<Member[] | null>(null);
  const [invitations, setInvitations] = useState<Invitation[] | null>(null);
  const [myRole, setMyRole] = useState<MembershipRole | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] = useState<MembershipRole>("member");
  const [inviting, setInviting] = useState(false);
  const [lastInviteUrl, setLastInviteUrl] = useState<string | null>(null);

  function load() {
    setError(null);
    getMe()
      .then((me) => {
        setMyRole(me.role);
        const canManageNow = me.role === "owner" || me.role === "admin";

        listMembers()
          .then(setMembers)
          .catch(() => setError("Could not load team members."));

        if (canManageNow) {
          listPendingInvitations()
            .then(setInvitations)
            .catch(() => setError("Could not load pending invitations."));
        }
      })
      .catch(() => setError("Could not load team data."));
  }

  useEffect(() => {
    if (!checking) load();
  }, [checking]);

  if (checking) return null;

  const canManage = myRole === "owner" || myRole === "admin";

  async function handleInvite(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setInviting(true);
    try {
      const result = await inviteMember(inviteEmail.trim(), inviteRole);
      setLastInviteUrl(result.invite_url);
      setInviteEmail("");
      load();
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.message : "Could not send invite.");
    } finally {
      setInviting(false);
    }
  }

  async function handleRevoke(id: number) {
    setError(null);
    try {
      await revokeInvitation(id);
      load();
    } catch {
      setError("Could not revoke invitation.");
    }
  }

  async function handleRemove(userId: number) {
    setError(null);
    try {
      await removeMember(userId);
      load();
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.message : "Could not remove member.");
    }
  }

  async function handleRoleChange(userId: number, role: MembershipRole) {
    setError(null);
    try {
      await updateMemberRole(userId, role);
      load();
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.message : "Could not update role.");
    }
  }

  function copyInviteUrl(url: string) {
    navigator.clipboard.writeText(url);
  }

  return (
    <AppShell>
      <Topbar><Breadcrumbs items={[{ label: "Team" }]} /></Topbar>
      <div className="app-shell__content">
        <PageHeader title="Team" subtitle="Manage who has access to your organization." />

        {error && <ErrorCallout message={error} />}

        {canManage && (
          <Card padding="lg" style={{ marginBottom: 20 }}>
            <CardHeader><CardTitle>Invite a team member</CardTitle></CardHeader>
            <form onSubmit={handleInvite} style={{ display: "flex", gap: 8, alignItems: "flex-end", flexWrap: "wrap" }}>
              <div style={{ flex: 1, minWidth: 220 }}>
                <label style={{ fontSize: "var(--text-xs)", color: "var(--color-text-secondary)" }}>Email</label>
                <input
                  type="email" required value={inviteEmail} onChange={(e) => setInviteEmail(e.target.value)}
                  placeholder="colleague@company.com"
                  style={{ display: "block", width: "100%", padding: "8px 10px", borderRadius: "var(--radius-sm)", border: "1px solid var(--color-border-strong)", fontSize: "var(--text-sm)", marginTop: 4 }}
                />
              </div>
              <div>
                <label style={{ fontSize: "var(--text-xs)", color: "var(--color-text-secondary)" }}>Role</label>
                <select
                  value={inviteRole} onChange={(e) => setInviteRole(e.target.value as MembershipRole)}
                  style={{ display: "block", padding: "8px 10px", borderRadius: "var(--radius-sm)", border: "1px solid var(--color-border-strong)", fontSize: "var(--text-sm)", marginTop: 4 }}
                >
                  <option value="member">Member</option>
                  <option value="admin">Admin</option>
                  <option value="owner">Owner</option>
                </select>
              </div>
              <Button type="submit" variant="primary" loading={inviting}>Send Invite</Button>
            </form>

            {lastInviteUrl && (
              <div style={{ marginTop: 16, padding: 12, background: "var(--color-accent-subtle)", borderRadius: "var(--radius-sm)" }}>
                <p style={{ fontSize: "var(--text-xs)", color: "var(--color-text-secondary)", marginBottom: 6 }}>
                  There&apos;s no email service connected yet -- copy this link and send it to them yourself:
                </p>
                <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                  <code style={{ fontSize: "var(--text-xs)", background: "var(--color-surface)", padding: "6px 8px", borderRadius: 4, flex: 1, overflow: "auto", whiteSpace: "nowrap" }}>
                    {lastInviteUrl}
                  </code>
                  <Button size="sm" variant="secondary" icon={<Copy size={13} />} onClick={() => copyInviteUrl(lastInviteUrl)}>
                    Copy
                  </Button>
                </div>
              </div>
            )}
          </Card>
        )}

        <Card padding="lg" style={{ marginBottom: 20 }}>
          <CardHeader><CardTitle>Members</CardTitle></CardHeader>
          {!members && <Spinner label="Loading members…" />}
          {members && members.map((m) => (
            <div key={m.user_id} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "10px 0", borderBottom: "1px solid var(--color-border)" }}>
              <div>
                <div style={{ fontSize: "var(--text-sm)", fontWeight: 500 }}>{m.display_name || m.email}</div>
                <div style={{ fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)" }}>
                  {m.email} · joined {formatRelativeTime(m.member_since)}
                </div>
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                {canManage ? (
                  <select
                    value={m.role}
                    onChange={(e) => handleRoleChange(m.user_id, e.target.value as MembershipRole)}
                    style={{ padding: "4px 8px", borderRadius: "var(--radius-sm)", border: "1px solid var(--color-border-strong)", fontSize: "var(--text-xs)" }}
                  >
                    <option value="member">Member</option>
                    <option value="admin">Admin</option>
                    <option value="owner">Owner</option>
                  </select>
                ) : (
                  <Badge tone="neutral">{m.role}</Badge>
                )}
                {canManage && m.role !== "owner" && (
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => {
                      if (window.confirm(`Remove ${m.display_name || m.email} from this organization?`)) {
                        handleRemove(m.user_id);
                      }
                    }}
                    aria-label="Remove member"
                  >
                    <X size={14} />
                  </Button>
                )}
              </div>
            </div>
          ))}
        </Card>

        {canManage && (
          <Card padding="lg">
            <CardHeader><CardTitle>Pending Invitations</CardTitle></CardHeader>
            {!invitations && <Spinner label="Loading…" />}
            {invitations && invitations.length === 0 && (
              <p style={{ fontSize: "var(--text-sm)", color: "var(--color-text-tertiary)" }}>No pending invitations.</p>
            )}
            {invitations && invitations.map((inv) => (
              <div key={inv.id} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "10px 0", borderBottom: "1px solid var(--color-border)" }}>
                <div>
                  <div style={{ fontSize: "var(--text-sm)", fontWeight: 500 }}>{inv.email}</div>
                  <div style={{ fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)" }}>
                    Invited as {inv.role} · sent {formatRelativeTime(inv.created_at)}
                  </div>
                </div>
                <div style={{ display: "flex", gap: 8 }}>
                  <Button size="sm" variant="secondary" icon={<Copy size={13} />} onClick={() => copyInviteUrl(inv.invite_url)}>
                    Copy link
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => handleRevoke(inv.id)}>Revoke</Button>
                </div>
              </div>
            ))}
          </Card>
        )}
      </div>
    </AppShell>
  );
}