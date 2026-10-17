"""
Level 1 -- uploads are size-limited BEFORE the application reads them, and ingestion is
all-or-nothing (no dataset record without its rows, no orphaned stored file).

Run:  python3 -m unittest backend.tests.test_upload_and_ingestion_safety -v
"""
import asyncio
import os
import unittest
from unittest import mock

os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production")
os.environ.setdefault("PASSWORD_PEPPER", "test-password-pepper-not-for-production")

from backend.app.middleware import upload_limits  # noqa: E402
from backend.app.schemas.errors import ValidationError  # noqa: E402
from backend.tests import evidence_fixtures as F  # noqa: E402
from backend.tests._api_base import SPEC, IsolatedApiTestCase  # noqa: E402

HEAD = ["crosslinker_ratio", "cure_temp_c", "salt_spray_hours"]


def good_csv(n=12):
    return F.csv_bytes(HEAD, F.base_rows(n))


class _FakeUpload:
    """Records how many bytes a handler asked for, like a spooled UploadFile."""
    def __init__(self, data):
        self._data, self.requested = data, []

    async def read(self, size=-1):
        self.requested.append(size)
        return self._data if size < 0 else self._data[:size]


class TestReadUploadLimited(unittest.TestCase):
    def test_reads_at_most_limit_plus_one_and_refuses_oversize(self):
        up = _FakeUpload(b"x" * 5000)
        with self.assertRaises(ValidationError) as ctx:
            asyncio.run(upload_limits.read_upload_limited(up, max_bytes=1000))
        self.assertIn("too large", ctx.exception.message)
        self.assertEqual(up.requested, [1001])  # never an unbounded read()

    def test_file_exactly_at_the_limit_is_accepted(self):
        up = _FakeUpload(b"x" * 1000)
        self.assertEqual(len(asyncio.run(upload_limits.read_upload_limited(up, max_bytes=1000))), 1000)

    def test_defaults_to_the_configured_limit(self):
        from backend.app.config.settings import settings
        up = _FakeUpload(b"x")
        asyncio.run(upload_limits.read_upload_limited(up))
        self.assertEqual(up.requested, [settings.MAX_UPLOAD_BYTES + 1])

    def test_guarded_paths(self):
        guarded = ["/api/change-cases/7/dataset", "/api/change-cases/7/dataset/preview",
                   "/api/change-cases/7/evidence/extract", "/api/projects/3/dataset"]
        for path in guarded:
            self.assertTrue(any(p.match(path) for p in upload_limits.UPLOAD_PATHS), path)
        for path in ("/api/change-cases", "/api/change-cases/7/candidates", "/api/auth/login",
                     "/api/change-cases/x/dataset", "/api/change-cases/7/dataset/other"):
            self.assertFalse(any(p.match(path) for p in upload_limits.UPLOAD_PATHS), path)


class UploadBase(IsolatedApiTestCase):
    dir_name = "_test_env_upload_safety"
    LIMIT = 5000

    def setUp(self):
        from backend.app.config.settings import settings
        self._old_limit = settings.MAX_UPLOAD_BYTES
        settings.MAX_UPLOAD_BYTES = self.LIMIT

    def tearDown(self):
        from backend.app.config.settings import settings
        settings.MAX_UPLOAD_BYTES = self._old_limit

    def case(self, label):
        org_id, client, case_id = self.setup_case(label, SPEC)
        return org_id, client, case_id

    def big(self):
        return b"a,b,c\n" + b"1,2,3\n" * ((self.LIMIT + upload_limits.MULTIPART_OVERHEAD_BYTES) // 6 + 100)

    def uploaded_files(self, org_id):
        from backend.app.services import qualification_dataset_service as svc
        folder = os.path.join(svc.UPLOAD_ROOT, str(org_id))
        return sorted(os.listdir(folder)) if os.path.isdir(folder) else []


class TestOversizedUploads(UploadBase):
    def test_oversized_dataset_upload_is_refused_with_413_and_nothing_is_stored(self):
        org_id, client, case_id = self.case("Big1")
        files_before = self.uploaded_files(org_id)
        r = client.post(f"/api/change-cases/{case_id}/dataset",
                        files={"file": ("big.csv", self.big(), "text/csv")})
        self.assertEqual(r.status_code, 413, r.text[:200])
        self.assertIn("too large", r.json()["error"])
        self.assertEqual(self.datasets(org_id, case_id), [])
        self.assertEqual(self.uploaded_files(org_id), files_before)

    def test_every_upload_endpoint_refuses_oversize(self):
        org_id, client, case_id = self.case("Big2")
        for route in ("dataset", "dataset/preview", "evidence/extract"):
            r = client.post(f"/api/change-cases/{case_id}/{route}", files={"file": ("big.csv", self.big(), "text/csv")})
            self.assertEqual(r.status_code, 413, (route, r.text[:200]))

    def test_legacy_project_upload_refuses_oversize(self):
        org_id, client = self.org_and_client("Big3")
        proj = client.post("/api/projects", json={
            "name": "p", "target_metric": "y", "direction": "maximize", "feature_columns": ["a", "b"]})
        self.assertEqual(proj.status_code, 201, proj.text)
        r = client.post(f"/api/projects/{proj.json()['id']}/dataset", files={"file": ("big.csv", self.big(), "text/csv")})
        self.assertEqual(r.status_code, 413, r.text[:200])

    def test_body_without_a_declared_length_is_cut_off_while_streaming(self):
        org_id, client, case_id = self.case("Big4")
        boundary = "XBOUNDARY"
        payload = self.big()

        def body():
            yield (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"big.csv\"\r\n"
                   "Content-Type: text/csv\r\n\r\n").encode()
            for i in range(0, len(payload), 4096):
                yield payload[i:i + 4096]
            yield f"\r\n--{boundary}--\r\n".encode()

        r = client.post(f"/api/change-cases/{case_id}/dataset", content=body(),
                        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        self.assertEqual(r.status_code, 413, r.text[:200])
        self.assertEqual(self.datasets(org_id, case_id), [])

    def test_a_file_under_the_limit_still_uploads(self):
        org_id, client, case_id = self.case("Small1")
        r = self.upload(client, case_id, "ok.csv", good_csv(12))
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(len(self.datasets(org_id, case_id)), 1)

    def test_other_routes_are_not_affected_by_the_upload_guard(self):
        _, client = self.org_and_client("Small2")
        big_name = "n" * 6000  # a large JSON body on a non-upload route is handled normally (here: rejected by validation)
        r = client.post("/api/change-cases", json={"name": big_name, "trigger_type": "other", "qualification_spec": {}})
        self.assertNotEqual(r.status_code, 413)

    def test_unauthenticated_oversize_is_also_refused_early(self):
        anon = self.client_factory()
        r = anon.post("/api/change-cases/1/dataset", files={"file": ("big.csv", self.big(), "text/csv")})
        self.assertEqual(r.status_code, 413)


class TestAtomicIngestion(UploadBase):
    LIMIT = 10 * 1024 * 1024

    def test_repository_stores_dataset_and_rows_together_or_not_at_all(self):
        from sqlalchemy.exc import IntegrityError
        from backend.app.repositories import qualification_dataset_repo as repo
        org_id, client, case_id = self.case("Atomic1")
        rows = [{"features": {"crosslinker_ratio": 0.1, "cure_temp_c": 150}, "target_value": 100.0},
                {"features": {"crosslinker_ratio": 0.2, "cure_temp_c": 160}, "target_value": None}]  # NOT NULL violation
        with self.assertRaises(IntegrityError):
            repo.create_dataset_with_experiments(org_id, case_id, 1, "f.csv", "stored.csv", rows)
        self.assertEqual(self.datasets(org_id, case_id), [])  # no half-saved dataset

        good = [{"features": {"crosslinker_ratio": 0.1, "cure_temp_c": 150}, "target_value": 100.0},
                {"features": {"crosslinker_ratio": 0.2, "cure_temp_c": 160}, "target_value": 200.0}]
        dataset_id = repo.create_dataset_with_experiments(org_id, case_id, 1, "f.csv", "stored.csv", good)
        self.assertEqual(len(self.experiments(org_id, dataset_id)), 2)
        self.assertEqual(self.datasets(org_id, case_id)[0]["row_count"], 2)

    def test_failed_save_leaves_no_dataset_and_removes_the_stored_file(self):
        from backend.app.repositories import qualification_dataset_repo as repo
        org_id, client, case_id = self.case("Atomic2")
        files_before = self.uploaded_files(org_id)
        with mock.patch.object(repo, "create_dataset_with_experiments", side_effect=RuntimeError("disk full")):
            with self.assertRaises(RuntimeError):
                client.post(f"/api/change-cases/{case_id}/dataset",
                            files={"file": ("ok.csv", good_csv(12), "text/csv")})
        self.assertEqual(self.datasets(org_id, case_id), [])
        self.assertEqual(self.uploaded_files(org_id), files_before)  # the file written first was cleaned up

    def test_a_failed_upload_does_not_replace_the_latest_dataset(self):
        from backend.app.repositories import qualification_dataset_repo as repo
        org_id, client, case_id = self.case("Atomic3")
        self.assertEqual(self.upload(client, case_id, "first.csv", good_csv(12)).status_code, 201)
        first = self.datasets(org_id, case_id)[0]
        with mock.patch.object(repo, "create_dataset_with_experiments", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                client.post(f"/api/change-cases/{case_id}/dataset", files={"file": ("second.csv", good_csv(14), "text/csv")})
        latest = repo.get_latest_dataset_for_change_case(org_id, case_id)
        self.assertEqual(latest["id"], first["id"])
        self.assertEqual(len(self.experiments(org_id, first["id"])), 12)

    def test_malformed_upload_stores_nothing(self):
        org_id, client, case_id = self.case("Atomic4")
        files_before = self.uploaded_files(org_id)
        for name, content in (("empty.csv", b""), ("junk.csv", b"\x00\x01\x02not,a,table"), ("wrong.csv", b"foo,bar\n1,2\n"),
                              ("fake.xlsx", b"this is not a zip"), ("fake.pdf", b"%PDF-1.4 broken")):
            r = client.post(f"/api/change-cases/{case_id}/dataset", files={"file": (name, content, "application/octet-stream")})
            self.assertIn(r.status_code, (400, 422), (name, r.status_code, r.text[:150]))
        self.assertEqual(self.datasets(org_id, case_id), [])
        self.assertEqual(self.uploaded_files(org_id), files_before)

    def test_successful_upload_stores_exactly_one_file_matching_its_dataset(self):
        org_id, client, case_id = self.case("Atomic5")
        files_before = set(self.uploaded_files(org_id))
        r = self.upload(client, case_id, "ok.csv", good_csv(12))
        self.assertEqual(r.status_code, 201, r.text)
        dataset = self.datasets(org_id, case_id)[0]
        new_files = set(self.uploaded_files(org_id)) - files_before
        self.assertEqual(new_files, {dataset["stored_filename"]})


if __name__ == "__main__":
    unittest.main()