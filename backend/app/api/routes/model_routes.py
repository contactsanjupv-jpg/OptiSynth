from typing import Optional

from fastapi import APIRouter, Depends, Query

from backend.app.api.dependencies import get_current_actor, AuthContext
from backend.app.schemas.model_schemas import RunValidationRequest
from backend.app.services import audit_service, model_validation_service as svc

router = APIRouter(prefix="/api", tags=["models"])


def _log(actor: AuthContext, action: str, detail: str) -> None:
    audit_service.log(actor.organization_id, actor.user["id"], f"model.{action}", detail=detail)


@router.get("/models")
def model_library(program_id: Optional[int] = None, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0),
                  actor: AuthContext = Depends(get_current_actor)):
    return svc.library(actor.organization_id, program_id, limit, offset)


@router.get("/models/protocols")
def validation_protocols(actor: AuthContext = Depends(get_current_actor)):
    return {"protocols": svc.protocols()}


@router.get("/models/compare")
def compare_models(a: int, b: int, actor: AuthContext = Depends(get_current_actor)):
    return svc.compare(actor.organization_id, a, b)


@router.get("/models/{version_id}")
def model_detail(version_id: int, actor: AuthContext = Depends(get_current_actor)):
    return svc.detail(actor.organization_id, actor.role, actor.user["id"], version_id)


@router.get("/models/{version_id}/uncertainty")
def model_uncertainty(version_id: int, actor: AuthContext = Depends(get_current_actor)):
    return svc.uncertainty_view(actor.organization_id, version_id)


@router.get("/models/{version_id}/applicability")
def model_applicability(version_id: int, actor: AuthContext = Depends(get_current_actor)):
    return svc.applicability_view(actor.organization_id, version_id)


@router.get("/models/{version_id}/validations/{validation_id}")
def validation_record(version_id: int, validation_id: int, actor: AuthContext = Depends(get_current_actor)):
    return svc.validation_detail(actor.organization_id, version_id, validation_id)


@router.post("/models/{version_id}/validations")
def run_validation(version_id: int, body: RunValidationRequest, actor: AuthContext = Depends(get_current_actor)):
    result = svc.run_validation(actor.organization_id, actor.role, actor.user["id"], version_id, body.protocol)
    _log(actor, "validation_run", f"model_version_id={version_id} protocol={body.protocol} status={result['validation']['status']} "
                                  f"created={result['created']}")
    return result


@router.post("/change-cases/{change_case_id}/models/register")
def register_model(change_case_id: int, actor: AuthContext = Depends(get_current_actor)):
    result = svc.register_current(actor.organization_id, actor.role, actor.user["id"], change_case_id)
    _log(actor, "register", f"change_case_id={change_case_id} model_version_id={result['version']['id']} created={result['created']}")
    return result
