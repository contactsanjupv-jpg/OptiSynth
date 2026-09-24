"""
LOCAL DEV / DEMO ONLY -- creates one realistic demo organization and a
fully worked forced-substitution qualification change case, so the
product can be demonstrated end-to-end without a real customer's data.

SCENARIO (clearly synthetic, not real customer data):
  "AquaShield 400" is a fictional industrial anti-corrosion coating for
  structural steel. Its current formulation uses a PFAS-based
  fluorosurfactant leveling/anti-cratering agent that is now restricted.
  Historical data below represents past internal formulation trials of
  THIS coating (varying crosslinker ratio and cure temperature) tested
  for salt-spray corrosion resistance per ASTM B117 -- a real, standard
  industrial coating test method. Target spec: >=500 hours to first rust.
  Four PFAS-free candidate additives are proposed by different suppliers,
  each implying a different recommended crosslinker/cure-temperature
  formulation -- exactly the kind of decision this product is built to
  rank.

Usage:
    python3 scripts/seed_demo_case.py
"""
import sys
import os
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.app.repositories import organizations_repo, users_repo, memberships_repo, subscriptions_repo
from backend.app.security.passwords import hash_password
from backend.app.services import change_case_service, qualification_dataset_service
from backend.app.repositories._time import now_iso

DEMO_ORG_NAME = "Demo Coatings Co"
DEMO_EMAIL = "demo@democoatings.com"
DEMO_PASSWORD = "demo-password-for-local-use-only-123"


def _historical_csv() -> bytes:
    # 20 real-shaped, honestly-synthetic historical formulation trials.
    # salt_spray_hours rises with crosslinker_ratio and cure_temp_c, with
    # realistic noise -- NOT every historical trial cleared 500 hours,
    # matching how real formulation development actually looks.
    import random
    random.seed(42)
    rows = ["crosslinker_ratio,cure_temp_c,salt_spray_hours"]
    for i in range(20):
        crosslinker = 0.08 + (i % 10) * 0.009
        cure_temp = 140 + (i % 8) * 5
        base = (crosslinker - 0.08) * 4500 + (cure_temp - 140) * 8
        hours = max(80, base + random.uniform(-45, 45))
        rows.append(f"{crosslinker:.3f},{cure_temp},{round(hours)}")
    return ("\n".join(rows)).encode("utf-8")


def main():
    existing_user = users_repo.get_user_by_email(DEMO_EMAIL)
    if existing_user:
        print(f"Demo user {DEMO_EMAIL} already exists -- delete the local database "
              "(data/rdopt.db) and re-run migrations for a fully clean demo, or reuse "
              "the existing demo org as-is.")
        sys.exit(0)

    org_id = organizations_repo.create_organization(DEMO_ORG_NAME)
    user_id = users_repo.create_user(DEMO_EMAIL, hash_password(DEMO_PASSWORD), "Demo Chemist")
    memberships_repo.create_membership(user_id, org_id, role="owner")
    subscriptions_repo.create_trial_subscription(org_id)
    # Demo org needs an active, non-trial plan to run the paid diagnostic --
    # same entitlement rule every real customer goes through.
    from sqlalchemy import text
    from backend.app.config.database import db_transaction
    with db_transaction() as conn:
        conn.execute(text("UPDATE subscriptions SET plan='pilot' WHERE organization_id=:org"), {"org": org_id})

    spec = {
        "feature_columns": ["crosslinker_ratio", "cure_temp_c"],
        "target_metric": "salt_spray_hours",
        "target_value": 500.0,
        "direction": "maximize",
    }
    case = change_case_service.create_change_case(
        org_id, user_id,
        name="AquaShield 400 -- PFAS Fluorosurfactant Replacement",
        trigger_type="regulatory_restriction",
        restricted_substance="PFAS-based fluorosurfactant leveling agent",
        qualification_spec=spec,
    )
    change_case_service.update_status(org_id, case["id"], "active")

    result = qualification_dataset_service.ingest_qualification_csv(
        org_id, case["id"], user_id, spec, "aquashield_400_historical_trials.csv", _historical_csv(),
    )

    candidates = [
        ("EcoShield SF-100 (silicone-modified, fluorine-free)", {"crosslinker_ratio": 0.160, "cure_temp_c": 178}),
        ("PolyGuard NF-22 (non-fluorinated polymeric leveling agent)", {"crosslinker_ratio": 0.145, "cure_temp_c": 170}),
        ("HydroFlex PF-Free (hydrocarbon-based surfactant)", {"crosslinker_ratio": 0.105, "cure_temp_c": 155}),
        ("GreenCoat Alt-9 (early-stage bio-based candidate)", {"crosslinker_ratio": 0.082, "cure_temp_c": 141}),
    ]
    for name, features in candidates:
        change_case_service.add_candidate(org_id, case["id"], name, features)

    ranked = change_case_service.rank_change_case(org_id, case["id"])

    print(f"\nDemo org created: {DEMO_ORG_NAME}")
    print(f"Login: {DEMO_EMAIL} / {DEMO_PASSWORD}")
    print(f"Change case id: {case['id']}")
    print(f"Historical rows ingested: {result['rows_ingested']}\n")
    print("Ranked candidates:")
    for r in ranked:
        print(f"  {r['candidate_name']:55s} prob={r['predicted_probability']:.0%}  "
              f"uncertainty(std)={r['uncertainty_std']:.1f}")
    print("\nRun the frontend and log in with the credentials above to see this in the UI.")


if __name__ == "__main__":
    main()
