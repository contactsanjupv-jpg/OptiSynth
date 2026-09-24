/**
 * Client-side auth STATE tracking only. The actual session credential is a
 * signed, httpOnly cookie set by the backend (see
 * backend/app/security/sessions.py) -- JavaScript cannot read it, and this
 * file never tries to. All this module does is remember "am I signed in"
 * for UI purposes (e.g. redirecting from /login once signed in) and cache
 * the current user's display info so the sidebar doesn't need to refetch
 * /api/auth/me on every render.
 *
 * This is NOT where auth is enforced -- every request still relies on the
 * browser automatically attaching the httpOnly cookie, and the backend
 * re-verifies it server-side on every request (see
 * backend/app/api/dependencies.py#get_current_actor). Clearing this cache
 * does not "log out" by itself -- see lib/api/auth.ts#logout, which calls
 * POST /api/auth/logout to actually invalidate the session.
 */

const CACHE_KEY = "rdopt_ui_session_cache";

export interface SessionCache {
  userEmail: string;
  displayName: string | null;
  organizationId: number;
}

export function getSessionCache(): SessionCache | null {
  if (typeof window === "undefined") return null;
  const raw = window.sessionStorage.getItem(CACHE_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as SessionCache;
  } catch {
    return null;
  }
}

export function setSessionCache(cache: SessionCache): void {
  if (typeof window === "undefined") return;
  window.sessionStorage.setItem(CACHE_KEY, JSON.stringify(cache));
}

export function clearSessionCache(): void {
  if (typeof window === "undefined") return;
  window.sessionStorage.removeItem(CACHE_KEY);
}
