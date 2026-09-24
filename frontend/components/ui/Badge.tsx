import "@/styles/components/badge.css";
import type { ProjectStatus } from "@/types";

const STATUS_LABEL: Record<ProjectStatus, string> = {
  draft: "Draft",
  running: "Running",
  completed: "Completed",
};

export function StatusBadge({ status }: { status: ProjectStatus }) {
  return <span className={`badge badge--${status}`}>{STATUS_LABEL[status]}</span>;
}

/** Generic badge for anything that isn't a ProjectStatus (e.g. experiment source). */
export function Badge({
  tone = "neutral",
  children,
}: {
  tone?: "neutral" | "success" | "warning" | "danger" | "accent";
  children: React.ReactNode;
}) {
  return <span className={`badge badge--tone-${tone}`}>{children}</span>;
}
