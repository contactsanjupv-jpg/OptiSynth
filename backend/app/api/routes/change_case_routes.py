"""
Routes for the forced-substitution qualification diagnostic. Every
handler: session-derived actor via get_current_actor (organization_id is
NEVER taken from the request), thin (real logic lives in
change_case_service.py / change_case_rules.py), audit-logged on writes.
"""
import json

from fastapi import APIRouter, Depends, UploadFile, File
from fastapi.responses import FileResponse

from backend.app.schemas.errors import ValidationError
from backend.app.schemas.change_case_schemas import (
    CreateChangeCaseRequest,
    UpdateStatusRequest,
    AddCandidateRequest,
    RecordOutcomeRequest,
)
from backend.app.services import change_case_service, qualification_dataset_service, audit_service
from backend.app.repositories import change_case_repo, candidate_repo, organizations_repo
from backend.app.api.dependencies import get_current_actor, AuthContext

router = APIRouter(prefix="/api/change-cases", tags=["change-cases"])


@router.post("", status_code=201)
async def create_change_case(body: CreateChangeCaseRequest, actor: AuthContext = Depends(get_current_actor)):
    case = change_case_service.create_change_case(
        actor.organization_id, actor.user["id"], body.name, body.trigger_type,
        body.restricted_substance, body.qualification_spec,
    )
    audit_service.log(actor.organization_id, actor.user["id"], "change_case.create", detail=f"name={body.name}")
    return case


@router.get("")
async def list_change_cases(actor: AuthContext = Depends(get_current_actor)):
    return change_case_repo.list_change_cases(actor.organization_id)


@router.get("/{change_case_id}")
async def get_change_case(change_case_id: int, actor: AuthContext = Depends(get_current_actor)):
    return change_case_service.get_change_case_or_404(actor.organization_id, change_case_id)


@router.patch("/{change_case_id}/status")
async def update_status(change_case_id: int, body: UpdateStatusRequest,
                         actor: AuthContext = Depends(get_current_actor)):
    result = change_case_service.update_status(actor.organization_id, change_case_id, body.status)
    audit_service.log(actor.organization_id, actor.user["id"], "change_case.status_update",
                       detail=f"change_case_id={change_case_id} new_status={body.status}")
    return result


@router.post("/{change_case_id}/dataset", status_code=201)
async def upload_qualification_dataset(change_case_id: int, file: UploadFile = File(...),
                                        actor: AuthContext = Depends(get_current_actor)):
    case = change_case_service.get_change_case_or_404(actor.organization_id, change_case_id)
    if not file.filename:
        raise ValidationError("No file was selected.")
    file_bytes = await file.read()
    spec = json.loads(case["qualification_spec_json"])
    result = qualification_dataset_service.ingest_qualification_csv(
        actor.organization_id, change_case_id, actor.user["id"], spec, file.filename, file_bytes,
    )
    # Never log raw dataset contents -- only the row count, matching
    # dataset_routes.py's exact discipline for the original domain.
    audit_service.log(actor.organization_id, actor.user["id"], "qualification_dataset.upload", None,
                       detail=f"change_case_id={change_case_id} rows_ingested={result['rows_ingested']}")
    return result


@router.post("/{change_case_id}/candidates", status_code=201)
async def add_candidate(change_case_id: int, body: AddCandidateRequest,
                         actor: AuthContext = Depends(get_current_actor)):
    candidate = change_case_service.add_candidate(actor.organization_id, change_case_id, body.name, body.features)
    audit_service.log(actor.organization_id, actor.user["id"], "candidate.add",
                       detail=f"change_case_id={change_case_id} candidate_name={body.name}")
    return candidate


@router.get("/{change_case_id}/candidates")
async def list_candidates(change_case_id: int, actor: AuthContext = Depends(get_current_actor)):
    change_case_service.get_change_case_or_404(actor.organization_id, change_case_id)  # 404 if not owned
    return change_case_service.list_candidates_with_predictions(actor.organization_id, change_case_id)


@router.post("/{change_case_id}/rank")
async def rank_change_case(change_case_id: int, actor: AuthContext = Depends(get_current_actor)):
    results = change_case_service.rank_change_case(actor.organization_id, change_case_id)
    audit_service.log(actor.organization_id, actor.user["id"], "change_case.rank",
                       detail=f"change_case_id={change_case_id} candidates_ranked={len(results)}")
    return results


@router.post("/{change_case_id}/candidates/{candidate_id}/outcome", status_code=201)
async def record_outcome(change_case_id: int, candidate_id: int, body: RecordOutcomeRequest,
                          actor: AuthContext = Depends(get_current_actor)):
    change_case_service.get_change_case_or_404(actor.organization_id, change_case_id)  # 404 if not owned
    candidate = candidate_repo.get_candidate(actor.organization_id, candidate_id)
    if candidate is None or candidate["change_case_id"] != change_case_id:
        raise LookupError("Candidate not found.")
    result = change_case_service.record_outcome(
        actor.organization_id, candidate_id, body.recommended_experiment_id,
        body.actual_result, body.passed_spec, actor.user["id"],
    )
    audit_service.log(actor.organization_id, actor.user["id"], "qualification_outcome.record",
                       detail=f"change_case_id={change_case_id} candidate_id={candidate_id} passed_spec={body.passed_spec}")
    return result


@router.post("/{change_case_id}/report")
async def generate_report(change_case_id: int, actor: AuthContext = Depends(get_current_actor)):
    change_case_service.get_change_case_or_404(actor.organization_id, change_case_id)  # 404 if not owned
    org = organizations_repo.get_organization(actor.organization_id)
    file_path = change_case_service.generate_report(actor.organization_id, change_case_id, org["name"])
    audit_service.log(actor.organization_id, actor.user["id"], "change_case.report_generate",
                       detail=f"change_case_id={change_case_id}")
    return FileResponse(
        file_path,
        filename=f"qualification_diagnostic_{change_case_id}.docx",
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
