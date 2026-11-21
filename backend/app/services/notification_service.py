from backend.app.repositories import notifications_repo, portfolio_repo

# Stage changes that people responsible for decisions should hear about.
_STAGE_NOTICES = {
    "evidence_review": ("evidence_review", "Evidence ready for review"),
    "engineering_review": ("decision_needed", "Engineering decision needed"),
    "approved": ("approved", "Program approved"),
    "rejected": ("rejected", "Program rejected"),
}


def notify_stage_change(organization_id: int, actor_user_id: int, change_case_id: int, to_stage: str, reason) -> None:
    notice = _STAGE_NOTICES.get(to_stage)
    if not notice:
        return
    kind, title = notice
    name = portfolio_repo.program_names(organization_id, [change_case_id]).get(change_case_id, "A program")
    body = f"{name}" + (f". {reason}" if reason else "")
    notifications_repo.create_many(organization_id, notifications_repo.admin_recipients(organization_id, actor_user_id),
                                   kind, title, body, f"/programs/{change_case_id}")


def notify_member_joined(organization_id: int, new_user_id: int, label: str) -> None:
    notifications_repo.create_many(organization_id, notifications_repo.admin_recipients(organization_id, new_user_id),
                                   "member_joined", "New team member", f"{label} accepted an invitation", "/settings?tab=team")


def inbox(organization_id: int, user_id: int, unread_only: bool = False) -> dict:
    return {"unread": notifications_repo.unread_count(organization_id, user_id),
            "items": notifications_repo.list_for_user(organization_id, user_id, unread_only)}


def mark_read(organization_id: int, user_id: int, notification_id: int) -> None:
    if not notifications_repo.mark_read(organization_id, user_id, notification_id):
        raise LookupError("Notification not found.")


def mark_all_read(organization_id: int, user_id: int) -> int:
    return notifications_repo.mark_all_read(organization_id, user_id)
