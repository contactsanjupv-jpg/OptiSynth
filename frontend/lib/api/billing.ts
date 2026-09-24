import { apiRequest } from "@/lib/api/http";
import type { BillingSummary } from "@/types";

/** Reflects real stored subscription state (every org starts on a 'trial'
 * plan at signup). Does NOT integrate with Stripe yet -- see
 * backend/app/services/billing_service.py docstring. The UI must not imply
 * a live payment flow beyond what's actually wired up. */
export function getBillingSummary() {
  return apiRequest<BillingSummary>("/api/billing/summary");
}
