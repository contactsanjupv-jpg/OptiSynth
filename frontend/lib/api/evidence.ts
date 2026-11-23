import { apiRequest } from "@/lib/api/http";

export type ReviewStatus = "accepted" | "rejected" | "clarification_requested" | "conflict_flagged" | "excluded" | "superseded";

export interface EvidenceDocument {
  id: number; version_number: number | null; filename: string; uploaded_at: string; uploaded_by: string | null; row_count: number;
  program: { id: number; name: string }; status: ReviewStatus; status_label: string; review_reason: string | null;
  reviewed_by: string | null; reviewed_at: string | null; document_type: string | null; document_type_label: string | null;
  source_note: string | null; document_version: string | null; test_method: string | null; document_date: string | null;
  source_reliability: string | null; superseded_by_dataset_id: number | null; file_sha256: string | null; is_current: boolean;
  open_conflicts: number;
}

export interface QualityDimension { key: string; label: string; level: string; basis: string; facts: Record<string, unknown> }
export interface ReviewEntry {
  id: number; action: string; label: string; from_status: string | null; to_status: string; reason: string | null;
  related_dataset_id: number | null; actor: string | null; created_at: string;
}
export interface DocumentDetail extends EvidenceDocument {
  quality: QualityDimension[]; history: ReviewEntry[]; others: { id: number; label: string }[];
  flags: { code: string; severity: string; message: string }[]; writable: boolean; can_review: boolean;
}

export interface Conflict {
  id: number; kind: string; severity: "high" | "medium" | "low"; title: string; description: string; dataset_ids: number[];
  documents: (string | undefined)[]; details: Record<string, unknown>; status: string; resolution: string | null;
  resolution_note: string | null; preferred_dataset_id: number | null; detected_at: string; resolved_at: string | null; resolved_by: string | null;
}

export interface ProgramDocuments {
  documents: EvidenceDocument[]; conflicts: Conflict[]; held_notice: string | null;
  permissions: { can_review: boolean; writable: boolean };
  types: { key: string; label: string }[]; reliability: { key: string; label: string }[];
}

export interface TraceValue {
  original_value: number; original_unit: string | null; unit_source: string | null; canonical_unit: string | null;
  normalized_value: number; normalized_unit: string | null; transformation: string;
}
export interface TraceRow {
  id: number; number: number; inputs: Record<string, number>; target: number;
  source: { file: string | null; table: string | null; location: string | null; row: number | null } | null;
  values: Record<string, TraceValue> | null; trace_note: string | null;
  source_flags: { code: string; severity: string; message: string }[];
}
export interface TracePage {
  dataset: { id: number; version_number: number; filename: string; status: string; status_label: string; reviewed_by: string | null;
             reviewed_at: string | null; file_sha256: string | null; uploaded_by: string | null };
  total: number; offset: number; limit: number; rows: TraceRow[];
}

export interface LibraryPage {
  total: number; limit: number; offset: number; items: EvidenceDocument[];
  types: { key: string; label: string }[]; statuses: { key: string; label: string }[];
}
export interface LibraryFilter { q?: string; document_type?: string; status?: string; offset?: number }

const base = (id: number) => `/api/change-cases/${id}`;

export function listEvidenceLibrary(f: LibraryFilter) {
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(f)) if (v !== undefined && v !== "") params.set(k, String(v));
  params.set("limit", "25");
  return apiRequest<LibraryPage>(`/api/evidence?${params.toString()}`);
}
export const getProgramDocuments = (id: number) => apiRequest<ProgramDocuments>(`${base(id)}/evidence-library`);
export const getDocument = (id: number, did: number) => apiRequest<DocumentDetail>(`${base(id)}/evidence/${did}`);
export const updateDocumentMetadata = (id: number, did: number, fields: Record<string, string | null>) =>
  apiRequest<DocumentDetail>(`${base(id)}/evidence/${did}/metadata`, { method: "PATCH", json: fields });
export const reviewDocument = (id: number, did: number, action: string, reason: string, supersededBy: number | null) =>
  apiRequest<DocumentDetail>(`${base(id)}/evidence/${did}/review`, { method: "POST", json: { action, reason: reason.trim() || null, superseded_by: supersededBy } });
export const getDocumentRows = (id: number, did: number, offset: number) =>
  apiRequest<TracePage>(`${base(id)}/evidence/${did}/rows?offset=${offset}&limit=25`);
export const scanConflicts = (id: number) => apiRequest<ProgramDocuments>(`${base(id)}/conflicts/scan`, { method: "POST" });
export const resolveConflict = (id: number, cid: number, resolution: string, note: string, preferred: number | null) =>
  apiRequest<ProgramDocuments>(`${base(id)}/conflicts/${cid}/resolve`, { method: "POST", json: { resolution, note, preferred_dataset_id: preferred } });
