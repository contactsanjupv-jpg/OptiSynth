"""
NOT RUNTIME-TESTED IN THE SANDBOX: fastapi is not installed here. Syntax-
validated via py_compile only.
"""
from fastapi import APIRouter, Depends, Query

from backend.app.services import dashboard_service, audit_service, billing_service
from backend.app.api.dependencies import get_current_actor, AuthContext

router = APIRouter(prefix="/api", tags=["dashboard"])


@router.get("/dashboard/summary")
def dashboard_summary(actor: AuthContext = Depends(get_current_actor)):
    return dashboard_service.get_dashboard_summary(actor.organization_id)


@router.get("/audit-log")
def audit_log(project_id: int | None = Query(default=None), actor: AuthContext = Depends(get_current_actor)):
    return audit_service.list_for_organization(actor.organization_id, project_id)


@router.get("/billing/summary")
def billing_summary(actor: AuthContext = Depends(get_current_actor)):
    return billing_service.get_billing_summary(actor.organization_id)
