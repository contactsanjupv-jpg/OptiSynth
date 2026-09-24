from backend.app.repositories import projects_repo, experiments_repo
from backend.app.schemas.project_schemas import CreateProjectRequest, UpdateStatusRequest, UpdateProjectRequest
from backend.app.schemas.errors import ValidationError
from backend.app.services.project_rules import check_structural_edit_allowed


def create_project(organization_id: int, user_id: int, body: CreateProjectRequest) -> dict:
    project_id = projects_repo.create_project(
        organization_id=organization_id,
        created_by_user_id=user_id,
        name=body.name,
        objective=body.objective,
        target_metric=body.target_metric,
        direction=body.direction,
        feature_columns=body.feature_columns,
        constraints=[c.model_dump() for c in body.constraints],
        target_value=body.target_value,
    )
    return projects_repo.get_project(organization_id, project_id)


def list_projects(organization_id: int) -> list:
    return projects_repo.list_projects(organization_id)


def get_project_or_404(organization_id: int, project_id: int) -> dict:
    project = projects_repo.get_project(organization_id, project_id)
    if not project:
        raise LookupError("Project not found.")
    return project


def update_status(organization_id: int, project_id: int, body: UpdateStatusRequest) -> dict:
    get_project_or_404(organization_id, project_id)
    projects_repo.update_project_status(organization_id, project_id, body.status)
    return projects_repo.get_project(organization_id, project_id)


def update_project(organization_id: int, project_id: int, body: UpdateProjectRequest) -> dict:
    get_project_or_404(organization_id, project_id)

    fields = body.model_dump(exclude_unset=True)
    if not fields:
        raise ValidationError("No fields were provided to update.")

    experiment_count = len(experiments_repo.list_experiments(organization_id, project_id))
    check_structural_edit_allowed(fields, experiment_count)

    if "constraints" in fields:
        fields["constraints"] = [c.model_dump() for c in body.constraints]

    projects_repo.update_project(organization_id, project_id, fields)
    return projects_repo.get_project(organization_id, project_id)