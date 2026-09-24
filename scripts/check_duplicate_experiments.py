"""
LOCAL DEV DIAGNOSTIC -- checks a project's experiments for exact
duplicate rows (same feature values AND same target value), which would
mean the same CSV got uploaded more than once. Exact duplicates inflate
cross-validated model metrics (a duplicate in the test fold is trivially
"predicted" by its twin in the training fold), so this matters for
trusting R²/MAE numbers, not just for tidiness.

Usage:
    python3 scripts/check_duplicate_experiments.py <project_id>
"""
import sys
import os
import json
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import text
from backend.app.config.database import db_connection


def main():
    if len(sys.argv) != 2:
        print("Usage: python3 scripts/check_duplicate_experiments.py <project_id>")
        sys.exit(1)

    project_id = int(sys.argv[1])

    with db_connection() as conn:
        rows = conn.execute(
            text("SELECT id, features_json, target_value, created_at FROM experiments "
                 "WHERE project_id = :pid ORDER BY id"),
            {"pid": project_id},
        ).mappings().all()

    if not rows:
        print(f"No experiments found for project {project_id}.")
        return

    signatures = []
    for r in rows:
        features = json.loads(r["features_json"])
        sig = (tuple(sorted(features.items())), round(r["target_value"], 4))
        signatures.append((r["id"], sig, r["created_at"]))

    counts = Counter(sig for _, sig, _ in signatures)
    duplicate_sigs = {sig: c for sig, c in counts.items() if c > 1}

    print(f"Total experiment rows: {len(rows)}")
    print(f"Unique rows (by feature values + target): {len(counts)}")
    print(f"Rows that are exact duplicates of another row: {len(rows) - len(counts)}")
    print()

    if not duplicate_sigs:
        print("No exact duplicates found -- this looks like genuinely new/different data, "
              "not the same file re-uploaded.")
        return

    print(f"Found {len(duplicate_sigs)} distinct rows that were uploaded more than once.")
    print("Example (first duplicate group):")
    example_sig = next(iter(duplicate_sigs))
    matching_ids = [(rid, ts) for rid, sig, ts in signatures if sig == example_sig]
    for rid, ts in matching_ids:
        print(f"  experiment id={rid}, uploaded at {ts}")
    print()
    print("If these timestamps are from two different uploads, the same source file "
          "(or overlapping data) was very likely uploaded twice.")


if __name__ == "__main__":
    main()