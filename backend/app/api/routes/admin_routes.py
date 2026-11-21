import json
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response

from backend.app.api.dependencies import get_current_actor, AuthContext
from backend.app.repositories import users_repo
from backend.app.schemas.admin_schemas import OrganizationUpdateRequest, DataExportRequest
from backend.app.schemas.errors import Forbidden, TooManyAttempts, ValidationError
from backend.app.security import login_throttle
from backend.app.security.passwords import verify_password
from backend.app.services import admin_service, audit_service, organization_service

router = APIRouter(prefix="/api", tags=["administration"])


@router.get("/organization")
def get_organization(actor: AuthContext = Depends(get_current_actor)):
    return organization_service.get_profile(actor.organization_id)


@router.patch("/organization")
def update_organization(body: OrganizationUpdateRequest, actor: AuthContext = Depends(get_current_actor)):
    profile, changed = organization_service.update_profile(actor.organization_id, actor.role, body.name, body.industry, body.website)
    if changed:
        audit_service.log(actor.organization_id, actor.user["id"], "organization.update", detail=f"changed={','.join(changed)}")
    return profile


@router.get("/audit/events")
def audit_events(user_id: Optional[int] = None, action: Optional[str] = None, q: Optional[str] = Query(None, max_length=200),
                 date_from: Optional[str] = None, date_to: Optional[str] = None, limit: int = 50, offset: int = 0,
                 actor: AuthContext = Depends(get_current_actor)):
    return admin_service.audit_history(actor.organization_id, actor.role, user_id, action, q, date_from, date_to, limit, offset)


@router.get("/security/events")
def security_events(actor: AuthContext = Depends(get_current_actor)):
    return admin_service.security_events(actor.organization_id, actor.user["id"])


@router.get("/data/uploads")
def upload_inventory(actor: AuthContext = Depends(get_current_actor)):
    return admin_service.upload_inventory(actor.organization_id, actor.role)


@router.post("/data/export")
def export_data(body: DataExportRequest, request: Request, actor: AuthContext = Depends(get_current_actor)):
    """Owner only, and the password is asked again: an export holds everything the organization stored."""
    address = request.client.host if request.client else "unknown"
    email = actor.user["email"]
    wait = login_throttle.throttle.retry_after(address, email)
    if wait:
        raise TooManyAttempts(wait, "Too many attempts. Please wait a few minutes and try again.")
    stored = users_repo.get_password_hash(actor.user["id"])
    if actor.role != "owner":
        raise Forbidden("Only an owner can export organization data.")
    if not stored or not verify_password(body.password, stored):
        login_throttle.throttle.record_failure(address, email)
        raise ValidationError("The password is not correct.", field="password")
    data = admin_service.export_all(actor.organization_id, actor.role)
    audit_service.log(actor.organization_id, actor.user["id"], "data.export")
    return Response(content=json.dumps(data, indent=2, default=str), media_type="application/json",
                    headers={"Content-Disposition": 'attachment; filename="optisynth-export.json"'})
