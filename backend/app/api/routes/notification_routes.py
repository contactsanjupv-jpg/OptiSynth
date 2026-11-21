from fastapi import APIRouter, Depends, Query

from backend.app.api.dependencies import get_current_actor, AuthContext
from backend.app.services import notification_service

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


@router.get("")
def inbox(unread_only: bool = Query(False), actor: AuthContext = Depends(get_current_actor)):
    return notification_service.inbox(actor.organization_id, actor.user["id"], unread_only)


@router.post("/read-all")
def read_all(actor: AuthContext = Depends(get_current_actor)):
    return {"marked": notification_service.mark_all_read(actor.organization_id, actor.user["id"])}


@router.post("/{notification_id}/read")
def read_one(notification_id: int, actor: AuthContext = Depends(get_current_actor)):
    notification_service.mark_read(actor.organization_id, actor.user["id"], notification_id)
    return {"ok": True}
