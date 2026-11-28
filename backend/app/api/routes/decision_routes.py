from fastapi import APIRouter, Depends

from backend.app.api.dependencies import get_current_actor, AuthContext
from backend.app.schemas.decision_schemas import (AttributeCreate, AttributeUpdate, ConstraintCreate, ConstraintUpdate, ObjectiveCreate,
                                                   ObjectiveUpdate, ValueSet)
from backend.app.services import audit_service, decision_service as svc

router = APIRouter(prefix="/api/change-cases/{change_case_id}/decision", tags=["decision"])


def _log(actor: AuthContext, action: str, detail: str) -> None:
    audit_service.log(actor.organization_id, actor.user["id"], f"decision.{action}", detail=detail)


@router.get("")
def decision_workspace(change_case_id: int, actor: AuthContext = Depends(get_current_actor)):
    return svc.workspace(actor.organization_id, actor.role, actor.user["id"], change_case_id)


@router.post("/attributes", status_code=201)
def create_attribute(change_case_id: int, body: AttributeCreate, actor: AuthContext = Depends(get_current_actor)):
    out = svc.create_attribute(actor.organization_id, actor.role, actor.user["id"], change_case_id, body)
    _log(actor, "attribute_create", f"change_case_id={change_case_id} attribute_id={out['id']} key={out['key']}")
    return out


@router.patch("/attributes/{attribute_id}")
def update_attribute(change_case_id: int, attribute_id: int, body: AttributeUpdate, actor: AuthContext = Depends(get_current_actor)):
    out = svc.update_attribute(actor.organization_id, actor.role, actor.user["id"], change_case_id, attribute_id, body)
    _log(actor, "attribute_update", f"change_case_id={change_case_id} attribute_id={attribute_id} status={out['status']}")
    return out


@router.put("/candidates/{candidate_id}/attributes/{attribute_id}")
def set_value(change_case_id: int, candidate_id: int, attribute_id: int, body: ValueSet, actor: AuthContext = Depends(get_current_actor)):
    out = svc.set_value(actor.organization_id, actor.role, actor.user["id"], change_case_id, candidate_id, attribute_id, body)
    _log(actor, "value_set", f"change_case_id={change_case_id} candidate_id={candidate_id} attribute_id={attribute_id} "
                             f"old={out['previous']!r} new={out['record']['value']!r} source={out['record']['source_note']!r}")
    return out["record"]


@router.delete("/candidates/{candidate_id}/attributes/{attribute_id}")
def clear_value(change_case_id: int, candidate_id: int, attribute_id: int, actor: AuthContext = Depends(get_current_actor)):
    out = svc.clear_value(actor.organization_id, actor.role, actor.user["id"], change_case_id, candidate_id, attribute_id)
    _log(actor, "value_clear", f"change_case_id={change_case_id} candidate_id={candidate_id} attribute_id={attribute_id} old={out['previous']!r}")
    return {"cleared": True}


@router.post("/constraints", status_code=201)
def create_constraint(change_case_id: int, body: ConstraintCreate, actor: AuthContext = Depends(get_current_actor)):
    out = svc.create_constraint(actor.organization_id, actor.role, actor.user["id"], change_case_id, body)
    _log(actor, "constraint_create", f"change_case_id={change_case_id} constraint_id={out['id']} kind={out['kind']} ref={out['ref']}")
    return out


@router.patch("/constraints/{constraint_id}")
def update_constraint(change_case_id: int, constraint_id: int, body: ConstraintUpdate, actor: AuthContext = Depends(get_current_actor)):
    out = svc.update_constraint(actor.organization_id, actor.role, actor.user["id"], change_case_id, constraint_id, body)
    _log(actor, "constraint_update", f"change_case_id={change_case_id} constraint_id={constraint_id} status={out['status']}")
    return out


@router.post("/objectives", status_code=201)
def create_objective(change_case_id: int, body: ObjectiveCreate, actor: AuthContext = Depends(get_current_actor)):
    out = svc.create_objective(actor.organization_id, actor.role, actor.user["id"], change_case_id, body)
    _log(actor, "objective_create", f"change_case_id={change_case_id} objective_id={out['id']} ref={out['ref']} direction={out['direction']}")
    return out


@router.patch("/objectives/{objective_id}")
def update_objective(change_case_id: int, objective_id: int, body: ObjectiveUpdate, actor: AuthContext = Depends(get_current_actor)):
    out = svc.update_objective(actor.organization_id, actor.role, actor.user["id"], change_case_id, objective_id, body)
    _log(actor, "objective_update", f"change_case_id={change_case_id} objective_id={objective_id} status={out['status']}")
    return out


@router.post("/analyses", status_code=201)
def run_analysis(change_case_id: int, actor: AuthContext = Depends(get_current_actor)):
    out = svc.run_analysis(actor.organization_id, actor.role, actor.user["id"], change_case_id)
    _log(actor, "analysis_run", f"change_case_id={change_case_id} analysis_id={out['analysis']['id']} hash={out['analysis']['input_hash'][:12]}")
    return out


@router.get("/analyses/{analysis_id}")
def get_analysis(change_case_id: int, analysis_id: int, actor: AuthContext = Depends(get_current_actor)):
    return svc.get_analysis(actor.organization_id, change_case_id, analysis_id)
