import os

from backend.app.repositories import reports_repo, organizations_repo, experiments_repo
from backend.app.services import optimization_service
from backend.app.services.report_builder import build_project_report


def generate_report(organization_id: int, user_id: int, project: dict) -> dict:
    # run_backtest_for_project internally validates there's enough data and
    # raises ValidationError (caught centrally, see middleware/error_handling.py)
    # if not -- no separate check needed here.
    backtest = optimization_service.run_backtest_for_project(organization_id, project)
    recommendations = optimization_service.get_recommendations(organization_id, user_id, project, n_recommend=5)
    n_rows = len(experiments_repo.list_experiments(organization_id, project["id"]))

    org = organizations_repo.get_organization(organization_id)
    org_name = org["name"] if org else "Customer"

    file_path = build_project_report(project, backtest, recommendations, n_rows, org_name)
    report_id = reports_repo.create_report(organization_id, project["id"], user_id, file_path, n_rows)
    return {"report_id": report_id, "file_path": file_path}


def list_reports(organization_id: int, project_id: int = None) -> list:
    return reports_repo.list_reports(organization_id, project_id)


def get_report_file(organization_id: int, report_id: int) -> dict:
    report = reports_repo.get_report(organization_id, report_id)
    if not report or not os.path.exists(report["file_path"]):
        raise LookupError("Report not found.")
    return report
