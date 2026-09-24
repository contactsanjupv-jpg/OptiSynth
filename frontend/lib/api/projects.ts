import { apiRequest } from "@/lib/api/http";
import type { Constraint, Direction, Project, ProjectStatus } from "@/types";

export interface CreateProjectInput {
  name: string;
  objective?: string;
  target_metric: string;
  direction: Direction;
  feature_columns: string[];
  constraints?: Constraint[];
  target_value?: number;
}

export function createProject(input: CreateProjectInput) {
  return apiRequest<Project>("/api/projects", { method: "POST", json: input });
}

export function listProjects() {
  return apiRequest<Project[]>("/api/projects");
}

export function getProject(projectId: number) {
  return apiRequest<Project>(`/api/projects/${projectId}`);
}

export function setProjectStatus(projectId: number, status: ProjectStatus) {
  return apiRequest<Project>(`/api/projects/${projectId}/status`, {
    method: "PATCH",
    json: { status },
  });
}

export interface UpdateProjectInput {
  name?: string;
  objective?: string;
  target_metric?: string;
  direction?: Direction;
  feature_columns?: string[];
  constraints?: Constraint[];
  target_value?: number | null;
}

export function updateProject(projectId: number, input: UpdateProjectInput) {
  return apiRequest<Project>(`/api/projects/${projectId}`, {
    method: "PATCH",
    json: input,
  });
}

export interface DatasetUploadResult {
  dataset_id: number;
  rows_ingested: number;
  rows_skipped: number;
  errors: { row: number; error: string }[];
}

export function uploadDataset(projectId: number, file: File) {
  const formData = new FormData();
  formData.append("file", file);
  return apiRequest<DatasetUploadResult>(`/api/projects/${projectId}/dataset`, {
    method: "POST",
    formData,
  });
}