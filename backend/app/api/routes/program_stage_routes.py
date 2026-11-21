from fastapi import APIRouter, Depends

from backend.app.api.dependencies import get_current_actor, AuthContext
from backend.app.schemas.program_schemas import ChangeStageRequest
from backend.app.services import audit_service, program_stage_service

router = APIRouter(prefix="/api/change-cases", tags=["program-stage"])


@router.get("/{change_case_id}/stage")
def get_stage(change_case_id: int, actor: AuthContext = Depends(get_current_actor)):
    return program_stage_service.stage_options(actor.organization_id, actor.role, change_case_id)


@router.get("/{change_case_id}/stage-history")
def get_stage_history(change_case_id: int, actor: AuthContext = Depends(get_current_actor)):
    return program_stage_service.stage_history(actor.organization_id, change_case_id)


@router.post("/{change_case_id}/stage")
def change_stage(change_case_id: int, body: ChangeStageRequest, actor: AuthContext = Depends(get_current_actor)):
    result = program_stage_service.change_stage(actor.organization_id, actor.role, actor.user["id"], change_case_id,
                                                body.to_stage, body.reason)
    audit_service.log(actor.organization_id, actor.user["id"], "change_case.stage_change",
                      detail=f"change_case_id={change_case_id} from={result['from_stage']} to={result['stage']}")
    return result
