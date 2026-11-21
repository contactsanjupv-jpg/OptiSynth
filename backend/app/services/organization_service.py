from urllib.parse import urlparse

from backend.app.repositories import organizations_repo
from backend.app.schemas.errors import Forbidden, ValidationError

ADMIN_ROLES = ("owner", "admin")
INDUSTRIES = ("Specialty chemicals", "Coatings and paints", "Adhesives and sealants", "Polymers and plastics",
              "Automotive supply", "Aerospace supply", "Other")


def require_admin(role: str) -> None:
    if role not in ADMIN_ROLES:
        raise Forbidden("Only an owner or admin can do this.")


def _public(org: dict) -> dict:
    return {"id": org["id"], "name": org["name"], "industry": org.get("industry"), "website": org.get("website"),
            "created_at": org["created_at"], "updated_at": org.get("updated_at")}


def get_profile(organization_id: int) -> dict:
    org = organizations_repo.get_organization(organization_id)
    if not org:
        raise LookupError("Organization not found.")
    return {**_public(org), "industries": list(INDUSTRIES)}


def clean_profile(name, industry, website) -> tuple:
    name = (name or "").strip()
    if not name:
        raise ValidationError("Enter the organization name.", field="name")
    if len(name) > 120:
        raise ValidationError("Keep the name under 120 characters.", field="name")
    industry = (industry or "").strip() or None
    if industry and len(industry) > 80:
        raise ValidationError("Keep the industry under 80 characters.", field="industry")
    website = (website or "").strip() or None
    if website:
        parsed = urlparse(website)
        if parsed.scheme not in ("http", "https") or not parsed.netloc or len(website) > 200:
            raise ValidationError("Enter a web address that starts with http:// or https://.", field="website")
    return name, industry, website


def update_profile(organization_id: int, role: str, name, industry, website) -> tuple:
    """Returns (profile, changed field names). Only owners and admins may change it."""
    require_admin(role)
    before = organizations_repo.get_organization(organization_id)
    if not before:
        raise LookupError("Organization not found.")
    name, industry, website = clean_profile(name, industry, website)
    organizations_repo.update_profile(organization_id, name, industry, website)
    changed = [f for f, new in (("name", name), ("industry", industry), ("website", website)) if (before.get(f) or None) != new]
    return get_profile(organization_id), changed
