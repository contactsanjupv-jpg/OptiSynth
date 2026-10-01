"""
OPERATOR TOOL -- evidence-intake REVIEW of a customer CSV against a change case.
Read-only: stores nothing. Shows proposed column mappings and every unit /
test-condition / conflict finding, and whether the file would be accepted.

Usage:
    python3 scripts/review_dataset.py --org-id 12 --case-id 3 --file customer.csv
    python3 scripts/review_dataset.py --org-id 12 --case-id 3 --file customer.csv --options review.json

review.json (all keys optional):
    {"column_mapping": {"crosslinker_ratio": "Crosslinker Ratio"},
     "declared_units": {"salt_spray_hours": "h"},
     "condition_columns": ["Test Temp"],
     "reference_conditions": {"Test Temp": "25"},
     "exclude_rows": [12, 13], "exclusion_reason": "typos confirmed by customer 2026-09-28"}

When the review shows no blocking issues, upload the same file with the same
options through the app's dataset upload (POST /api/change-cases/{id}/dataset,
form field "options") -- that is what stores it.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.app.config import database
from backend.app.schemas.errors import ValidationError
from backend.app.services import change_case_service, qualification_dataset_service


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Review a customer CSV against a change case (read-only).")
    ap.add_argument("--org-id", type=int, required=True)
    ap.add_argument("--case-id", type=int, required=True)
    ap.add_argument("--file", required=True)
    ap.add_argument("--options", default=None, help="path to a JSON options file")
    args = ap.parse_args(argv)

    print(f"Database: {database.engine.url.render_as_string(hide_password=True)}")
    try:
        case = change_case_service.get_change_case_or_404(args.org_id, args.case_id)
    except LookupError:
        print(f"REFUSED: no change case {args.case_id} in organization {args.org_id}.")
        return 1
    try:
        with open(args.file, "rb") as f:
            file_bytes = f.read()
        options = None
        if args.options:
            with open(args.options, "r", encoding="utf-8") as f:
                options = json.load(f)
        result = qualification_dataset_service.preview_qualification_csv(
            json.loads(case["qualification_spec_json"]), os.path.basename(args.file), file_bytes, options,
        )
    except (OSError, ValueError) as e:
        print(f"REFUSED: {e}")
        return 1
    except ValidationError as e:
        print(f"REFUSED: {e}")
        return 1

    review = result["review"]
    print(f"Change case: {case['name']} (id {case['id']})")
    print(f"File SHA-256: {result['file_sha256']}")
    print("Column mapping used:")
    for canon, info in review["column_mapping"].items():
        print(f"  {canon:<28} <- {info['source_header']!r}  [{info['method']}]")
    if review["proposed_mapping"]:
        print("Proposed (NOT applied until you confirm in column_mapping):")
        for canon, header in review["proposed_mapping"].items():
            print(f"  {canon:<28} <- {header!r}")
    print("Findings:")
    for f in review["flags"]:
        rows = f" rows {f['rows'][:10]}" if f.get("rows") else ""
        print(f"  [{f['severity'].upper():8}] {f['code']}: {f['message']}{rows}")
    print("Counts:", json.dumps(review.get("counts", {})))
    if "data_quality_status" in result:
        print(f"Data quality: {result['data_quality_status']}")
    print("RESULT:", "WOULD BE ACCEPTED" if result["would_be_accepted"] else "NOT ACCEPTED -- resolve the BLOCKING findings")
    return 0 if result["would_be_accepted"] else 2


if __name__ == "__main__":
    sys.exit(main())