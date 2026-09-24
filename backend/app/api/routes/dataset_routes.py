"""
NOT RUNTIME-TESTED IN THE SANDBOX: fastapi/python-multipart are not
installed here. Syntax-validated via py_compile only.
"""
from fastapi import APIRouter, Depends, UploadFile, File

from backend.app.schemas.errors import ValidationError
from backend.app.services import project_service, dataset_service, audit_service
from backend.app.api.dependencies import get_current_actor, AuthContext

router = APIRouter(prefix="/api/projects", tags=["datasets"])


@router.post("/{project_id}/dataset", status_code=201)
async def upload_dataset(project_id: int, file: UploadFile = File(...),
                          actor: AuthContext = Depends(get_current_actor)):
    project = project_service.get_project_or_404(actor.organization_id, project_id)

    if not file.filename:
        raise ValidationError("No file was selected.")

    file_bytes = await file.read()
    result = dataset_service.ingest_csv(
        actor.organization_id, project_id, actor.user["id"], project, file.filename, file_bytes
    )
    # Never log raw dataset contents -- only the row count.
    audit_service.log(actor.organization_id, actor.user["id"], "dataset.upload", project_id,
                       detail=f"rows_ingested={result['rows_ingested']}")
    return result
