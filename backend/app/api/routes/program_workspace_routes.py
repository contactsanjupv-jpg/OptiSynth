from fastapi import APIRouter, Depends

from backend.app.api.dependencies import get_current_actor, AuthContext
from backend.app.schemas.workspace_schemas import (
    CommentRequest, DetailsRequest, MilestoneRequest, MilestoneUpdateRequest, OwnerRequest, RequirementRequest,
    RequirementResultRequest, RequirementUpdateRequest, ReviewerRequest, TaskRequest, TaskUpdateRequest,
)
from backend.app.services import audit_service, program_workspace_service as svc

router = APIRouter(prefix="/api/change-cases/{change_case_id}", tags=["program-workspace"])


def _log(actor: AuthContext, action: str, case_id: int, extra: str = "") -> None:
    audit_service.log(actor.organization_id, actor.user["id"], f"change_case.{action}",
                      detail=f"change_case_id={case_id}" + (f" {extra}" if extra else ""))


@router.get("/workspace")
def get_workspace(change_case_id: int, actor: AuthContext = Depends(get_current_actor)):
    return svc.workspace(actor.organization_id, actor.role, actor.user["id"], change_case_id)


@router.patch("/details")
def update_details(change_case_id: int, body: DetailsRequest, actor: AuthContext = Depends(get_current_actor)):
    fields = svc.update_details(actor.organization_id, actor.role, actor.user["id"], change_case_id, body.model_dump(exclude_unset=True))
    _log(actor, "details_update", change_case_id, f"fields={','.join(fields)}")
    return svc.workspace(actor.organization_id, actor.role, actor.user["id"], change_case_id)


@router.put("/owner")
def set_owner(change_case_id: int, body: OwnerRequest, actor: AuthContext = Depends(get_current_actor)):
    svc.set_owner(actor.organization_id, actor.role, actor.user["id"], change_case_id, body.user_id)
    _log(actor, "owner_set", change_case_id, f"owner_user_id={body.user_id}")
    return svc.workspace(actor.organization_id, actor.role, actor.user["id"], change_case_id)


@router.post("/reviewers", status_code=201)
def add_reviewer(change_case_id: int, body: ReviewerRequest, actor: AuthContext = Depends(get_current_actor)):
    svc.add_reviewer(actor.organization_id, actor.role, actor.user["id"], change_case_id, body.user_id)
    _log(actor, "reviewer_add", change_case_id, f"reviewer_user_id={body.user_id}")
    return svc.workspace(actor.organization_id, actor.role, actor.user["id"], change_case_id)


@router.delete("/reviewers/{user_id}")
def remove_reviewer(change_case_id: int, user_id: int, actor: AuthContext = Depends(get_current_actor)):
    svc.remove_reviewer(actor.organization_id, actor.role, actor.user["id"], change_case_id, user_id)
    _log(actor, "reviewer_remove", change_case_id, f"reviewer_user_id={user_id}")
    return svc.workspace(actor.organization_id, actor.role, actor.user["id"], change_case_id)


@router.post("/requirements", status_code=201)
def add_requirement(change_case_id: int, body: RequirementRequest, actor: AuthContext = Depends(get_current_actor)):
    new_id = svc.add_requirement(actor.organization_id, actor.role, actor.user["id"], change_case_id, body.model_dump())
    _log(actor, "requirement_add", change_case_id, f"requirement_id={new_id}")
    return svc.workspace(actor.organization_id, actor.role, actor.user["id"], change_case_id)


@router.patch("/requirements/{requirement_id}")
def update_requirement(change_case_id: int, requirement_id: int, body: RequirementUpdateRequest,
                       actor: AuthContext = Depends(get_current_actor)):
    fields = svc.update_requirement(actor.organization_id, actor.role, actor.user["id"], change_case_id, requirement_id,
                                    body.model_dump(exclude_unset=True))
    _log(actor, "requirement_update", change_case_id, f"requirement_id={requirement_id} fields={','.join(fields)}")
    return svc.workspace(actor.organization_id, actor.role, actor.user["id"], change_case_id)


@router.post("/requirements/{requirement_id}/archive")
def archive_requirement(change_case_id: int, requirement_id: int, actor: AuthContext = Depends(get_current_actor)):
    svc.archive_requirement(actor.organization_id, actor.role, actor.user["id"], change_case_id, requirement_id, True)
    _log(actor, "requirement_archive", change_case_id, f"requirement_id={requirement_id}")
    return svc.workspace(actor.organization_id, actor.role, actor.user["id"], change_case_id)


@router.post("/requirements/{requirement_id}/restore")
def restore_requirement(change_case_id: int, requirement_id: int, actor: AuthContext = Depends(get_current_actor)):
    svc.archive_requirement(actor.organization_id, actor.role, actor.user["id"], change_case_id, requirement_id, False)
    _log(actor, "requirement_restore", change_case_id, f"requirement_id={requirement_id}")
    return svc.workspace(actor.organization_id, actor.role, actor.user["id"], change_case_id)


@router.post("/requirements/{requirement_id}/results", status_code=201)
def record_result(change_case_id: int, requirement_id: int, body: RequirementResultRequest,
                  actor: AuthContext = Depends(get_current_actor)):
    svc.record_result(actor.organization_id, actor.user["id"], change_case_id, requirement_id, body.model_dump())
    _log(actor, "requirement_result", change_case_id, f"requirement_id={requirement_id} met={body.met}")
    return svc.workspace(actor.organization_id, actor.role, actor.user["id"], change_case_id)


@router.post("/milestones", status_code=201)
def add_milestone(change_case_id: int, body: MilestoneRequest, actor: AuthContext = Depends(get_current_actor)):
    new_id = svc.add_milestone(actor.organization_id, actor.user["id"], change_case_id, body.title, body.due_date)
    _log(actor, "milestone_add", change_case_id, f"milestone_id={new_id}")
    return svc.workspace(actor.organization_id, actor.role, actor.user["id"], change_case_id)


@router.patch("/milestones/{milestone_id}")
def update_milestone(change_case_id: int, milestone_id: int, body: MilestoneUpdateRequest,
                     actor: AuthContext = Depends(get_current_actor)):
    fields = svc.update_milestone(actor.organization_id, actor.user["id"], change_case_id, milestone_id, body.model_dump(exclude_unset=True))
    _log(actor, "milestone_update", change_case_id, f"milestone_id={milestone_id} fields={','.join(fields)}")
    return svc.workspace(actor.organization_id, actor.role, actor.user["id"], change_case_id)


@router.delete("/milestones/{milestone_id}")
def delete_milestone(change_case_id: int, milestone_id: int, actor: AuthContext = Depends(get_current_actor)):
    svc.delete_milestone(actor.organization_id, change_case_id, milestone_id)
    _log(actor, "milestone_delete", change_case_id, f"milestone_id={milestone_id}")
    return svc.workspace(actor.organization_id, actor.role, actor.user["id"], change_case_id)


@router.post("/tasks", status_code=201)
def add_task(change_case_id: int, body: TaskRequest, actor: AuthContext = Depends(get_current_actor)):
    new_id = svc.add_task(actor.organization_id, actor.user["id"], change_case_id, body.title, body.assignee_user_id, body.due_date)
    _log(actor, "task_add", change_case_id, f"task_id={new_id}")
    return svc.workspace(actor.organization_id, actor.role, actor.user["id"], change_case_id)


@router.patch("/tasks/{task_id}")
def update_task(change_case_id: int, task_id: int, body: TaskUpdateRequest, actor: AuthContext = Depends(get_current_actor)):
    svc.set_task_done(actor.organization_id, actor.user["id"], change_case_id, task_id, body.completed)
    _log(actor, "task_update", change_case_id, f"task_id={task_id} completed={body.completed}")
    return svc.workspace(actor.organization_id, actor.role, actor.user["id"], change_case_id)


@router.delete("/tasks/{task_id}")
def delete_task(change_case_id: int, task_id: int, actor: AuthContext = Depends(get_current_actor)):
    svc.delete_task(actor.organization_id, change_case_id, task_id)
    _log(actor, "task_delete", change_case_id, f"task_id={task_id}")
    return svc.workspace(actor.organization_id, actor.role, actor.user["id"], change_case_id)


@router.get("/comments")
def list_comments(change_case_id: int, actor: AuthContext = Depends(get_current_actor)):
    return svc.list_comments(actor.organization_id, change_case_id)


@router.post("/comments", status_code=201)
def add_comment(change_case_id: int, body: CommentRequest, actor: AuthContext = Depends(get_current_actor)):
    new_id, kind = svc.add_comment(actor.organization_id, actor.role, actor.user["id"], change_case_id, body.kind, body.body)
    _log(actor, "decision_record" if kind == "decision" else "comment_add", change_case_id, f"comment_id={new_id}")
    return svc.list_comments(actor.organization_id, change_case_id)


@router.get("/timeline")
def get_timeline(change_case_id: int, actor: AuthContext = Depends(get_current_actor)):
    return svc.timeline(actor.organization_id, change_case_id)
