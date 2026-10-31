"""Download filename for the qualification diagnostic report. Pure functions: no database, no framework."""
import re
import unicodedata
from datetime import datetime, timezone
from urllib.parse import quote

DEFAULT_TITLE = "qualification_diagnostic"
NO_RUN = "none"
MAX_TITLE_CHARS = 80
EXTENSION = ".docx"


def run_identifier(created_at):
    """'20261029T204217Z' from a stored ISO-8601 timestamp (UTC). None if missing or unparseable."""
    if not created_at:
        return None
    try:
        parsed = datetime.fromisoformat(str(created_at).strip())
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def latest_run_identifier(created_ats):
    """Identifier of the newest ranking run among stored prediction timestamps (None if there is none)."""
    return max(filter(None, (run_identifier(c) for c in created_ats)), default=None)


def _slug(text, limit):
    text = unicodedata.normalize("NFKC", str(text or ""))
    text = re.sub(r"[^\w]+", "_", text).strip("_")  # separators, quotes, control chars, dots, spaces -> "_"
    return text[:limit].rstrip("_")


def build_report_filename(title, change_case_id, run_id, ascii_only=False):
    slug = _slug(title, MAX_TITLE_CHARS)
    if ascii_only:
        slug = _slug(unicodedata.normalize("NFKD", slug).encode("ascii", "ignore").decode("ascii"), MAX_TITLE_CHARS)
    return f"{slug or DEFAULT_TITLE}_case_{int(change_case_id)}_run_{run_id or NO_RUN}{EXTENSION}"


def content_disposition(title, change_case_id, run_id):
    """ASCII `filename` fallback plus a UTF-8 `filename*` when the title has non-ASCII letters."""
    full = build_report_filename(title, change_case_id, run_id)
    plain = build_report_filename(title, change_case_id, run_id, ascii_only=True)
    value = f'attachment; filename="{plain}"'
    if full != plain:
        value += f"; filename*=UTF-8''{quote(full, safe='')}"
    return value
