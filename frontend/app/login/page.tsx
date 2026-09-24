"use client";

import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Atom } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { ErrorCallout } from "@/components/ui/Feedback";
import { login, signup, previewInvitation } from "@/lib/api";
import { ApiRequestError } from "@/lib/api/http";
import { setSessionCache } from "@/lib/auth/session";
import type { InvitationPreview } from "@/lib/api/team";
import "@/styles/components/auth.css";

type Mode = "signup" | "login";

function LoginPageInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const inviteToken = searchParams.get("invite");

  const [mode, setMode] = useState<Mode>("signup");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [organizationName, setOrganizationName] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const [invitePreview, setInvitePreview] = useState<InvitationPreview | null>(null);
  const [inviteError, setInviteError] = useState<string | null>(null);
  const [checkingInvite, setCheckingInvite] = useState(!!inviteToken);

  useEffect(() => {
    if (!inviteToken) return;
    previewInvitation(inviteToken)
      .then((preview) => {
        setInvitePreview(preview);
        setEmail(preview.email);
        setMode("signup");
      })
      .catch(() => setInviteError("This invite link is no longer valid -- it may have expired or already been used."))
      .finally(() => setCheckingInvite(false));
  }, [inviteToken]);

  async function handleSignup(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const res = await signup({
        email, password,
        display_name: displayName || undefined,
        ...(inviteToken ? { invite_token: inviteToken } : { organization_name: organizationName }),
      });
      setSessionCache({
        userEmail: res.user.email,
        displayName: res.user.display_name,
        organizationId: res.organization.id,
      });
      router.push("/dashboard");
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.message : "Something went wrong.");
    } finally {
      setLoading(false);
    }
  }

  async function handleLogin(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const res = await login({ email, password });
      setSessionCache({
        userEmail: res.user.email,
        displayName: res.user.display_name,
        organizationId: res.memberships[0]?.organization_id ?? 0,
      });
      router.push("/dashboard");
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.message : "Something went wrong.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="auth-page">
      <div className="auth-card">
        <Atom size={22} className="auth-card__brand-icon" />
        <h1 className="auth-card__title">OptiSynth</h1>

        {inviteToken && checkingInvite && (
          <p className="auth-card__subtitle">Checking your invite…</p>
        )}

        {inviteToken && inviteError && (
          <ErrorCallout message={inviteError} />
        )}

        {invitePreview && (
          <p className="auth-card__subtitle">
            You&apos;ve been invited to join <strong>{invitePreview.organization_name}</strong> as {invitePreview.role}.
            Set a password to accept.
          </p>
        )}

        {!inviteToken && (
          <p className="auth-card__subtitle">Find better candidates with fewer experiments.</p>
        )}

        {!invitePreview && !inviteToken && (
          <div className="auth-card__mode-switch">
            <button
              className={`auth-card__mode-btn${mode === "signup" ? " auth-card__mode-btn--active" : ""}`}
              onClick={() => setMode("signup")}
              type="button"
            >
              Create account
            </button>
            <button
              className={`auth-card__mode-btn${mode === "login" ? " auth-card__mode-btn--active" : ""}`}
              onClick={() => setMode("login")}
              type="button"
            >
              Sign in
            </button>
          </div>
        )}

        {error && <ErrorCallout message={error} />}

        {mode === "signup" ? (
          <form onSubmit={handleSignup} className="auth-card__form">
            {!invitePreview && (
              <>
                <label className="auth-card__label" htmlFor="organizationName">Company / team name</label>
                <input
                  id="organizationName" className="auth-card__input" value={organizationName}
                  onChange={(e) => setOrganizationName(e.target.value)} placeholder="Acme Coatings" required
                />
              </>
            )}
            <label className="auth-card__label" htmlFor="displayName">Your name</label>
            <input
              id="displayName" className="auth-card__input" value={displayName}
              onChange={(e) => setDisplayName(e.target.value)} placeholder="Dr. Sarah Chen"
            />
            <label className="auth-card__label" htmlFor="email">Work email</label>
            <input
              id="email" type="email" className="auth-card__input" value={email}
              onChange={(e) => setEmail(e.target.value)} placeholder="sarah@acme.com" required
              disabled={!!invitePreview}
            />
            <label className="auth-card__label" htmlFor="password">Password</label>
            <input
              id="password" type="password" className="auth-card__input" value={password}
              onChange={(e) => setPassword(e.target.value)} placeholder="At least 10 characters" required
              minLength={10}
            />
            <Button type="submit" variant="primary" loading={loading}>
              {invitePreview ? "Accept invite & sign up" : "Create account"}
            </Button>
          </form>
        ) : (
          <form onSubmit={handleLogin} className="auth-card__form">
            <label className="auth-card__label" htmlFor="loginEmail">Email</label>
            <input
              id="loginEmail" type="email" className="auth-card__input" value={email}
              onChange={(e) => setEmail(e.target.value)} required
            />
            <label className="auth-card__label" htmlFor="loginPassword">Password</label>
            <input
              id="loginPassword" type="password" className="auth-card__input" value={password}
              onChange={(e) => setPassword(e.target.value)} required
            />
            <Button type="submit" variant="primary" loading={loading}>Sign in</Button>
          </form>
        )}
      </div>
    </div>
  );
}

export default function LoginPage() {
  return (
    <Suspense fallback={null}>
      <LoginPageInner />
    </Suspense>
  );
}