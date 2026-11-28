"""Level 6: decision setup, customer entered values, constraints, objectives and immutable analyses.
Run:  python3 -m unittest backend.tests.test_decision_engine_api -v"""
import os
import re
import shutil
import unittest
import uuid

from alembic import command
from sqlalchemy import create_engine, event, text

from backend.tests.test_migration_portability import _config, _UseDatabase
from backend.tests.test_model_validation_center import Base


def api(client, case_id, path=""):
    return f"/api/change-cases/{case_id}/decision{path}"


class DecisionBase(Base):
    dir_name = "_test_env_decision"

    def candidates(self, client, case_id, specs):
        ids = {}
        for name, ratio, temp in specs:
            r = client.post(f"/api/change-cases/{case_id}/candidates", json={"name": name, "features": {"crosslinker_ratio": ratio, "cure_temp_c": temp}})
            self.assertEqual(r.status_code, 201, r.text)
            ids[name] = r.json()["id"]
        return ids

    def attribute(self, client, case_id, key, vtype="number", unit=None, category="cost", label=None):
        r = client.post(api(client, case_id, "/attributes"), json={"key": key, "label": label or key.title(), "value_type": vtype, "unit": unit, "category": category})
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()["id"]

    def value(self, client, case_id, cand, attr, value, note="Supplier quote Q-1"):
        r = client.put(api(client, case_id, f"/candidates/{cand}/attributes/{attr}"), json={"value": value, "source_note": note})
        self.assertEqual(r.status_code, 200, r.text)

    def constraint(self, client, case_id, **kw):
        body = {"name": "c", "kind": "hard", "category": "cost_limit", "rationale": "Customer requirement"}
        body.update(kw)
        r = client.post(api(client, case_id, "/constraints"), json=body)
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()["id"]

    def objective(self, client, case_id, name, ref, direction):
        r = client.post(api(client, case_id, "/objectives"), json={"name": name, "ref": ref, "direction": direction})
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()["id"]

    def run_analysis(self, client, case_id):
        r = client.post(api(client, case_id, "/analyses"))
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()["analysis"]

    def scenario(self, label):
        """Three ranked candidates. Cost is a customer attribute: A 50, B 150, C not entered."""
        org, client, case_id, ids = self.case_with_evidence(label, candidates=0)
        cands = self.candidates(client, case_id, [("A", 0.10, 150.0), ("B", 0.12, 160.0), ("C", 0.14, 170.0)])
        self.assertEqual(client.post(f"/api/change-cases/{case_id}/rank").status_code, 200)
        cost = self.attribute(client, case_id, "cost", unit="USD")
        self.value(client, case_id, cands["A"], cost, 50)
        self.value(client, case_id, cands["B"], cost, 150)
        listing = {c["candidate_name"]: c for c in client.get(f"/api/change-cases/{case_id}/candidates").json()}
        return org, client, case_id, cands, cost, listing


class TestSetup(DecisionBase):
    dir_name = "_test_env_decision_setup"

    def test_attributes_validation_and_archive_guard(self):
        org, client, case_id, cands, cost, listing = self.scenario("Attr")
        w = client.get(api(client, case_id)).json()
        self.assertEqual([a["key"] for a in w["attributes"]], ["cost"])
        self.assertEqual(client.post(api(client, case_id, "/attributes"), json={"key": "cost", "label": "x", "value_type": "number", "category": "cost"}).status_code, 400)
        self.assertEqual(client.post(api(client, case_id, "/attributes"), json={"key": "Bad Key", "label": "x", "value_type": "number", "category": "cost"}).status_code, 422)
        self.assertEqual(client.post(api(client, case_id, "/attributes"), json={"key": "supplier", "label": "S", "value_type": "text", "unit": "kg", "category": "supplier"}).status_code, 400)
        self.assertEqual(client.post(api(client, case_id, "/attributes"), json={"key": "other1", "label": "S", "value_type": "text", "category": "nonsense"}).status_code, 400)
        self.constraint(client, case_id, name="Cost cap", ref="attr:cost", operator="<=", value=100, unit="USD")
        r = client.patch(api(client, case_id, f"/attributes/{cost}"), json={"status": "archived"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("Cost cap", r.json()["error"])

    def test_values_need_a_source_a_valid_type_and_an_active_candidate(self):
        org, client, case_id, cands, cost, listing = self.scenario("Vals")
        url = api(client, case_id, f"/candidates/{cands['C']}/attributes/{cost}")
        self.assertEqual(client.put(url, json={"value": 70}).status_code, 422)
        self.assertEqual(client.put(url, json={"value": 70, "source_note": "x"}).status_code, 422)  # a source of at least 3 characters
        self.assertEqual(client.put(url, json={"value": "cheap", "source_note": "Quote 7"}).status_code, 400)
        self.assertEqual(client.put(url, json={"value": True, "source_note": "Quote 7"}).status_code, 400)
        ok = client.put(url, json={"value": 70, "source_note": "Quote 7"})
        self.assertEqual(ok.status_code, 200)
        w = client.get(api(client, case_id)).json()
        row = {r["name"]: r for r in w["candidate_values"]}
        self.assertEqual(row["C"]["values"][str(cost)]["value"], 70)
        self.assertEqual(row["C"]["values"][str(cost)]["source_note"], "Quote 7")
        self.assertEqual(client.delete(url).status_code, 200)
        self.assertIsNone({r["name"]: r for r in client.get(api(client, case_id)).json()["candidate_values"]}["C"]["values"][str(cost)])  # unknown again, not zero
        self.assertEqual(client.delete(url).status_code, 404)
        self.assertEqual(client.delete(api(client, case_id, f"/candidates/999999/attributes/{cost}")).status_code, 404)
        self.assertEqual(client.post(f"/api/change-cases/{case_id}/candidates/{cands['C']}/archive").status_code, 200)
        self.assertEqual(client.put(url, json={"value": 70, "source_note": "Quote 7"}).status_code, 404)

    def test_constraint_validation(self):
        org, client, case_id, cands, cost, listing = self.scenario("Con")
        mk = lambda **kw: client.post(api(client, case_id, "/constraints"), json={"name": "c", "kind": "hard", "category": "cost_limit", "rationale": "Customer requirement", **kw})  # noqa: E731
        self.assertEqual(mk(ref="attr:cost", operator="<=", value=100).status_code, 201)
        self.assertEqual(mk(ref="attr:nope", operator="<=", value=100).status_code, 400)
        self.assertEqual(mk(ref="attr:cost", operator="equals", value="x").status_code, 400)  # text operator on a number
        self.assertEqual(mk(ref="predicted:target", operator="==", value=100).status_code, 400)  # exact match on a prediction
        self.assertEqual(mk(ref="attr:cost", operator="between", value=[5, 1]).status_code, 400)
        self.assertEqual(mk(ref="attr:cost", operator="between", value=5).status_code, 400)
        self.assertEqual(mk(ref="attr:cost", operator="<=", value=100, unit="EUR").status_code, 400)  # no silent unit mixing
        self.assertEqual(mk(ref="attr:cost", operator="<=", value=100, unit="USD").status_code, 201)
        self.assertEqual(mk(ref="attr:cost", operator="<=", value="abc").status_code, 400)
        self.assertEqual(mk(ref="feature:crosslinker_ratio", operator="between", value=[0.1, 0.2], category="composition_range").status_code, 201)
        self.assertEqual(mk(ref="attr:cost", operator="<=", value=100, category="made_up").status_code, 400)
        self.assertEqual(mk(ref="attr:cost", operator="<=", value=100, rationale="").status_code, 422)
        w = client.get(api(client, case_id)).json()
        by = {c["ref"]: c for c in w["constraints"]}
        self.assertEqual(by["attr:cost"]["unit"], "USD")
        self.assertEqual(by["feature:crosslinker_ratio"]["unit"], "fraction")  # the unit the program declared for the input

    def test_constraint_edit_archive_and_restore_are_revalidated(self):
        org, client, case_id, cands, cost, listing = self.scenario("Edit")
        cid = self.constraint(client, case_id, ref="attr:cost", operator="<=", value=100)
        self.assertEqual(client.patch(api(client, case_id, f"/constraints/{cid}"), json={"value": "abc"}).status_code, 400)
        self.assertEqual(client.patch(api(client, case_id, f"/constraints/{cid}"), json={"value": 90}).json()["value"], 90.0)
        self.assertEqual(client.patch(api(client, case_id, f"/constraints/{cid}"), json={"status": "archived"}).json()["status"], "archived")
        self.assertEqual(client.patch(api(client, case_id, f"/constraints/{cid}"), json={"status": "active"}).json()["status"], "active")
        self.assertEqual(client.patch(api(client, case_id, "/constraints/99999"), json={"name": "x"}).status_code, 404)

    def test_objectives_need_numbers_and_stay_few(self):
        org, client, case_id, cands, cost, listing = self.scenario("Obj")
        text_attr = self.attribute(client, case_id, "supplier", "text", category="supplier")
        mk = lambda ref: client.post(api(client, case_id, "/objectives"), json={"name": "o", "ref": ref, "direction": "minimize"})  # noqa: E731
        self.assertEqual(mk("attr:supplier").status_code, 400)
        self.assertEqual(mk("attr:cost").status_code, 201)
        self.assertEqual(mk("attr:cost").status_code, 400)  # already an objective
        self.assertEqual(mk("predicted:target").status_code, 201)
        self.assertEqual(mk("feature:cure_temp_c").status_code, 201)
        extras = [self.attribute(client, case_id, f"extra{i}", unit="u") for i in range(3)]
        for i in range(3):
            self.assertEqual(mk(f"attr:extra{i}").status_code, 201)
        r = mk("feature:crosslinker_ratio")
        self.assertEqual(r.status_code, 400)
        self.assertIn("at most 6", r.json()["error"])


class TestPermissions(DecisionBase):
    dir_name = "_test_env_decision_perms"

    def test_members_enter_values_but_only_managers_change_the_setup_and_only_reviewers_run(self):
        org, client, case_id, cands, cost, listing = self.scenario("Perm")
        member = self.member_client(client, "member")
        self.assertEqual(member.get(api(client, case_id)).status_code, 200)
        w = member.get(api(client, case_id)).json()
        self.assertEqual(w["permissions"], {"can_manage": False, "can_run": False, "can_enter_values": True})
        self.assertEqual(member.post(api(client, case_id, "/attributes"), json={"key": "x1", "label": "x", "value_type": "number", "category": "cost"}).status_code, 403)
        self.assertEqual(member.post(api(client, case_id, "/constraints"), json={"name": "c", "kind": "hard", "category": "cost_limit", "ref": "attr:cost", "operator": "<=", "value": 1, "rationale": "abc"}).status_code, 403)
        self.assertEqual(member.post(api(client, case_id, "/objectives"), json={"name": "o", "ref": "attr:cost", "direction": "minimize"}).status_code, 403)
        self.assertEqual(member.put(api(client, case_id, f"/candidates/{cands['C']}/attributes/{cost}"), json={"value": 80, "source_note": "Quote 9"}).status_code, 200)
        self.assertEqual(member.post(api(client, case_id, "/analyses")).status_code, 403)
        client.post(f"/api/change-cases/{case_id}/reviewers", json={"user_id": self.person(member)})
        self.assertEqual(member.post(api(client, case_id, "/analyses")).status_code, 201)
        admin = self.member_client(client, "admin")
        self.assertEqual(admin.post(api(client, case_id, "/attributes"), json={"key": "x1", "label": "x", "value_type": "number", "category": "cost"}).status_code, 201)

    def test_other_organizations_and_anonymous_users_see_nothing(self):
        org, client, case_id, cands, cost, listing = self.scenario("Iso")
        a = self.run_analysis(client, case_id)
        _, other = self.org_and_client("OtherDecision")
        calls = [other.get(api(other, case_id)), other.post(api(other, case_id, "/analyses")), other.get(api(other, case_id, f"/analyses/{a['id']}")),
                 other.post(api(other, case_id, "/attributes"), json={"key": "zz", "label": "z", "value_type": "number", "category": "cost"}),
                 other.put(api(other, case_id, f"/candidates/{cands['A']}/attributes/{cost}"), json={"value": 1, "source_note": "abc"}),
                 other.patch(api(other, case_id, "/constraints/1"), json={"name": "x"})]
        for r in calls:
            self.assertEqual(r.status_code, 404, r.text)
        self.assertEqual(self.client_factory().get(api(client, case_id)).status_code, 401)

    def test_a_closed_program_cannot_be_changed_or_analysed(self):
        org, client, case_id, cands, cost, listing = self.scenario("Closed")
        client.patch(f"/api/change-cases/{case_id}/status", json={"status": "active"})
        client.patch(f"/api/change-cases/{case_id}/status", json={"status": "completed"})
        self.assertEqual(client.post(api(client, case_id, "/analyses")).status_code, 400)
        self.assertEqual(client.post(api(client, case_id, "/attributes"), json={"key": "zz", "label": "z", "value_type": "number", "category": "cost"}).status_code, 400)
        self.assertEqual(client.get(api(client, case_id)).status_code, 200)

    def test_audit_records_setup_values_and_runs(self):
        org, client, case_id, cands, cost, listing = self.scenario("Audit")
        self.constraint(client, case_id, ref="attr:cost", operator="<=", value=100)
        self.run_analysis(client, case_id)
        from backend.app.config import database
        with database.db_connection() as conn:
            rows = conn.execute(text("SELECT action, detail FROM audit_logs WHERE organization_id = :o"), {"o": org}).fetchall()
        actions = {r[0] for r in rows}
        for a in ("decision.attribute_create", "decision.value_set", "decision.constraint_create", "decision.analysis_run"):
            self.assertIn(a, actions)
        detail = [r[1] for r in rows if r[0] == "decision.value_set"][0]
        self.assertIn("source='Supplier quote Q-1'", detail)


class TestAnalysis(DecisionBase):
    dir_name = "_test_env_decision_analysis"

    def predicted(self, listing, name):
        p = listing[name]["latest_prediction"]
        return p["predicted_value"], p["uncertainty_std"]

    def test_groups_front_missing_information_and_unknown_stays_unknown(self):
        org, client, case_id, cands, cost, listing = self.scenario("Groups")
        floor = min(mu - 3 * sd for mu, sd in (self.predicted(listing, n) for n in "ABC")) - 1
        self.constraint(client, case_id, name="Strength floor", category="property_threshold", ref="predicted:target", operator=">=", value=floor)
        self.constraint(client, case_id, name="Cost cap", ref="attr:cost", operator="<=", value=100, unit="USD")
        self.objective(client, case_id, "Strength", "predicted:target", "maximize")
        self.objective(client, case_id, "Cost", "attr:cost", "minimize")
        a = self.run_analysis(client, case_id)
        groups = {r["name"]: r["group"] for r in a["candidates"].values()}
        self.assertEqual(groups, {"A": "feasible", "B": "rejected", "C": "unresolved"})
        c = {r["name"]: r for r in a["candidates"].values()}
        self.assertEqual(a["summary"]["front"], [cands["A"]])
        self.assertEqual(c["C"]["pareto"]["status"], "not_eligible")  # never silently feasible
        self.assertTrue(any(m["needed_for"] == "Cost cap" for m in c["C"]["missing_information"]))
        self.assertEqual([h["state"] for h in c["C"]["hard"]], ["satisfied", "unknown"])
        self.assertEqual([h["state"] for h in c["B"]["hard"]], ["satisfied", "violated"])
        self.assertIn("Cost cap", c["B"]["explanation"]["dominating_constraints"][0]["name"])
        impact = {x["name"]: x for x in a["summary"]["constraint_impact"]}
        self.assertEqual(impact["Cost cap"]["sole_blocker_for"], [cands["B"]])
        self.assertEqual(a["order"][0], cands["A"])

    def test_candidate_comparison_carries_everything_the_acceptance_list_asks_for(self):
        org, client, case_id, cands, cost, listing = self.scenario("Compare")
        self.constraint(client, case_id, name="Cost cap", ref="attr:cost", operator="<=", value=100, unit="USD")
        self.constraint(client, case_id, name="Prefer cheap", kind="soft", ref="attr:cost", operator="<=", value=40, unit="USD")
        self.objective(client, case_id, "Strength", "predicted:target", "maximize")
        a = self.run_analysis(client, case_id)
        row = a["candidates"][str(cands["A"])]
        self.assertTrue(row["predicted"]["available"])
        for key in ("value", "sd", "interval", "unit"):
            self.assertIn(key, row["predicted"])
        ctx = row["context"]
        for key in ("domain_label", "input_space_label", "decision_support_label", "model_version_number", "validation_state"):
            self.assertIn(key, ctx)
        self.assertIsNotNone(ctx["domain_label"])
        self.assertIsNotNone(ctx["input_space_label"])
        self.assertEqual(row["soft_summary"], {"met": 0, "missed": 1, "unknown": 0, "total": 1})
        self.assertIn("missing_information", row)
        self.assertEqual(row["physical_validation"]["status"], "none_recorded")
        self.assertEqual(row["qualification_status"], "not_qualified")
        self.assertEqual(a["target"]["unit"], "h")
        self.assertIn("No weights", a["summary"]["method"])

    def test_a_predicted_value_whose_interval_crosses_the_limit_is_not_a_pass(self):
        org, client, case_id, cands, cost, listing = self.scenario("Interval")
        mu, sd = self.predicted(listing, "A")
        self.assertGreater(sd, 0)
        self.constraint(client, case_id, name="Right at the estimate", category="property_threshold", ref="predicted:target", operator=">=", value=mu - sd / 10)
        a = self.run_analysis(client, case_id)
        row = a["candidates"][str(cands["A"])]
        self.assertEqual(row["hard"][0]["state"], "unknown")
        self.assertTrue(row["hard"][0]["point_estimate_met"])
        self.assertEqual(row["group"], "unresolved")
        self.assertEqual(row["predicted"]["interval"][0] < mu < row["predicted"]["interval"][1], True)
        self.assertIn("not a calibrated guarantee", " ".join(row["explanation"]["supported_by"]))

    def test_a_stale_prediction_is_unknown_not_used(self):
        org, client, case_id, cands, cost, listing = self.scenario("Stale")
        self.constraint(client, case_id, name="Floor", category="property_threshold", ref="predicted:target", operator=">=", value=-1e9)
        client.patch(f"/api/change-cases/{case_id}/candidates/{cands['A']}", json={"features": {"crosslinker_ratio": 0.11, "cure_temp_c": 152.0}})
        a = self.run_analysis(client, case_id)
        row = a["candidates"][str(cands["A"])]
        self.assertFalse(row["predicted"]["available"])
        self.assertEqual(row["hard"][0]["state"], "unknown")
        self.assertIn("inputs changed", row["hard"][0]["detail"])
        self.assertEqual(row["group"], "unresolved")

    def test_exclusions_supplier_rules_and_composition_ranges(self):
        org, client, case_id, cands, cost, listing = self.scenario("Rules")
        mats = self.attribute(client, case_id, "materials", "text_list", category="composition", label="Materials")
        sup = self.attribute(client, case_id, "supplier", "text", category="supplier", label="Supplier")
        ok = self.attribute(client, case_id, "line_ok", "boolean", category="manufacturing", label="Runs on line 2")
        self.value(client, case_id, cands["A"], mats, ["resin", "PFAS binder"])
        self.value(client, case_id, cands["B"], mats, ["resin"])
        self.value(client, case_id, cands["A"], sup, "Acme")
        self.value(client, case_id, cands["B"], sup, "Gamma")
        self.value(client, case_id, cands["B"], ok, False)
        self.constraint(client, case_id, name="No PFAS", category="material_exclusion", ref="attr:materials", operator="excludes_any", value=["pfas binder"])
        self.constraint(client, case_id, name="Approved suppliers", category="supplier_restriction", ref="attr:supplier", operator="in", value=["Acme", "Beta"])
        self.constraint(client, case_id, name="Line 2", category="manufacturing_restriction", ref="attr:line_ok", operator="equals", value=True)
        self.constraint(client, case_id, name="Ratio window", category="composition_range", ref="feature:crosslinker_ratio", operator="between", value=[0.09, 0.13])
        a = self.run_analysis(client, case_id)
        by = {r["name"]: {h["name"]: h["state"] for h in r["hard"]} for r in a["candidates"].values()}
        self.assertEqual(by["A"], {"No PFAS": "violated", "Approved suppliers": "satisfied", "Line 2": "unknown", "Ratio window": "satisfied"})
        self.assertEqual(by["B"], {"No PFAS": "satisfied", "Approved suppliers": "violated", "Line 2": "violated", "Ratio window": "satisfied"})
        self.assertEqual(by["C"], {"No PFAS": "unknown", "Approved suppliers": "unknown", "Line 2": "unknown", "Ratio window": "violated"})

    def test_pareto_with_customer_objectives_and_no_weights(self):
        org, client, case_id, cands, cost, listing = self.scenario("Pareto")
        env = self.attribute(client, case_id, "co2", unit="kg", category="environmental", label="Carbon footprint")
        for name, c, e in (("A", 50, 8), ("B", 40, 9), ("C", 60, 7)):
            self.value(client, case_id, cands[name], cost, c)
            self.value(client, case_id, cands[name], env, e)
        self.objective(client, case_id, "Cost", "attr:cost", "minimize")
        self.objective(client, case_id, "Carbon", "attr:co2", "minimize")
        a = self.run_analysis(client, case_id)
        self.assertEqual(sorted(a["summary"]["front"]), sorted(cands.values()))  # three way trade off: nobody dominates
        self.value(client, case_id, cands["C"], cost, 70)
        self.value(client, case_id, cands["C"], env, 10)
        b = self.run_analysis(client, case_id)
        self.assertEqual(b["candidates"][str(cands["C"])]["pareto"]["status"], "dominated")
        self.assertEqual(sorted(b["candidates"][str(cands["C"])]["pareto"]["dominated_by"]), sorted([cands["A"], cands["B"]]))
        self.assertNotIn("weight", " ".join(a["summary"]["method"].lower().split("no weights")[0:1]))

    def test_no_objectives_means_no_trade_off(self):
        org, client, case_id, cands, cost, listing = self.scenario("NoObj")
        a = self.run_analysis(client, case_id)
        self.assertTrue(all(r["pareto"]["status"] == "no_objectives" for r in a["candidates"].values()))
        self.assertEqual(a["summary"]["front"], [])

    def test_a_front_candidate_is_never_called_qualified_even_with_physical_results(self):
        org, client, case_id, cands, cost, listing = self.scenario("Qual")
        self.objective(client, case_id, "Cost", "attr:cost", "minimize")
        from backend.app.config import database
        with database.db_transaction() as conn:
            conn.execute(text("INSERT INTO qualification_outcomes (organization_id, candidate_id, actual_result, passed_spec, created_at) VALUES (:o, :c, '99', 1, 't')"),
                         {"o": org, "c": cands["A"]})
        a = self.run_analysis(client, case_id)
        top = a["candidates"][str(a["order"][0])]
        self.assertEqual(top["pareto"]["status"], "front")
        self.assertEqual(top["qualification_status"], "not_qualified")
        self.assertEqual(top["physical_validation"]["status"], "outcomes_recorded")
        e = top["explanation"]
        self.assertTrue(e["qualification"].startswith("Not qualified."))
        self.assertIn("does not by itself qualify", e["qualification"])
        for row in a["candidates"].values():
            text_ = " ".join([row["explanation"]["headline"], *row["explanation"]["promising"], *row["explanation"]["could_change"]])
            self.assertNotRegex(text_, r"(?i)\bqualified\b")
        self.assertTrue(a["qualification"].startswith("Not qualified."))

    def test_analyses_are_immutable_snapshots_and_the_workspace_says_when_setup_moved_on(self):
        org, client, case_id, cands, cost, listing = self.scenario("Snap")
        cid = self.constraint(client, case_id, name="Cost cap", ref="attr:cost", operator="<=", value=100, unit="USD")
        a1 = self.run_analysis(client, case_id)
        a1b = self.run_analysis(client, case_id)
        self.assertEqual(a1["input_hash"], a1b["input_hash"])  # same inputs, same hash
        self.assertNotEqual(a1["id"], a1b["id"])
        self.assertFalse(client.get(api(client, case_id)).json()["analysis_outdated"])
        client.patch(api(client, case_id, f"/constraints/{cid}"), json={"value": 10})
        self.value(client, case_id, cands["C"], cost, 5)
        self.assertTrue(client.get(api(client, case_id)).json()["analysis_outdated"])
        again = client.get(api(client, case_id, f"/analyses/{a1['id']}")).json()
        self.assertEqual(again["candidates"], a1["candidates"])  # the old record is exactly what it was
        self.assertEqual(again["constraints"][0]["value"], 100.0)
        a2 = self.run_analysis(client, case_id)
        self.assertNotEqual(a2["input_hash"], a1["input_hash"])
        self.assertEqual(len(client.get(api(client, case_id)).json()["analyses"]), 3)
        self.assertEqual(client.get(api(client, case_id, "/analyses/999999")).status_code, 404)

    def test_archived_rules_are_left_out_of_new_analyses_but_kept_in_old_ones(self):
        org, client, case_id, cands, cost, listing = self.scenario("Archive")
        cid = self.constraint(client, case_id, name="Cost cap", ref="attr:cost", operator="<=", value=100, unit="USD")
        a1 = self.run_analysis(client, case_id)
        self.assertEqual(a1["candidates"][str(cands["B"])]["group"], "rejected")
        client.patch(api(client, case_id, f"/constraints/{cid}"), json={"status": "archived"})
        a2 = self.run_analysis(client, case_id)
        self.assertEqual(a2["candidates"][str(cands["B"])]["group"], "feasible")
        self.assertEqual(client.get(api(client, case_id, f"/analyses/{a1['id']}")).json()["candidates"][str(cands["B"])]["group"], "rejected")

    def test_prediction_numbers_and_ranking_are_untouched(self):
        org, client, case_id, cands, cost, listing = self.scenario("Untouched")
        before = client.get(f"/api/change-cases/{case_id}/candidates").json()
        self.objective(client, case_id, "Cost", "attr:cost", "minimize")
        self.run_analysis(client, case_id)
        after = client.get(f"/api/change-cases/{case_id}/candidates").json()
        strip = lambda rows: [(r["candidate_name"], r["latest_prediction"]["predicted_value"], r["latest_prediction"]["predicted_probability"], r.get("rank")) for r in rows]  # noqa: E731
        self.assertEqual(strip(before), strip(after))

    def test_nothing_is_invented_when_no_business_values_are_entered(self):
        org, client, case_id, ids = self.case_with_evidence("Bare", candidates=0)
        cands = self.candidates(client, case_id, [("A", 0.10, 150.0)])
        client.post(f"/api/change-cases/{case_id}/rank")
        self.attribute(client, case_id, "cost", unit="USD")
        self.constraint(client, case_id, name="Cost cap", ref="attr:cost", operator="<=", value=100, unit="USD")
        a = self.run_analysis(client, case_id)
        row = a["candidates"][str(cands["A"])]
        self.assertEqual((row["group"], row["hard"][0]["state"]), ("unresolved", "unknown"))
        self.assertEqual(row["hard"][0]["detail"], "No value is recorded.")
        w = client.get(api(client, case_id)).json()
        self.assertIn("Anything not entered is shown as unknown", w["no_invented_data"])

    def test_a_program_without_candidates_cannot_be_analysed(self):
        org, client, case_id, ids = self.case_with_evidence("Empty", candidates=0)
        r = client.post(api(client, case_id, "/analyses"))
        self.assertEqual(r.status_code, 400)
        self.assertIn("at least one candidate", r.json()["error"])


class TestFrontendWiring(unittest.TestCase):
    ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "frontend"))

    def read(self, rel):
        with open(os.path.join(self.ROOT, rel), encoding="utf-8") as f:
            return f.read()

    def test_every_decision_api_call_has_a_backend_route(self):
        os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production")
        os.environ.setdefault("PASSWORD_PEPPER", "test-password-pepper-not-for-production")
        from backend.app.main import create_app
        routes = {(m, r.path) for r in create_app().routes for m in getattr(r, "methods", None) or ()}
        src = self.read("lib/api/decision.ts")
        calls = [("/api/change-cases/1/decision" + path, method) for path, method in
                 re.findall(r"`\$\{base\(programId\)\}([^`]*)`(?:, \{ method: \"(\w+)\")?", src)]
        calls.append(("/api/change-cases/1/decision", ""))  # the workspace call uses base(programId) alone
        self.assertGreaterEqual(len(calls), 10)
        for path, method in calls:
            concrete = re.sub(r"\$\{[^}]*\}", "1", path)
            found = any(m == (method or "GET") and re.match("^" + re.sub(r"\{[^}]+\}", "[^/]+", rp) + "$", concrete) for m, rp in routes)
            self.assertTrue(found, f"no backend route for {method or 'GET'} {path}")

    def test_files_exist_follow_the_copy_standard_and_are_mounted(self):
        files = ("lib/api/decision.ts", "components/decision/DecisionWorkspace.tsx", "components/decision/DecisionSetup.tsx",
                 "components/decision/DecisionResults.tsx", "styles/components/decision.css")
        for rel in files:
            self.assertTrue(os.path.isfile(os.path.join(self.ROOT, rel)), rel)
        self.assertIn("DecisionWorkspace", self.read("app/programs/[id]/page.tsx"))
        for rel in files[1:4]:
            text_ = re.sub(r"/\*.*?\*/", "", self.read(rel), flags=re.S)
            for banned in ("\u2014", "\u2013", " -- ", " \u00b7 "):
                self.assertNotIn(banned, text_, rel)
            self.assertNotRegex(text_, r"(?i)diagnostic")
            self.assertNotRegex(text_, r"(?i)\bis qualified\b|\bqualified candidate\b|\bwinner\b|\bbest candidate\b")

    def test_results_screen_states_that_a_position_is_not_qualification(self):
        results = self.read("components/decision/DecisionResults.tsx")
        self.assertIn("qualification", results.lower())
        self.assertIn("Not qualified", results)
        self.assertIn("Unknown", results)  # unknown values are shown, not hidden


class TestMigration(unittest.TestCase):
    def test_upgrade_downgrade_and_reupgrade(self):
        d = os.path.join(os.path.dirname(__file__), f"_test_env_decision_mig_{uuid.uuid4().hex[:8]}")
        os.makedirs(d)
        self.addCleanup(shutil.rmtree, d, True)
        url = f"sqlite:///{d}/m.db"
        engine = create_engine(url)

        @event.listens_for(engine, "connect")
        def _fk(c, r):
            c.execute("PRAGMA foreign_keys=ON")
        with _UseDatabase(url):
            command.upgrade(_config(), "e3f7a9b2c4d6")
            command.upgrade(_config(), "head")
            tables = ("decision_attributes", "candidate_attribute_values", "decision_constraints", "decision_objectives", "decision_analyses")
            with engine.connect() as conn:
                names = {r[0] for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))}
                self.assertTrue(set(tables) <= names)
            command.downgrade(_config(), "e3f7a9b2c4d6")
            with engine.connect() as conn:
                names = {r[0] for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))}
                self.assertFalse(set(tables) & names)
                self.assertIn("model_versions", names)  # the earlier level is untouched
            command.upgrade(_config(), "head")
        engine.dispose()


if __name__ == "__main__":
    unittest.main()
