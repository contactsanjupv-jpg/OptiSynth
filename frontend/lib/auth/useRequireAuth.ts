"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { getMe } from "@/lib/api/auth";
import { setSessionCache, getSessionCache } from "@/lib/auth/session";
import { ApiRequestError } from "@/lib/api/http";

/**
 * Every authenticated page calls this at the top of its component. Unlike
 * an API-key-in-localStorage scheme, there's no local secret to check --
 * this calls GET /api/auth/me, which only succeeds if the browser's
 * httpOnly session cookie is present AND still valid server-side (not
 * expired, user still active, membership still exists). A 401 here always
 * means "not authenticated right now," regardless of why.
 */
export function useRequireAuth() {
  const router = useRouter();
  const [checking, setChecking] = useState(true);
  const [organizationId, setOrganizationId] = useState<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    getMe()
      .then((res) => {
        if (cancelled) return;
        setSessionCache({
          userEmail: res.user.email,
          displayName: res.user.display_name,
          organizationId: res.organization_id,
        });
        setOrganizationId(res.organization_id);
        setChecking(false);
      })
      .catch((err) => {
        if (cancelled) return;
        if (err instanceof ApiRequestError && err.status === 401) {
          router.replace("/login");
        } else {
          // Network/server error -- still send to login rather than
          // silently rendering a broken authenticated page.
          router.replace("/login");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [router]);

  return { checking, organizationId, cached: getSessionCache() };
}
