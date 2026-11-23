from typing import Optional

from fastapi import APIRouter, Depends, Query

from backend.app.api.dependencies import get_current_actor, AuthContext
from backend.app.schemas.evidence_schemas import MetadataRequest, ResolveRequest, ReviewRequest
from backend.app.services import audit_service, evidence_intel_service as svc

router = APIRouter(prefix="/api", tags=["evidence"])


def _log(actor: AuthContext, action: str, case_id: int, extra: str = "") -> None:
    audit_service.log(actor.organization_id, actor.user["id"], f"change_case.{action}",
                      detail=f"change_case_id={case_id}" + (f" {extra}" if extra else ""))


@router.get("/evidence")
def evidence_library(q: Optional[str] = Query(None, max_length=200), document_type: Optional[str] = None, status: Optional[str] = None,
                     program_id: Optional[int] = None, limit: int = 50, offset: int = 0, actor: AuthContext = Depends(get_current_actor)):
    return svc.library(actor.organization_id, q, document_type, status, program_id, limit, offset)


@router.get("/change-cases/{change_case_id}/evidence-library")
def program_documents(change_case_id: int, actor: AuthContext = Depends(get_current_actor)):
    return svc.documents(actor.organization_id, actor.role, actor.user["id"], change_case_id)


@router.get("/change-cases/{change_case_id}/evidence/{dataset_id}")
def document_detail(change_case_id: int, dataset_id: int, actor: AuthContext = Depends(get_current_actor)):
    return svc.detail(actor.organization_id, actor.role, actor.user["id"], change_case_id, dataset_id)


@router.patch("/change-cases/{change_case_id}/evidence/{dataset_id}/metadata")
def update_metadata(change_case_id: int, dataset_id: int, body: MetadataRequest, actor: AuthContext = Depends(get_current_actor)):
    fields = svc.update_metadata(actor.organization_id, actor.role, actor.user["id"], change_case_id, dataset_id, body.model_dump(exclude_unset=True))
    _log(actor, "evidence_metadata", change_case_id, f"dataset_id={dataset_id} fields={','.join(fields)}")
    return svc.detail(actor.organization_id, actor.role, actor.user["id"], change_case_id, dataset_id)


@router.post("/change-cases/{change_case_id}/evidence/{dataset_id}/review")
def review_document(change_case_id: int, dataset_id: int, body: ReviewRequest, actor: AuthContext = Depends(get_current_actor)):
    result = svc.review(actor.organization_id, actor.role, actor.user["id"], change_case_id, dataset_id, body.action, body.reason, body.superseded_by)
    _log(actor, "evidence_review", change_case_id, f"dataset_id={dataset_id} action={body.action}")
    return result


@router.get("/change-cases/{change_case_id}/evidence/{dataset_id}/rows")
def document_rows(change_case_id: int, dataset_id: int, offset: int = 0, limit: int = 50, actor: AuthContext = Depends(get_current_actor)):
    return svc.rows(actor.organization_id, change_case_id, dataset_id, offset, limit)


@router.post("/change-cases/{change_case_id}/conflicts/scan")
def scan_conflicts(change_case_id: int, actor: AuthContext = Depends(get_current_actor)):
    svc.workspace._writable(actor.organization_id, change_case_id)
    result = svc.scan(actor.organization_id, change_case_id)
    _log(actor, "conflicts_scan", change_case_id, f"added={result['added']} closed={result['closed']}")
    return svc.documents(actor.organization_id, actor.role, actor.user["id"], change_case_id)


@router.post("/change-cases/{change_case_id}/conflicts/{conflict_id}/resolve")
def resolve_conflict(change_case_id: int, conflict_id: int, body: ResolveRequest, actor: AuthContext = Depends(get_current_actor)):
    result = svc.resolve_conflict(actor.organization_id, actor.role, actor.user["id"], change_case_id, conflict_id,
                                  body.resolution, body.note, body.preferred_dataset_id)
    _log(actor, "conflict_resolve", change_case_id, f"conflict_id={conflict_id} resolution={body.resolution}")
    return result
