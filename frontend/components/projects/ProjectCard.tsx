import Link from "next/link";
import { Card } from "@/components/ui/Card";
import { StatusBadge } from "@/components/ui/Badge";
import { formatNumber, formatRelativeTime } from "@/lib/utils/format";
import type { Project } from "@/types";
import "@/styles/components/project-card.css";

export function ProjectCard({ project }: { project: Project }) {
  return (
    <Link href={`/projects/${project.id}`} className="project-card-link">
      <Card padding="md" className="project-card">
        <div className="project-card__top">
          <h3 className="project-card__name">{project.name}</h3>
          <StatusBadge status={project.status} />
        </div>
        <p className="project-card__objective">{project.objective || "No objective set"}</p>
        <div className="project-card__meta">
          <span>
            Target: {project.target_metric} {project.direction === "maximize" ? "\u2265" : "\u2264"}{" "}
            {project.target_value !== null ? formatNumber(project.target_value) : "not set"}
          </span>
        </div>
        <div className="project-card__footer">
          <span>{project.experiment_count ?? 0} experiments</span>
          <span>Updated {formatRelativeTime(project.updated_at)}</span>
        </div>
      </Card>
    </Link>
  );
}
