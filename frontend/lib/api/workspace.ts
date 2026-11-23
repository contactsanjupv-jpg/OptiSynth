import { apiRequest } from "@/lib/api/http";

export type Criticality = "critical" | "important" | "informational";
export type Support = "met" | "not_met" | "no_result";
export interface Person { id: number | null; name: string | null }

export interface Requirement {
  id: number; property: string; threshold: number; direction: "maximize" | "minimize"; unit: string | null;
  test_method: string | null; criticality: Criticality; source: string | null; is_ranking_target: boolean;
  support: Support; result_count: number;
  latest_result: { id: number; text: string; met: boolean; reference: string | null; created_at: string; recorded_by: string | null } | null;
}
export interface ArchivedRequirement {
  id: number; property: string; threshold: number; direction: "maximize" | "minimize"; unit: string | null;
  test_method: string | null; criticality: Criticality; source: string | null; archived_at: string;
}
export interface Milestone { id: number; title: string; due_date: string; completed_at: string | null; overdue: boolean }
export interface Task { id: number; title: string; due_date: string | null; completed_at: string | null; overdue: boolean; assignee: Person | null }

export interface Workspace {
  program: {
    id: number; name: string; status: string; stage: string; stage_label: string; trigger_type: string; restricted_substance: string | null;
    target_date: string | null; archived: boolean; material_replaced: string | null; reason_for_substitution: string | null;
    business_impact: string | null; original_formulation: string | null; manufacturing_constraints: string | null;
  };
  owner: Person | null; reviewers: Person[]; requirements: Requirement[]; archived_requirements: ArchivedRequirement[]; milestones: Milestone[]; tasks: Task[];
  completion: { missing_critical: string[]; message: string | null; has_critical: boolean };
  permissions: { can_manage: boolean; can_write: boolean; can_decide: boolean };
  people: { id: number; name: string; role: string }[];
  today: string;
}

export interface Comment { id: number; kind: "comment" | "decision"; body: string; created_at: string; author: string | null }
export interface TimelineEntry { id: number; label: string; actor: string | null; created_at: string }

const base = (id: number) => `/api/change-cases/${id}`;
export const getWorkspace = (id: number) => apiRequest<Workspace>(`${base(id)}/workspace`);
export const updateDetails = (id: number, fields: Record<string, string | null>) =>
  apiRequest<Workspace>(`${base(id)}/details`, { method: "PATCH", json: fields });
export const setOwner = (id: number, userId: number | null) => apiRequest<Workspace>(`${base(id)}/owner`, { method: "PUT", json: { user_id: userId } });
export const addReviewer = (id: number, userId: number) => apiRequest<Workspace>(`${base(id)}/reviewers`, { method: "POST", json: { user_id: userId } });
export const removeReviewer = (id: number, userId: number) => apiRequest<Workspace>(`${base(id)}/reviewers/${userId}`, { method: "DELETE" });

export interface RequirementInput {
  property: string; threshold: number; direction: string; unit: string | null; test_method: string | null; criticality: string; source: string | null;
}
export const addRequirement = (id: number, input: RequirementInput) => apiRequest<Workspace>(`${base(id)}/requirements`, { method: "POST", json: input });
export const updateRequirement = (id: number, reqId: number, fields: Partial<RequirementInput>) =>
  apiRequest<Workspace>(`${base(id)}/requirements/${reqId}`, { method: "PATCH", json: fields });
export const archiveRequirement = (id: number, reqId: number) => apiRequest<Workspace>(`${base(id)}/requirements/${reqId}/archive`, { method: "POST" });
export const restoreRequirement = (id: number, reqId: number) => apiRequest<Workspace>(`${base(id)}/requirements/${reqId}/restore`, { method: "POST" });
export const recordRequirementResult = (id: number, reqId: number, input: { result_text: string; met: boolean; test_reference: string | null }) =>
  apiRequest<Workspace>(`${base(id)}/requirements/${reqId}/results`, { method: "POST", json: input });

export const addMilestone = (id: number, title: string, dueDate: string) =>
  apiRequest<Workspace>(`${base(id)}/milestones`, { method: "POST", json: { title, due_date: dueDate } });
export const completeMilestone = (id: number, mid: number, completed: boolean) =>
  apiRequest<Workspace>(`${base(id)}/milestones/${mid}`, { method: "PATCH", json: { completed } });
export const deleteMilestone = (id: number, mid: number) => apiRequest<Workspace>(`${base(id)}/milestones/${mid}`, { method: "DELETE" });

export const addTask = (id: number, title: string, assignee: number | null, dueDate: string | null) =>
  apiRequest<Workspace>(`${base(id)}/tasks`, { method: "POST", json: { title, assignee_user_id: assignee, due_date: dueDate } });
export const completeTask = (id: number, tid: number, completed: boolean) =>
  apiRequest<Workspace>(`${base(id)}/tasks/${tid}`, { method: "PATCH", json: { completed } });
export const deleteTask = (id: number, tid: number) => apiRequest<Workspace>(`${base(id)}/tasks/${tid}`, { method: "DELETE" });

export const listComments = (id: number) => apiRequest<Comment[]>(`${base(id)}/comments`);
export const addComment = (id: number, kind: "comment" | "decision", body: string) =>
  apiRequest<Comment[]>(`${base(id)}/comments`, { method: "POST", json: { kind, body } });
export const getTimeline = (id: number) => apiRequest<TimelineEntry[]>(`${base(id)}/timeline`);
