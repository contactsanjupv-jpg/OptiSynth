import type { ApiError } from "@/types";

/**
 * The ONLY function in the frontend that calls fetch() directly against the
 * backend. Every typed endpoint function in lib/api/*.ts goes through this.
 *
 * Auth: `credentials: "include"` sends the httpOnly session cookie set by
 * the backend (see backend/app/security/sessions.py). There is no
 * client-readable secret to attach here -- that's the point: XSS-stolen
 * JavaScript cannot read this cookie, unlike an API key that was sitting
 * in localStorage.
 */

const BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:5050";

export class ApiRequestError extends Error {
  status: number;
  body: ApiError | null;
  constructor(status: number, body: ApiError | null) {
    super(body?.error ?? `Request failed with status ${status}`);
    this.status = status;
    this.body = body;
  }
}

interface RequestOptions {
  method?: "GET" | "POST" | "PATCH" | "DELETE";
  json?: unknown;
  formData?: FormData;
  /** For endpoints returning a binary file (e.g. report download). */
  expectBlob?: boolean;
}

export async function apiRequest<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const { method = "GET", json, formData, expectBlob = false } = opts;

  const headers: Record<string, string> = {};
  if (json !== undefined) {
    headers["Content-Type"] = "application/json";
  }

  const res = await fetch(`${BASE_URL}${path}`, {
    method,
    headers,
    credentials: "include",
    body: formData ?? (json !== undefined ? JSON.stringify(json) : undefined),
  });

  if (!res.ok) {
    let body: ApiError | null = null;
    try {
      body = await res.json();
    } catch {
      /* non-JSON error body, leave body null */
    }
    throw new ApiRequestError(res.status, body);
  }

  if (expectBlob) {
    return (await res.blob()) as unknown as T;
  }
  if (res.status === 204) {
    return undefined as unknown as T;
  }
  return (await res.json()) as T;
}
