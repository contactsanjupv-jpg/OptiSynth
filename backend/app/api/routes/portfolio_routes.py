from typing import Optional

from fastapi import APIRouter, Depends, Query

from backend.app.api.dependencies import get_current_actor, AuthContext
from backend.app.services import portfolio_service

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])


@router.get("/overview")
def overview(days: int = Query(portfolio_service.DEFAULT_DAYS), actor: AuthContext = Depends(get_current_actor)):
    return portfolio_service.overview(actor.organization_id, days)


@router.get("/programs")
def programs(filter: Optional[str] = None, stage: Optional[str] = None, issue: Optional[str] = None,
             q: Optional[str] = Query(None, max_length=200), actor: AuthContext = Depends(get_current_actor)):
    return portfolio_service.programs(actor.organization_id, filter, stage, issue, q)
