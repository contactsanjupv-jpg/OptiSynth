from backend.app.repositories import projects_repo, experiments_repo


def get_dashboard_summary(organization_id: int) -> dict:
    projects = projects_repo.list_projects(organization_id)
    total_experiments = 0
    active_count = 0
    project_summaries = []

    for p in projects:
        experiments = experiments_repo.list_experiments(organization_id, p["id"])
        total_experiments += len(experiments)
        if p["status"] == "running":
            active_count += 1

        best_value = None
        if experiments:
            values = [e["target_value"] for e in experiments]
            best_value = max(values) if p["direction"] == "maximize" else min(values)

        target_met = (
            best_value is not None and p["target_value"] is not None and (
                best_value >= p["target_value"] if p["direction"] == "maximize"
                else best_value <= p["target_value"]
            )
        )
        project_summaries.append({
            "id": p["id"], "name": p["name"], "status": p["status"],
            "target_metric": p["target_metric"], "target_value": p["target_value"],
            "direction": p["direction"], "experiment_count": len(experiments),
            "best_value": best_value, "target_met": target_met,
            "updated_at": p["updated_at"],
        })

    return {
        "active_projects": active_count,
        "total_projects": len(projects),
        "total_experiments": total_experiments,
        "targets_achieved": sum(1 for p in project_summaries if p["target_met"]),
        "projects": project_summaries,
    }
