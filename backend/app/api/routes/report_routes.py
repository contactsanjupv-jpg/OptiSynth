"""
NOT RUNTIME-TESTED IN THE SANDBOX: fastapi is not installed here. Syntax-
validated via py_compile only.
"""
from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse

from backend.app.services import project_service, report_service, audit_service
from backend.app.api.dependencies import get_current_actor, AuthContext

router = APIRouter(prefix="/api", tags=["reports"])


@router.post("/projects/{project_id}/report", status_code=201)
def generate_report(project_id: int, actor: AuthContext = Depends(get_current_actor)):
    project = project_service.get_project_or_404(actor.organization_id, project_id)
    result = report_service.generate_report(actor.organization_id, actor.user["id"], project)
    audit_service.log(actor.organization_id, actor.user["id"], "report.generate", project_id)
    return {"report_id": result["report_id"]}


@router.get("/projects/{project_id}/reports")
def list_project_reports(project_id: int, actor: AuthContext = Depends(get_current_actor)):
    project_service.get_project_or_404(actor.organization_id, project_id)
    return report_service.list_reports(actor.organization_id, project_id)


@router.get("/reports/{report_id}/download")
def download_report(report_id: int, actor: AuthContext = Depends(get_current_actor)):
    report = report_service.get_report_file(actor.organization_id, report_id)
    return FileResponse(
        report["file_path"],
        filename=f"report_project_{report['project_id']}_{report_id}.docx",
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
