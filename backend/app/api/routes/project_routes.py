"""
NOT RUNTIME-TESTED IN THE SANDBOX: fastapi is not installed here. Syntax-
validated via py_compile only -- see README "Sandbox limitations".
"""
from fastapi import APIRouter, Depends

from backend.app.schemas.project_schemas import (
    CreateProjectRequest, UpdateStatusRequest, UpdateProjectRequest, ProjectResponse,
)
from backend.app.services import project_service, audit_service
from backend.app.repositories import experiments_repo
from backend.app.api.dependencies import get_current_actor, AuthContext

router = APIRouter(prefix="/api/projects", tags=["projects"])


@router.post("", response_model=ProjectResponse, status_code=201)
def create_project(body: CreateProjectRequest, actor: AuthContext = Depends(get_current_actor)):
    project = project_service.create_project(actor.organization_id, actor.user["id"], body)
    audit_service.log(actor.organization_id, actor.user["id"], "project.create", project["id"])
    return project


@router.get("", response_model=list[ProjectResponse])
def list_projects(actor: AuthContext = Depends(get_current_actor)):
    projects = project_service.list_projects(actor.organization_id)
    for p in projects:
        p["experiment_count"] = len(experiments_repo.list_experiments(actor.organization_id, p["id"]))
    return projects


@router.get("/{project_id}", response_model=ProjectResponse)
def get_project(project_id: int, actor: AuthContext = Depends(get_current_actor)):
    project = project_service.get_project_or_404(actor.organization_id, project_id)
    project["experiment_count"] = len(experiments_repo.list_experiments(actor.organization_id, project_id))
    return project


@router.patch("/{project_id}/status", response_model=ProjectResponse)
def set_status(project_id: int, body: UpdateStatusRequest, actor: AuthContext = Depends(get_current_actor)):
    project = project_service.update_status(actor.organization_id, project_id, body)
    audit_service.log(actor.organization_id, actor.user["id"], "project.status_update", project_id,
                       detail=f"status={project['status']}")
    return project


@router.patch("/{project_id}", response_model=ProjectResponse)
def update_project(project_id: int, body: UpdateProjectRequest, actor: AuthContext = Depends(get_current_actor)):
    project = project_service.update_project(actor.organization_id, project_id, body)
    project["experiment_count"] = len(experiments_repo.list_experiments(actor.organization_id, project_id))
    updated_fields = ",".join(sorted(body.model_dump(exclude_unset=True).keys()))
    audit_service.log(actor.organization_id, actor.user["id"], "project.update", project_id,
                       detail=f"fields={updated_fields}")
    return project


@router.get("/{project_id}/experiments")
def list_experiments(project_id: int, actor: AuthContext = Depends(get_current_actor)):
    project_service.get_project_or_404(actor.organization_id, project_id)  # 404s if not owned
    return experiments_repo.list_experiments(actor.organization_id, project_id)