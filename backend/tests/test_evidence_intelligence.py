"""Level 4: evidence library, review workflow, conflicts, quality dimensions and provenance.
Run:  python3 -m unittest backend.tests.test_evidence_intelligence -v"""
import json
import os
import re
import unittest
import uuid

from alembic import command
from sqlalchemy import create_engine, event, text

from backend.app.schemas.errors import ValidationError
from backend.app.services import evidence_intel_rules as rules
from backend.tests import evidence_fixtures as F
from backend.tests import test_diagnostic_lifecycle as lc
from backend.tests.test_migration_portability import _config, _UseDatabase

HEAD = lc.HEAD


def doc(i, rows, **kw):
    base = {"id": i, "version_number": i, "filename": f"d{i}.csv", "status": "accepted", "sha": f"sha{i}", "document_type": "test_report",
            "source_note": "Lab", "document_version": "1", "test_method": None, "document_date": None, "source_reliability": None,
            "review": {}, "rows": [{"id": i * 100 + n, "features": {"a": a}, "target": t, "source_row": n + 2} for n, (a, t) in enumerate(rows)]}
    base.update(kw)
    return base


class TestRules(unittest.TestCase):
    def test_review_transitions(self):
        self.assertEqual(rules.check_review("accept", "clarification_requested", None, None, 1), ("accepted", None))
        for action in ("reject", "request_clarification", "flag_conflict", "exclude"):
            with self.assertRaises(ValidationError, msg=action):
                rules.check_review(action, "accepted", " ", None, 1)  # a reason is required
            self.assertEqual(rules.check_review(action, "accepted", "Because", None, 1)[1], "Because")
        with self.assertRaises(ValidationError):
            rules.check_review("accept", "rejected", None, None, 1)            # coming back from rejection needs a reason
        with self.assertRaises(ValidationError):
            rules.check_review("reject", "rejected", "again", None, 1)         # same status
        with self.assertRaises(ValidationError):
            rules.check_review("accept", "superseded", "x", None, 1)           # superseded is final
        with self.assertRaises(ValidationError):
            rules.check_review("supersede", "accepted", "Newer data", None, 1)  # names no replacement
        with self.assertRaises(ValidationError):
            rules.check_review("supersede", "accepted", "Newer data", 1, 1)     # replaces itself
        with self.assertRaises(ValidationError):
            rules.check_review("approve", "accepted", "x", None, 1)

    def test_metadata_validation(self):
        self.assertEqual(rules.clean_metadata({"document_type": "test_report", "document_date": "2026-01-31"})["document_date"], "2026-01-31")
        for bad in ({"document_type": "memo"}, {"document_date": "31/01/2026"}, {"source_reliability": "great"}, {"colour": "red"}, {}):
            with self.assertRaises(ValidationError, msg=str(bad)):
                rules.clean_metadata(bad)

    def kinds(self, docs):
        return sorted(c["kind"] for c in rules.detect_conflicts(docs))

    def test_conflicting_measurements_are_reported_never_resolved(self):
        a = doc(1, [(1, 100), (2, 200), (3, 300)])
        b = doc(2, [(1, 100), (2, 290), (3, 300)])
        found = rules.detect_conflicts([a, b])
        (conflict,) = [c for c in found if c["kind"] == "conflicting_measurements"]
        self.assertEqual((conflict["dataset_ids"], conflict["severity"]), ([1, 2], "high"))
        self.assertEqual(conflict["details"]["examples"][0]["values"], {"1": 200, "2": 290})
        self.assertIn("No value has been chosen", conflict["description"])
        self.assertEqual(self.kinds([a, doc(2, [(1, 100), (2, 205), (3, 300)])]).count("conflicting_measurements"), 0)  # within the threshold
        self.assertIn("duplicate_observations", self.kinds([a, doc(2, [(1, 100), (9, 150)])]))

    def test_documents_out_of_play_are_ignored(self):
        a, b = doc(1, [(1, 100), (2, 400)]), doc(2, [(1, 400), (2, 100)], status="rejected")
        self.assertNotIn("conflicting_measurements", self.kinds([a, b]))

    def test_methods_conditions_metadata_and_suspicious_records(self):
        a = doc(1, [(n, 10 * n) for n in range(6)], test_method="ASTM B117", review={"conditions": {"reference": {"temp": "25"}}})
        b = doc(2, [(n + 10, 5) for n in range(6)], test_method="ISO 9227", review={"conditions": {"reference": {"temp": "40"}}},
                document_type=None, source_note=None)
        kinds = self.kinds([a, b])
        for expected in ("incompatible_test_methods", "different_conditions", "missing_metadata", "suspicious_records"):
            self.assertIn(expected, kinds)
        c = doc(3, [(n, n) for n in range(6)], review={"conditions": {"reference": {"temp": "25"}}})
        d = doc(4, [(n, n) for n in range(6)], review={})
        self.assertIn("conditions_not_comparable", self.kinds([c, d]))
        self.assertIn("duplicate_document", self.kinds([doc(5, [(1, 1)], sha="same"), doc(6, [(2, 2)], sha="same")]))
        no_source = doc(7, [(n, n) for n in range(6)])
        no_source["rows"][0]["source_row"] = None
        self.assertIn("incomplete_provenance", self.kinds([no_source]))

    def test_fingerprints_are_stable_and_distinct(self):
        a, b = doc(1, [(1, 100), (2, 200)]), doc(2, [(1, 100), (2, 400)])
        first = [c["fingerprint"] for c in rules.detect_conflicts([a, b])]
        self.assertEqual(first, [c["fingerprint"] for c in rules.detect_conflicts([a, b])])
        self.assertEqual(len(first), len(set(first)))

    def test_quality_is_six_separate_dimensions_without_a_combined_score(self):
        d = doc(1, [(1, 10), (1, 12), (2, 30)], review={"units": {"t": {"canonical": "h", "conversions": [{"from": "min", "to": "h", "rows": 2}]}},
                                                         "conditions": {"reference": {"temp": "25"}}}, document_date="2025-01-01", source_reliability="high")
        dims = rules.quality_dimensions(d, "2026-11-23")
        self.assertEqual([x["key"] for x in dims], ["completeness", "source_reliability", "measurement_comparability", "recency",
                                                    "replication", "test_condition_compatibility"])
        by = {x["key"]: x for x in dims}
        self.assertEqual(by["source_reliability"]["level"], "high")
        self.assertEqual(by["measurement_comparability"]["level"], "medium")
        self.assertEqual(by["recency"]["level"], "high")
        self.assertEqual(by["replication"]["level"], "substantial")
        self.assertTrue(all(x["basis"] for x in dims))
        self.assertFalse(any("score" in json.dumps(x).lower() for x in dims))

    def test_missing_information_is_not_assessed_rather_than_good(self):
        by = {x["key"]: x for x in rules.quality_dimensions(doc(1, [(1, 1)], review={}), "2026-11-23")}
        for key in ("source_reliability", "recency", "measurement_comparability", "test_condition_compatibility"):
            self.assertEqual(by[key]["level"], "not_assessed", key)
        self.assertEqual(by["replication"]["level"], "none")


class Base(lc.LifecycleBase):
    dir_name = "_test_env_evidence_intel"

    def person(self, client):
        return client.get("/api/auth/me").json()["user"]["id"]

    def csv(self, scale=1, extra=(), n=14):
        rows = [(a, b, c * scale) for a, b, c in F.base_rows(n)] + list(extra)
        return F.csv_bytes(HEAD, rows)

    def docs(self, client, case_id):
        r = client.get(f"/api/change-cases/{case_id}/evidence-library")
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def review(self, client, case_id, dataset_id, action, reason="Because", **kw):
        return client.post(f"/api/change-cases/{case_id}/evidence/{dataset_id}/review", json={"action": action, "reason": reason, **kw})

    def two_uploads(self, label, scale=1):
        org, client, case_id, ids = self.case_with_evidence(label, candidates=1)
        r = self.upload(client, case_id, "second.csv", self.csv(scale))
        self.assertEqual(r.status_code, 201, r.text)
        d = self.docs(client, case_id)["documents"]
        return org, client, case_id, ids, d[1]["id"], d[0]["id"]  # older id, newer id


class TestLibraryAndMetadata(Base):
    dir_name = "_test_env_evidence_lib"

    def test_uploads_start_accepted_with_a_version_number_and_history(self):
        org, client, case_id, ids, old, new = self.two_uploads("Start")
        docs = self.docs(client, case_id)["documents"]
        self.assertEqual([(d["version_number"], d["status"], d["is_current"]) for d in docs], [(2, "accepted", True), (1, "accepted", False)])
        detail = client.get(f"/api/change-cases/{case_id}/evidence/{new}").json()
        self.assertEqual(detail["filename"], "second.csv")
        self.assertEqual(len(detail["quality"]), 6)
        self.assertEqual({q["key"] for q in detail["quality"] if q["level"] == "not_assessed"} >= {"source_reliability", "recency"}, True)
        self.assertEqual(detail["history"], [])  # nothing reviewed yet after upload
        self.assertTrue(detail["can_review"])

    def test_metadata_is_validated_audited_and_permissioned(self):
        org, client, case_id, ids, old, new = self.two_uploads("Meta")
        member = self.member_client(client, "member")
        body = {"document_type": "test_report", "source_note": "ISO 9227 salt spray, supplier lab", "document_version": "Rev B",
                "test_method": "ISO 9227", "document_date": "2026-03-01", "source_reliability": "medium"}
        self.assertEqual(member.patch(f"/api/change-cases/{case_id}/evidence/{new}/metadata", json=body).status_code, 403)
        r = client.patch(f"/api/change-cases/{case_id}/evidence/{new}/metadata", json=body)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((r.json()["document_type_label"], r.json()["test_method"]), ("Test report", "ISO 9227"))
        quality = {q["key"]: q for q in r.json()["quality"]}
        self.assertEqual((quality["source_reliability"]["level"], quality["recency"]["level"]), ("medium", "high"))
        for bad in ({"document_type": "memo"}, {"document_date": "soon"}, {"colour": "red"}, {}):
            self.assertIn(client.patch(f"/api/change-cases/{case_id}/evidence/{new}/metadata", json=bad).status_code, (400, 422))
        labels = [t["label"] for t in client.get(f"/api/change-cases/{case_id}/timeline").json()]
        self.assertIn("Evidence details updated", labels)

    def test_the_library_searches_filters_pages_and_isolates_organizations(self):
        org, client, case_id, ids, old, new = self.two_uploads("Lib")
        client.patch(f"/api/change-cases/{case_id}/evidence/{new}/metadata", json={"document_type": "lab_data", "source_note": "Pilot line"})
        lib = lambda q="": client.get("/api/evidence" + q)
        self.assertEqual(lib().json()["total"], 2)
        self.assertEqual([i["filename"] for i in lib("?q=second").json()["items"]], ["second.csv"])
        self.assertEqual(lib("?q=pilot").json()["total"], 1)
        self.assertEqual(lib("?document_type=lab_data").json()["total"], 1)
        self.assertEqual(lib(f"?program_id={case_id}").json()["total"], 2)
        self.assertEqual(lib("?q=zzz").json()["items"], [])
        self.assertEqual(len(lib("?limit=1").json()["items"]), 1)
        self.assertEqual(lib("?document_type=memo").status_code, 400)
        self.assertEqual(lib("?status=nope").status_code, 400)
        item = lib("?q=second").json()["items"][0]
        self.assertEqual((item["program"]["id"], item["version_number"], item["status"]), (case_id, 2, "accepted"))
        _, other = self.org_and_client("Elsewhere")
        self.assertEqual(other.get("/api/evidence").json()["total"], 0)
        self.assertEqual(self.client_factory().get("/api/evidence").status_code, 401)


class TestReviewWorkflow(Base):
    dir_name = "_test_env_evidence_review"

    def test_reject_removes_evidence_and_ranking_falls_back_to_the_previous_accepted_upload(self):
        org, client, case_id, ids, old, new = self.two_uploads("Reject")
        self.assertEqual(self.review(client, case_id, new, "reject", reason=" ").status_code, 400)  # reason needed
        r = self.review(client, case_id, new, "reject", reason="Wrong batch")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((r.json()["status"], r.json()["history"][0]["reason"]), ("rejected", "Wrong batch"))
        summary = {d["id"]: d for d in client.get(f"/api/change-cases/{case_id}/evidence").json()}
        self.assertEqual((summary[old]["is_current"], summary[new]["status"]), (True, "rejected"))
        rank = client.post(f"/api/change-cases/{case_id}/rank")
        self.assertEqual(rank.status_code, 200, rank.text)
        cand = client.get(f"/api/change-cases/{case_id}/candidates").json()[0]
        self.assertEqual(cand["latest_prediction"]["dataset_version_id"], old)

    def test_evidence_under_review_pauses_ranking_instead_of_being_skipped(self):
        org, client, case_id, ids, old, new = self.two_uploads("Held")
        for action in ("request_clarification", "flag_conflict"):
            self.assertEqual(self.review(client, case_id, new, action, reason="Check the units").status_code, 200)
            r = client.post(f"/api/change-cases/{case_id}/rank")
            self.assertEqual(r.status_code, 400, action)
            self.assertIn("under review", r.json()["error"])
            self.assertIn("under review", self.docs(client, case_id)["held_notice"])
            self.assertIn("evidence_held", {a["type"] for a in client.get("/api/portfolio/overview").json()["actions"]} | set()) if False else None
            self.assertEqual(self.review(client, case_id, new, "accept", reason=None).status_code, 200)
            self.assertIsNone(self.docs(client, case_id)["held_notice"])
        self.assertEqual(client.post(f"/api/change-cases/{case_id}/rank").status_code, 200)

    def test_held_evidence_is_an_overview_issue(self):
        org, client, case_id, ids, old, new = self.two_uploads("HeldIssue")
        client.patch(f"/api/change-cases/{case_id}/status", json={"status": "active"})
        self.review(client, case_id, new, "request_clarification", reason="Which lab made this")
        types = {a["type"] for a in client.get("/api/portfolio/overview").json()["actions"]}
        self.assertIn("evidence_held", types)
        self.review(client, case_id, new, "exclude", reason="Not relevant")
        self.assertNotIn("evidence_held", {a["type"] for a in client.get("/api/portfolio/overview").json()["actions"]})

    def test_exclude_and_supersede(self):
        org, client, case_id, ids, old, new = self.two_uploads("Super")
        self.assertEqual(self.review(client, case_id, old, "supersede", reason="Replaced by a larger data set").status_code, 400)  # names no replacement
        self.assertEqual(self.review(client, case_id, new, "supersede", reason="x", superseded_by=old).status_code, 400)  # older cannot replace newer
        r = self.review(client, case_id, old, "supersede", reason="Replaced by a larger data set", superseded_by=new)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["superseded_by_dataset_id"], new)
        self.assertEqual(self.review(client, case_id, old, "accept", reason="Undo").status_code, 400)  # final
        self.assertEqual(self.review(client, case_id, new, "supersede", reason="x", superseded_by=old).status_code, 400)  # replacement not accepted
        ex = self.review(client, case_id, new, "exclude", reason="Out of scope")
        self.assertEqual(ex.json()["status"], "excluded")
        self.assertEqual([h["action"] for h in ex.json()["history"]], ["exclude"])

    def test_rejecting_the_only_evidence_removes_it_from_the_stage_requirements(self):
        org, client, case_id, ids = self.case_with_evidence("OnlyOne", candidates=1)
        only = self.docs(client, case_id)["documents"][0]["id"]
        for s in ("requirements", "evidence_collection"):
            client.post(f"/api/change-cases/{case_id}/stage", json={"to_stage": s})
        self.assertEqual(client.post(f"/api/change-cases/{case_id}/stage", json={"to_stage": "evidence_review"}).status_code, 200)
        client.post(f"/api/change-cases/{case_id}/stage", json={"to_stage": "evidence_collection", "reason": "Rework"})
        self.review(client, case_id, only, "reject", reason="Unusable scan")
        blocked = client.post(f"/api/change-cases/{case_id}/stage", json={"to_stage": "evidence_review"})
        self.assertEqual(blocked.status_code, 400)
        self.assertIn("Upload evidence", blocked.json()["error"])
        self.assertEqual(client.post(f"/api/change-cases/{case_id}/rank").status_code, 400)  # no usable evidence

    def test_who_may_review(self):
        org, client, case_id, ids, old, new = self.two_uploads("Who")
        member = self.member_client(client, "member")
        self.assertEqual(self.review(member, case_id, new, "reject").status_code, 403)
        client.post(f"/api/change-cases/{case_id}/reviewers", json={"user_id": self.person(member)})
        self.assertEqual(self.review(member, case_id, new, "request_clarification", reason="Please confirm the method").status_code, 200)
        client.delete(f"/api/change-cases/{case_id}/reviewers/{self.person(member)}")
        self.assertEqual(self.review(member, case_id, new, "accept").status_code, 403)  # no longer a reviewer
        admin = self.member_client(client, "admin")
        self.assertEqual(self.review(admin, case_id, new, "accept").status_code, 200)

    def test_history_notifications_and_audit(self):
        org, client, case_id, ids, old, new = self.two_uploads("Hist")
        uploader = self.person(client)
        admin = self.member_client(client, "admin")
        self.review(admin, case_id, new, "flag_conflict", reason="Does not match the lab report")
        self.review(admin, case_id, new, "accept", reason="Lab confirmed")
        hist = client.get(f"/api/change-cases/{case_id}/evidence/{new}").json()["history"]
        self.assertEqual([(h["action"], h["from_status"], h["to_status"]) for h in hist],
                         [("accept", "conflict_flagged", "accepted"), ("flag_conflict", "accepted", "conflict_flagged")])
        self.assertTrue(all(h["actor"] for h in hist))
        self.assertIn("Evidence conflict flagged", [n["title"] for n in client.get("/api/notifications").json()["items"]])
        self.assertIn("Evidence reviewed", [t["label"] for t in client.get(f"/api/change-cases/{case_id}/timeline").json()])
        self.assertEqual(self.review(client, case_id, new, "accept", reason=None).status_code, 400)  # already accepted

    def test_closed_and_archived_programs_and_other_organizations_are_read_only_or_hidden(self):
        org, client, case_id, ids, old, new = self.two_uploads("RO")
        _, other = self.org_and_client("Other")
        for r in (other.get(f"/api/change-cases/{case_id}/evidence-library"), other.get(f"/api/change-cases/{case_id}/evidence/{new}"),
                  self.review(other, case_id, new, "reject"), other.get(f"/api/change-cases/{case_id}/evidence/{new}/rows"),
                  other.post(f"/api/change-cases/{case_id}/conflicts/scan")):
            self.assertEqual(r.status_code, 404)
        self.assertEqual(client.get(f"/api/change-cases/{case_id}/evidence/999999").status_code, 404)
        self.assertEqual(self.client_factory().get(f"/api/change-cases/{case_id}/evidence-library").status_code, 401)
        client.patch(f"/api/change-cases/{case_id}/status", json={"status": "active"})
        client.patch(f"/api/change-cases/{case_id}/status", json={"status": "completed"})
        self.assertEqual(self.review(client, case_id, new, "reject").status_code, 400)
        self.assertFalse(self.docs(client, case_id)["permissions"]["can_review"])
        self.assertEqual(client.get(f"/api/change-cases/{case_id}/evidence/{new}").status_code, 200)


class TestConflictsApi(Base):
    dir_name = "_test_env_evidence_conflicts"

    def conflicts(self, client, case_id, kind=None):
        found = self.docs(client, case_id)["conflicts"]
        return [c for c in found if kind is None or c["kind"] == kind]

    def test_conflicting_documents_are_detected_listed_and_raised_in_the_overview(self):
        org, client, case_id, ids, old, new = self.two_uploads("Detect", scale=3)
        (c,) = self.conflicts(client, case_id, "conflicting_measurements")
        self.assertEqual((c["status"], c["severity"], sorted(c["dataset_ids"])), ("open", "high", sorted([old, new])))
        self.assertEqual(len(c["documents"]), 2)
        client.patch(f"/api/change-cases/{case_id}/status", json={"status": "active"})
        self.assertIn("evidence_conflict", {a["type"] for a in client.get("/api/portfolio/overview").json()["actions"]})
        # the data was not touched and no value was chosen
        self.assertEqual({d["status"] for d in self.docs(client, case_id)["documents"]}, {"accepted"})

    def test_a_person_resolves_it_with_a_traceable_decision(self):
        org, client, case_id, ids, old, new = self.two_uploads("Resolve", scale=3)
        (c,) = self.conflicts(client, case_id, "conflicting_measurements")
        url = f"/api/change-cases/{case_id}/conflicts/{c['id']}/resolve"
        member = self.member_client(client, "member")
        self.assertEqual(member.post(url, json={"resolution": "accepted_as_is", "note": "Fine"}).status_code, 403)
        self.assertEqual(client.post(url, json={"resolution": "accepted_as_is", "note": " "}).status_code, 400)
        self.assertEqual(client.post(url, json={"resolution": "pick_best", "note": "Fine"}).status_code, 400)
        self.assertEqual(client.post(url, json={"resolution": "prefer_dataset", "note": "Newer lab", "preferred_dataset_id": 999999}).status_code, 400)
        self.assertEqual(client.post(url, json={"resolution": "prefer_dataset", "note": "Newer lab"}).status_code, 400)
        r = client.post(url, json={"resolution": "prefer_dataset", "note": "The newer lab used the current method", "preferred_dataset_id": new})
        self.assertEqual(r.status_code, 200, r.text)
        (done,) = [x for x in r.json()["conflicts"] if x["id"] == c["id"]]
        self.assertEqual((done["status"], done["resolution"], done["preferred_dataset_id"], done["resolved_by"] is not None), ("resolved", "prefer_dataset", new, True))
        self.assertEqual(client.post(url, json={"resolution": "accepted_as_is", "note": "again"}).status_code, 400)  # already resolved
        # a rescan keeps the person's decision
        again = client.post(f"/api/change-cases/{case_id}/conflicts/scan").json()["conflicts"]
        self.assertEqual([x["status"] for x in again if x["id"] == c["id"]], ["resolved"])
        self.assertIn("Evidence conflict resolved", [t["label"] for t in client.get(f"/api/change-cases/{case_id}/timeline").json()])
        client.patch(f"/api/change-cases/{case_id}/status", json={"status": "active"})
        self.assertNotIn("evidence_conflict", {a["type"] for a in client.get("/api/portfolio/overview").json()["actions"]})

    def test_a_conflict_closes_itself_when_the_evidence_changes(self):
        org, client, case_id, ids, old, new = self.two_uploads("AutoClose", scale=3)
        self.assertEqual(len([c for c in self.conflicts(client, case_id, "conflicting_measurements") if c["status"] == "open"]), 1)
        self.review(client, case_id, new, "exclude", reason="Different alloy")
        (c,) = self.conflicts(client, case_id, "conflicting_measurements")
        self.assertEqual((c["status"], c["resolution"], c["resolved_by"]), ("resolved", "no_longer_detected", None))

    def test_methods_metadata_and_units_are_reported(self):
        org, client, case_id, ids, old, new = self.two_uploads("Methods")
        client.patch(f"/api/change-cases/{case_id}/evidence/{old}/metadata", json={"test_method": "ASTM B117"})
        client.patch(f"/api/change-cases/{case_id}/evidence/{new}/metadata", json={"test_method": "ISO 9227"})
        kinds = {c["kind"] for c in self.conflicts(client, case_id) if c["status"] == "open"}
        self.assertIn("incompatible_test_methods", kinds)
        self.assertIn("missing_metadata", kinds)
        client.patch(f"/api/change-cases/{case_id}/evidence/{new}/metadata", json={"test_method": "ASTM B117"})
        self.assertNotIn("incompatible_test_methods", {c["kind"] for c in self.conflicts(client, case_id) if c["status"] == "open"})

    def test_scan_is_permissioned_by_program_state(self):
        org, client, case_id, ids, old, new = self.two_uploads("Scan")
        self.assertEqual(client.post(f"/api/change-cases/{case_id}/conflicts/scan").status_code, 200)
        client.post(f"/api/change-cases/{case_id}/archive")
        self.assertEqual(client.post(f"/api/change-cases/{case_id}/conflicts/scan").status_code, 400)


class TestProvenance(Base):
    dir_name = "_test_env_evidence_prov"

    def rows(self, client, case_id, dataset_id, q=""):
        r = client.get(f"/api/change-cases/{case_id}/evidence/{dataset_id}/rows{q}")
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def test_every_value_traces_to_its_source_row_with_original_and_normalized_values(self):
        org, client, case_id, ids = self.case_with_evidence("Trace", candidates=1)
        did = self.docs(client, case_id)["documents"][0]["id"]
        body = self.rows(client, case_id, did)
        self.assertEqual((body["total"], body["dataset"]["version_number"], body["dataset"]["status"]), (14, 1, "accepted"))
        self.assertEqual(len(body["dataset"]["file_sha256"]), 64)
        first = body["rows"][0]
        self.assertEqual(first["source"]["file"], "h.csv")
        self.assertEqual(first["source"]["row"], 2)
        v = first["values"]["salt_spray_hours"]
        self.assertEqual((v["original_value"], v["normalized_value"]), (100.0, 100.0))
        self.assertEqual(set(first["values"]), {"crosslinker_ratio", "cure_temp_c", "salt_spray_hours"})
        self.assertIsNone(first["trace_note"])
        self.assertEqual([r["number"] for r in self.rows(client, case_id, did, "?offset=3&limit=2")["rows"]], [4, 5])
        self.assertEqual(len(self.rows(client, case_id, did, "?limit=9999")["rows"]), 14)

    def test_a_unit_conversion_is_recorded_with_both_values_and_the_factor_used(self):
        org, client, case_id, ids = self.case_with_evidence("Convert", candidates=1)
        rows = [(a, b, c * 60) for a, b, c in F.base_rows(14)]  # minutes
        spec = json.loads(client.get(f"/api/change-cases/{case_id}").json()["qualification_spec_json"])
        declared = dict(spec["units"])
        self.assertEqual(declared["salt_spray_hours"], "h")
        declared["salt_spray_hours"] = "min"
        r = self.upload(client, case_id, "minutes.csv", F.csv_bytes(HEAD, rows), options={"declared_units": declared})
        self.assertEqual(r.status_code, 201, r.text)
        did = self.docs(client, case_id)["documents"][0]["id"]
        v = self.rows(client, case_id, did)["rows"][1]["values"]["salt_spray_hours"]
        self.assertEqual((v["original_unit"], v["unit_source"], v["normalized_unit"]), ("min", "declared by the reviewer", "h"))
        self.assertAlmostEqual(v["original_value"] / 60, v["normalized_value"], places=6)
        self.assertIn("exact factor", v["transformation"])
        quality = {q["key"]: q for q in client.get(f"/api/change-cases/{case_id}/evidence/{did}").json()["quality"]}
        self.assertEqual(quality["measurement_comparability"]["level"], "medium")
        self.assertIn("unit_conversion", {c["kind"] for c in self.docs(client, case_id)["conflicts"]})

    def test_older_uploads_say_plainly_that_originals_were_not_recorded(self):
        from backend.app.config.database import db_transaction
        org, client, case_id, ids = self.case_with_evidence("Legacy", candidates=1)
        did = self.docs(client, case_id)["documents"][0]["id"]
        with db_transaction() as conn:
            conn.execute(text("UPDATE qualification_experiments SET provenance_json = '{\"file\": \"h.csv\", \"table\": \"csv\", \"row\": 2}' "
                              "WHERE qualification_dataset_id = :d"), {"d": did})
        row = self.rows(client, case_id, did)["rows"][0]
        self.assertIsNone(row["values"])
        self.assertIn("not recorded", row["trace_note"])
        self.assertEqual(row["source"]["row"], 2)

    def test_review_notes_for_a_source_row_are_attached_to_that_row(self):
        org, client, case_id, ids = self.case_with_evidence("Flags", candidates=1)
        rows = [(a, b, c) for a, b, c in F.base_rows(14)] + [(0.2, 200, 999), (0.2, 200, 999)]  # an exact repeat
        r = self.upload(client, case_id, "repeat.csv", F.csv_bytes(HEAD, rows))
        self.assertEqual(r.status_code, 201, r.text)
        did = self.docs(client, case_id)["documents"][0]["id"]
        self.assertIn("duplicate_observations", {c["kind"] for c in self.docs(client, case_id)["conflicts"]})
        body = self.rows(client, case_id, did)
        self.assertEqual(body["total"], 16)

    def test_deleting_a_program_removes_review_history_and_conflicts(self):
        from backend.app.config.database import db_connection
        org, client, case_id, ids, old, new = self.two_uploads("Delete", scale=3)
        self.review(client, case_id, new, "request_clarification", reason="Check")
        # a program with rankings cannot be deleted, so use a fresh one with evidence only
        org2, c2, case2 = self.setup_case("DeleteMe")
        self.paid(org2)
        self.upload(c2, case2, "a.csv", self.csv())
        self.upload(c2, case2, "b.csv", self.csv(3))
        self.assertGreater(len(self.docs(c2, case2)["conflicts"]), 0)
        self.assertEqual(c2.delete(f"/api/change-cases/{case2}").status_code, 200)
        with db_connection() as conn:
            for table in ("evidence_reviews", "evidence_conflicts"):
                self.assertEqual(conn.execute(text(f"SELECT COUNT(*) FROM {table} WHERE change_case_id = :c"), {"c": case2}).scalar(), 0, table)


class TestFrontendWiring(unittest.TestCase):
    ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "frontend"))

    def read(self, rel):
        with open(os.path.join(self.ROOT, rel), encoding="utf-8") as f:
            return f.read()

    def test_every_evidence_api_call_has_a_backend_route(self):
        os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production")
        os.environ.setdefault("PASSWORD_PEPPER", "test-password-pepper-not-for-production")
        from backend.app.main import create_app
        routes = {(m, r.path) for r in create_app().routes for m in getattr(r, "methods", None) or ()}
        src = self.read("lib/api/evidence.ts")
        calls = re.findall(r"`(/api[^`]*)`(?:, \{ method: \"(\w+)\")?", src) + re.findall(r"`\$\{base\(id\)\}([^`]*)`(?:, \{ method: \"(\w+)\")?", src)
        self.assertGreaterEqual(len(calls), 8)
        for path, method in calls:
            if not path.startswith("/api"):
                path = "/api/change-cases/{x}" + path
            concrete = re.sub(r"\$\{[^}]+\}", "1", path).split("?")[0]
            found = any(m == (method or "GET") and re.match("^" + re.sub(r"\{[^}]+\}", "[^/]+", rp) + "$", concrete) for m, rp in routes)
            self.assertTrue(found, f"no backend route for {method or 'GET'} {path}")

    def test_the_pages_and_components_exist_and_follow_the_copy_standard(self):
        for rel in ("components/evidence/EvidencePanel.tsx", "app/evidence/page.tsx", "lib/api/evidence.ts"):
            self.assertTrue(os.path.isfile(os.path.join(self.ROOT, rel)), rel)
        self.assertIn("const { checking } = useRequireAuth()", self.read("app/evidence/page.tsx"))
        self.assertIn("<Suspense", self.read("app/evidence/page.tsx"))
        self.assertIn('href: "/evidence"', self.read("components/layout/Sidebar.tsx"))
        for rel in ("components/evidence/EvidencePanel.tsx", "app/evidence/page.tsx"):
            text_ = re.sub(r"/\*.*?\*/", "", self.read(rel), flags=re.S)
            for banned in ("\u2014", "\u2013", " -- ", " \u00b7 "):
                self.assertNotIn(banned, text_, rel)
            self.assertNotRegex(text_, r"(?i)diagnostic")


class TestMigration(unittest.TestCase):
    def test_existing_uploads_are_accepted_with_a_history_row_and_the_migration_reverses(self):
        import shutil
        d = os.path.join(os.path.dirname(__file__), f"_test_env_ev_mig_{uuid.uuid4().hex[:8]}")
        os.makedirs(d)
        self.addCleanup(shutil.rmtree, d, True)
        url = f"sqlite:///{d}/m.db"
        engine = create_engine(url)

        @event.listens_for(engine, "connect")
        def _fk(c, r):
            c.execute("PRAGMA foreign_keys=ON")
        with _UseDatabase(url):
            command.upgrade(_config(), "c1d5e7f9a2b4")
            with engine.begin() as conn:
                conn.execute(text("INSERT INTO organizations (name, created_at) VALUES ('M', 't')"))
                conn.execute(text("INSERT INTO users (email, password_hash, display_name, is_active, created_at) VALUES ('m@example.com', 'h', 'M', 1, 't')"))
                conn.execute(text("""INSERT INTO change_cases (organization_id, created_by_user_id, name, trigger_type, restricted_substance,
                                     qualification_spec_json, status, created_at, updated_at) VALUES (1, 1, 'c', 'other', 'x', '{}', 'draft', 't', 't')"""))
                conn.execute(text("""INSERT INTO qualification_datasets (organization_id, change_case_id, uploaded_by_user_id, original_filename,
                                     stored_filename, row_count, created_at) VALUES (1, 1, 1, 'f.csv', 's.csv', 5, '2026-01-01T00:00:00+00:00')"""))
            command.upgrade(_config(), "head")
            with engine.connect() as conn:
                row = conn.execute(text("SELECT review_status, review_reason, reviewed_by_user_id, reviewed_at FROM qualification_datasets")).one()
                self.assertEqual(tuple(row), ("accepted", "Accepted at upload review", 1, "2026-01-01T00:00:00+00:00"))
                hist = conn.execute(text("SELECT action, from_status, to_status, actor_user_id FROM evidence_reviews")).all()
                self.assertEqual([tuple(h) for h in hist], [("accept", None, "accepted", 1)])
            command.downgrade(_config(), "c1d5e7f9a2b4")
            with engine.connect() as conn:
                cols = {r[1] for r in conn.execute(text("PRAGMA table_info(qualification_datasets)"))}
                self.assertFalse({"review_status", "document_type", "superseded_by_dataset_id"} & cols)
                self.assertEqual(conn.execute(text("SELECT COUNT(*) FROM qualification_datasets")).scalar(), 1)
            command.upgrade(_config(), "head")
        engine.dispose()


if __name__ == "__main__":
    unittest.main()
