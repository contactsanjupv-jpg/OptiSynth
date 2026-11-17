"""Level 5: hostile-file resource limits, login guessing/enumeration, production secrets, error detail, caching, report
cleanup, dependency pins. Hostile files here are SMALL stand-ins for the real thing (the review used 100 KB to 200 KB
files that cost minutes of CPU and gigabytes of memory); every test is bounded by a short timeout.
Run:  python3 -m unittest backend.tests.test_level5_hardening -v"""
import io
import json
import logging
import os
import re
import resource
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import zipfile
import zlib
from unittest import mock

from docx import Document
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openpyxl import Workbook

from backend.app.config import settings as settings_module
from backend.app.config.settings import ConfigError, Settings, settings
from backend.app.middleware.error_handling import register_error_handlers
from backend.app.middleware.security_headers import SecurityHeadersMiddleware
from backend.app.schemas.errors import TooManyAttempts, ValidationError
from backend.app.security import login_throttle
from backend.app.security.login_throttle import LoginThrottle
from backend.app.services import auth_service, evidence_extraction as ee, sandboxed_parse
from backend.tests import evidence_fixtures as F
from backend.tests._api_base import IsolatedApiTestCase

NS = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


# ---------------------------------------------------------------- hostile-file builders
def _rewrite_zip(base: bytes, member: str, data: bytes) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(base)) as zin, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zout:
        for item in zin.infolist():
            zout.writestr(item, data if item.filename == member else zin.read(item.filename))
    return out.getvalue()


def _xlsx_with_sheet(sheet_xml: str) -> bytes:
    wb = Workbook()
    wb.active.title = "Data"
    wb.active.append(["a", "b"])
    wb.active.append([1, 2])
    buf = io.BytesIO()
    wb.save(buf)
    return _rewrite_zip(buf.getvalue(), "xl/worksheets/sheet1.xml", sheet_xml.encode())


def _xlsx_blank_rows(n: int, dimension: bool) -> bytes:
    dim = f'<dimension ref="A1:B{n}"/>' if dimension else ""
    head = (f'<?xml version="1.0"?><worksheet {NS}>{dim}<sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>a</t></is></c></row>'
            '<row r="2"><c r="A2"><v>1</v></c></row>')
    return _xlsx_with_sheet(head + "<row/>" * n + "</sheetData></worksheet>")


def _docx_empty_paragraphs(n: int) -> bytes:
    d = Document()
    d.add_paragraph("x")
    buf = io.BytesIO()
    d.save(buf)
    base = buf.getvalue()
    with zipfile.ZipFile(io.BytesIO(base)) as z:
        xml = z.read("word/document.xml").decode()
    head, tail = xml.split("<w:body>")[0] + "<w:body>", "</w:body>" + xml.split("</w:body>")[1]
    return _rewrite_zip(base, "word/document.xml", (head + "<w:p/>" * n + "<w:sectPr/>" + tail).encode())


def _pdf_inflating(megabytes: int) -> bytes:
    comp = zlib.compress(b"q " * (megabytes * 1024 * 1024 // 2), 9)
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << >> >>",
            b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(comp) + comp + b"\nendstream"]
    out, offs = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offs:
        out += b"%010d 00000 n \n" % off
    return out + b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF" % (len(objs) + 1, xref)


def _zip_declaring(megabytes: int, member="word/document.xml") -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        z.writestr(member, b"\0" * (megabytes * 1024 * 1024))
    return out.getvalue()


def _worker_processes() -> int:
    if not shutil.which("pgrep"):
        return 0
    # Only children of THIS process: a bare `pgrep -f` also matches any shell whose command line mentions the module.
    return len(subprocess.run(["pgrep", "-P", str(os.getpid()), "-f", "services.parse_worker"], capture_output=True, text=True).stdout.split())


def _outcome(returncode=0, stdout=b"", stderr=b"", timed_out=False, too_large=False):
    return sandboxed_parse.Outcome(returncode, stdout, stderr, timed_out, too_large)


# ---------------------------------------------------------------- 1. archive guard + sandbox
class TestArchiveGuard(unittest.TestCase):
    def test_a_zip_declaring_far_more_data_than_allowed_is_refused_before_any_work(self):
        data = _zip_declaring(6)
        self.assertLess(len(data), 20_000)
        with mock.patch.object(settings, "MAX_ARCHIVE_UNCOMPRESSED_BYTES", 1024 * 1024), \
             mock.patch("subprocess.Popen", side_effect=AssertionError("must not start a process")):
            for name in ("a.docx", "a.xlsx"):
                with self.assertRaises(ValidationError) as cm:
                    ee.list_tables(name, data)
                self.assertIn("expands to far more data than its size suggests", cm.exception.message)
                self.assertIn("about 6 MB once unpacked", cm.exception.message)

    def test_too_many_parts_is_refused(self):
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as z:
            for i in range(30):
                z.writestr(f"p{i}.xml", b"x")
        with mock.patch.object(settings, "MAX_ARCHIVE_ENTRIES", 10):
            with self.assertRaises(ValidationError) as cm:
                ee.check_archive("xlsx", out.getvalue())
        self.assertIn("30 parts", cm.exception.message)

    def test_limits_are_inclusive_and_ordinary_files_pass(self):
        data = F.make_xlsx({"Data": F.grid(F.CANON_HEADERS, F.base_rows(14))})
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            declared, parts = sum(i.file_size for i in z.infolist()), len(z.infolist())
        with mock.patch.object(settings, "MAX_ARCHIVE_UNCOMPRESSED_BYTES", declared), mock.patch.object(settings, "MAX_ARCHIVE_ENTRIES", parts):
            ee.check_archive("xlsx", data)  # exactly at the limits: allowed
        with mock.patch.object(settings, "MAX_ARCHIVE_UNCOMPRESSED_BYTES", declared - 1):
            with self.assertRaises(ValidationError):
                ee.check_archive("xlsx", data)

    def test_csv_pdf_and_non_zip_bytes_are_left_to_their_own_checks(self):
        for kind in ("csv", "pdf"):
            ee.check_archive(kind, _zip_declaring(1))  # not an Office container: the guard does not apply
        ee.check_archive("xlsx", b"this is not a zip file")  # the parser rejects it with its usual message
        with self.assertRaises(ValidationError) as cm:
            ee.list_tables("broken.xlsx", b"this is not a zip file")
        self.assertNotIn("expands", cm.exception.message)


class TestScannedRowCap(unittest.TestCase):
    def test_blank_rows_are_bounded_when_the_sheet_declares_its_size(self):
        data = _xlsx_blank_rows(150, dimension=True)
        with mock.patch.object(settings, "MAX_UPLOAD_ROWS", 20), mock.patch.object(settings, "PARSE_ISOLATION", False):
            with self.assertRaises(ValidationError) as cm:
                ee.list_tables("blank.xlsx", data)
        self.assertIn("more than 100 rows once blank rows are counted", cm.exception.message)

    def test_a_sheet_with_some_blank_rows_still_loads(self):
        data = _xlsx_blank_rows(30, dimension=True)
        with mock.patch.object(settings, "MAX_UPLOAD_ROWS", 20), mock.patch.object(settings, "PARSE_ISOLATION", False):
            tables = ee.list_tables("blank.xlsx", data)["tables"]
        self.assertEqual(len(tables), 1)

    def test_a_row_wider_than_excel_allows_is_refused(self):
        from openpyxl.utils import get_column_letter
        cells = "".join(f'<c r="{get_column_letter(i)}1" t="inlineStr"><is><t>v</t></is></c>' for i in range(1, ee.MAX_XLSX_CELLS_PER_ROW + 6))
        data = _xlsx_with_sheet(f'<?xml version="1.0"?><worksheet {NS}><sheetData><row r="1">{cells}</row></sheetData></worksheet>')
        with mock.patch.object(settings, "PARSE_ISOLATION", False):
            with self.assertRaises(ValidationError) as cm:
                ee.list_tables("wide.xlsx", data)
        self.assertIn("wider than", cm.exception.message)


class TestSandboxMatchesInProcess(unittest.TestCase):
    def _both(self, name, data):
        with mock.patch.object(settings, "PARSE_ISOLATION", False):
            direct = ee.list_tables(name, data)
        with mock.patch.object(settings, "PARSE_ISOLATION", True):
            isolated = ee.list_tables(name, data)
        return direct, isolated

    def test_identical_results_for_every_binary_format(self):
        grid = F.grid(F.CANON_HEADERS, F.base_rows(14))
        for name, data in (("a.xlsx", F.make_xlsx({"Data": grid, "Two": grid})), ("a.docx", F.make_docx([("table", grid)])),
                           ("a.pdf", F.make_pdf([("table", grid)]))):
            direct, isolated = self._both(name, data)
            self.assertEqual(json.dumps(direct, sort_keys=True), json.dumps(isolated, sort_keys=True), name)
            self.assertTrue(isolated["tables"], name)

    def test_the_usual_error_messages_survive_the_process_boundary(self):
        for name, data in (("bad.xlsx", b"not a workbook"), ("bad.docx", b"not a document"), ("bad.pdf", b"%PDF-1.4 garbage")):
            direct = isolated = None
            with mock.patch.object(settings, "PARSE_ISOLATION", False):
                with self.assertRaises(ValidationError) as a:
                    ee.list_tables(name, data)
            with mock.patch.object(settings, "PARSE_ISOLATION", True):
                with self.assertRaises(ValidationError) as b:
                    ee.list_tables(name, data)
            self.assertEqual(a.exception.message, b.exception.message, name)

    def test_csv_is_not_sent_to_a_child_process(self):
        with mock.patch("subprocess.Popen", side_effect=AssertionError("csv must stay in-process")):
            tables = ee.list_tables("a.csv", F.csv_bytes(F.CANON_HEADERS, F.base_rows(14)))["tables"]
        self.assertTrue(tables)


class TestHostileFilesAreContained(unittest.TestCase):
    """Real inflating files, a short timeout, and a check that the parent process stayed small."""

    def setUp(self):
        self._patches = [mock.patch.object(settings, "PARSE_ISOLATION", True), mock.patch.object(settings, "PARSE_TIMEOUT_SECONDS", 3)]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)

    def _assert_refused_quickly(self, name, data):
        before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        started = time.time()
        with self.assertRaises(ValidationError) as cm:
            ee.list_tables(name, data)
        self.assertLess(time.time() - started, 15, "the wall-clock limit must end the work")
        message = cm.exception.message
        self.assertTrue(
        "too large or complex to read safely" in message
        or "more than 100000 rows once blank rows are counted" in message,
        message,)
        grew_mb = (resource.getrusage(resource.RUSAGE_SELF).ru_maxrss - before) / (1 if sys.platform == "darwin" else 1024) / (1024 if sys.platform == "darwin" else 1)
        self.assertLess(grew_mb, 300, "the web process itself must not have done the work")
        return cm.exception.message

    def test_millions_of_empty_word_paragraphs(self):
        data = _docx_empty_paragraphs(8_000_000)
        self.assertLess(len(data), 200_000)  # a 106 KB file cost 83 s and 1 GB in the review
        self._assert_refused_quickly("bomb.docx", data)
        for _ in range(50):
            if _worker_processes() == 0:
                break
            time.sleep(0.1)
        self.assertEqual(_worker_processes(), 0, "the killed parser must not linger")

    def test_a_pdf_page_stream_that_inflates_enormously(self):
        data = _pdf_inflating(10)
        self.assertLess(len(data), 20_000)  # 10 KB: 31 s and 2 GB in the review
        self._assert_refused_quickly("bomb.pdf", data)

    def test_millions_of_blank_spreadsheet_rows_without_a_size_declaration(self):
        data = _xlsx_blank_rows(4_000_000, dimension=False)
        self.assertLess(len(data), 2_000_000)
        with mock.patch.object(settings, "MAX_ARCHIVE_UNCOMPRESSED_BYTES", 500 * 1024 * 1024):
            self._assert_refused_quickly("bomb.xlsx", data)

    @unittest.skipUnless(sys.platform.startswith("linux"), "an address-space limit is only enforced on Linux")
    def test_the_memory_limit_stops_a_parser_that_grows_quickly(self):
        with mock.patch.object(settings, "PARSE_MEMORY_MB", 400), mock.patch.object(settings, "PARSE_TIMEOUT_SECONDS", 60):
            started = time.time()
            try:
                result = ee.list_tables("bomb.pdf", _pdf_inflating(10))
                outcome = "read (the PDF library gave up on the page: no tables)"
                self.assertEqual(result["tables"], [])
            except ValidationError as err:
                outcome = "refused"
                self.assertIn("too large or complex", err.message)
            # Either way it ended by the memory limit, not by waiting out the 60 s clock, and it did not take the server down.
            self.assertLess(time.time() - started, 40, outcome)


class TestLimitsReachTheChild(unittest.TestCase):
    def test_a_limit_changed_in_the_parent_applies_in_the_parser_process(self):
        data = F.pdf_two_tables()
        with mock.patch.object(ee, "MAX_PDF_PAGES", 1), mock.patch.object(settings, "PARSE_ISOLATION", True):
            with self.assertRaises(ValidationError) as cm:
                ee.list_tables("two.pdf", data)
        self.assertIn("pages (limit 1)", cm.exception.message)
        with mock.patch.object(settings, "PARSE_ISOLATION", True):
            self.assertTrue(ee.list_tables("two.pdf", data)["tables"])  # and back to normal afterwards

    def test_current_limits_lists_every_limit_and_is_sent_to_the_worker(self):
        self.assertEqual(set(ee.current_limits()), set(ee.LIMIT_NAMES))
        self.assertTrue(all(isinstance(v, int) for v in ee.current_limits().values()))
        sent = {}

        def fake(cmd, *args, **kw):
            sent["cmd"] = cmd
            return _outcome(stdout=json.dumps({"ok": {"tables": [], "warnings": []}}).encode())

        with mock.patch.object(sandboxed_parse, "_run_bounded", side_effect=fake):
            sandboxed_parse.parse("pdf", b"x", limits={"MAX_PDF_PAGES": 3})
        self.assertEqual(json.loads(sent["cmd"][-1]), {"MAX_PDF_PAGES": 3})

    def test_a_hostile_limit_name_is_ignored_by_the_worker(self):
        done = subprocess.run([sys.executable, "-m", "backend.app.services.parse_worker", "pdf", str(2 * 1024 ** 3), "20",
                               json.dumps({"os": 1, "__name__": 5, "MAX_PDF_PAGES": 7})],
                              input=F.make_pdf([("table", F.grid(F.CANON_HEADERS, F.base_rows(3)))]), capture_output=True,
                              env=sandboxed_parse.child_environment(), timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr[-300:])
        self.assertIn("ok", json.loads(done.stdout))


class TestSandboxMechanics(unittest.TestCase):
    def test_the_child_receives_no_secrets_and_no_database(self):
        with mock.patch.dict(os.environ, {"SECRET_KEY": "REAL-SECRET-VALUE", "PASSWORD_PEPPER": "REAL-PEPPER", "DATABASE_URL": "postgresql://u:pw@h/db"}):
            env = sandboxed_parse.child_environment()
        flat = json.dumps(env)
        for leaked in ("REAL-SECRET-VALUE", "REAL-PEPPER", "postgresql", "pw@h"):
            self.assertNotIn(leaked, flat)
        self.assertEqual(env["DATABASE_URL"], "sqlite://")
        self.assertEqual(env["PARSE_ISOLATION"], "false")
        self.assertIn(ROOT, env["PYTHONPATH"].split(os.pathsep))

    def _fake(self, **kw):
        return mock.patch.object(sandboxed_parse, "_run_bounded", return_value=_outcome(**kw))

    def test_every_way_the_child_can_answer(self):
        ok = json.dumps({"ok": {"tables": [{"ref": "t1"}], "warnings": ["w"]}}).encode()
        with self._fake(stdout=ok):
            self.assertEqual(sandboxed_parse.parse("xlsx", b"x"), ([{"ref": "t1"}], ["w"]))
        with self._fake(stdout=json.dumps({"validation": "Could not read this .xlsx file."}).encode()):
            with self.assertRaises(ValidationError) as cm:
                sandboxed_parse.parse("xlsx", b"x")
            self.assertEqual(cm.exception.message, "Could not read this .xlsx file.")
        for fake in (self._fake(returncode=-9), self._fake(returncode=1, stdout=b"Traceback"), self._fake(stdout=b"not json"),
                     self._fake(stdout=json.dumps({"too_heavy": "memory"}).encode())):
            with fake:
                with self.assertRaises(ValidationError) as cm:
                    sandboxed_parse.parse("pdf", b"x")
                self.assertEqual(cm.exception.message, sandboxed_parse.TOO_HEAVY_MESSAGE)
        with self._fake(stdout=json.dumps({"error": "KeyError"}).encode()):
            with self.assertRaises(RuntimeError) as cm:  # a parser BUG stays a 500, never a silent "bad file"
                sandboxed_parse.parse("pdf", b"x")
            self.assertIn("KeyError", str(cm.exception))
            self.assertNotIn("Traceback", str(cm.exception))

    def test_a_timeout_is_reported_as_too_heavy(self):
        with self._fake(timed_out=True):
            with self.assertRaises(ValidationError) as cm:
                sandboxed_parse.parse("pdf", b"x")
        self.assertEqual(cm.exception.message, sandboxed_parse.TOO_HEAVY_MESSAGE)

    def test_only_a_few_parsers_run_at_once_and_the_rest_are_told_to_retry(self):
        with mock.patch.object(settings, "MAX_CONCURRENT_PARSES", 1), mock.patch.object(sandboxed_parse, "QUEUE_WAIT_SECONDS", 0.2):
            sem = sandboxed_parse._semaphore()
            self.assertTrue(sem.acquire(timeout=1))
            try:
                with mock.patch.object(sandboxed_parse, "_run_bounded", side_effect=AssertionError("no slot, no process")):
                    with self.assertRaises(ValidationError) as cm:
                        sandboxed_parse.parse("pdf", b"x")
                self.assertEqual(cm.exception.message, sandboxed_parse.BUSY_MESSAGE)
            finally:
                sem.release()
        with mock.patch.object(settings, "MAX_CONCURRENT_PARSES", 1):
            self.assertEqual(sandboxed_parse.parse.__module__, "backend.app.services.sandboxed_parse")  # slot was released: usable again
            with self._fake(stdout=json.dumps({"ok": {"tables": [], "warnings": []}}).encode()):
                self.assertEqual(sandboxed_parse.parse("pdf", b"x"), ([], []))

    def _fake(self, **kw):
        return mock.patch.object(sandboxed_parse, "_run_bounded", return_value=_outcome(**kw))


def _run_child(code, data=b"", timeout=15, max_stdout=1024 * 1024, max_stderr=4096):
    """Runs a small stand-in for the parser through the real bounded runner."""
    return sandboxed_parse._run_bounded([sys.executable, "-c", code], data, timeout, dict(os.environ), max_stdout, max_stderr)


MB = 1024 * 1024


class TestBoundedOutput(unittest.TestCase):
    """The parent must never hold more than the cap, whatever the child writes (review: 400 MB written -> +800 MB held)."""

    def test_a_child_that_floods_stdout_is_killed_and_only_the_cap_is_kept(self):
        started = time.time()
        out = _run_child("import sys\nwhile True: sys.stdout.write('x' * 65536)", max_stdout=1 * MB)
        self.assertTrue(out.output_too_large)
        self.assertFalse(out.timed_out)
        self.assertLessEqual(len(out.stdout), 1 * MB + sandboxed_parse.CHUNK_BYTES)
        self.assertNotEqual(out.returncode, 0)
        self.assertLess(time.time() - started, 10, "an endless writer must be stopped by the cap, not by waiting")

    def test_the_cap_is_inclusive_one_byte_over_is_refused(self):
        exact = _run_child(f"import sys; sys.stdout.write('y' * {1 * MB})", max_stdout=1 * MB)
        self.assertEqual((exact.output_too_large, exact.returncode, len(exact.stdout)), (False, 0, 1 * MB))
        over = _run_child(f"import sys; sys.stdout.write('y' * {1 * MB + 1})", max_stdout=1 * MB)
        self.assertTrue(over.output_too_large)

    def test_python_memory_in_the_parent_stays_under_the_cap_while_the_child_writes_hundreds_of_megabytes(self):
        import tracemalloc
        tracemalloc.start()
        try:
            out = _run_child(f"import sys\nfor _ in range(300): sys.stdout.write('z' * {1 * MB})", max_stdout=4 * MB, timeout=30)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertTrue(out.output_too_large)
        self.assertLess(peak, 4 * MB + 3 * sandboxed_parse.CHUNK_BYTES + 2 * MB, f"held {peak / MB:.1f} MB (the old code held about 2x what was written)")

    def test_stderr_is_drained_but_only_its_start_is_kept_and_the_answer_still_arrives(self):
        code = (f"import sys\nsys.stderr.write('e' * {5 * MB})\nsys.stderr.flush()\nsys.stdout.write('{{\"ok\": 1}}')")
        started = time.time()
        out = _run_child(code, max_stderr=4096, timeout=20)
        self.assertEqual((out.returncode, bytes(out.stdout), out.output_too_large, out.timed_out), (0, b'{"ok": 1}', False, False))
        self.assertEqual(len(out.stderr), 4096)
        self.assertEqual(bytes(out.stderr), b"e" * 4096)
        self.assertLess(time.time() - started, 15, "a full stderr pipe must never stall the child")

    def test_endless_stderr_is_ended_by_the_clock_and_never_grows_the_parent(self):
        import tracemalloc
        tracemalloc.start()
        try:
            out = _run_child("import sys\nwhile True: sys.stderr.write('e' * 65536)", timeout=2, max_stderr=4096)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertTrue(out.timed_out)
        self.assertLess(peak, 2 * MB)
        self.assertLessEqual(len(out.stderr), 4096)

    def test_a_child_that_outlives_its_time_is_killed(self):
        started = time.time()
        out = _run_child("import time; time.sleep(60)", timeout=1)
        self.assertTrue(out.timed_out)
        self.assertLess(time.time() - started, 8)
        self.assertLess(out.returncode, 0)  # terminated by a signal

    @unittest.skipUnless(os.name == "posix", "process groups are a POSIX feature")
    def test_the_whole_process_group_is_killed_not_just_the_child(self):
        code = ("import subprocess, sys, time\n"
                "g = subprocess.Popen(['sleep', '60'])\n"
                "sys.stdout.write(str(g.pid)); sys.stdout.flush()\n"
                "time.sleep(60)")
        out = _run_child(code, timeout=1)
        self.assertTrue(out.timed_out)
        grandchild = int(bytes(out.stdout).decode())
        for _ in range(50):
            try:
                os.kill(grandchild, 0)
                time.sleep(0.1)
            except ProcessLookupError:
                break
        else:
            self.fail("a process the parser started was left running")

    def test_large_input_is_delivered_and_a_child_that_never_reads_it_does_not_hang_or_raise(self):
        data = os.urandom(5 * MB)
        echo = _run_child("import sys; sys.stdout.write(str(len(sys.stdin.buffer.read())))", data=data, timeout=20)
        self.assertEqual((echo.returncode, bytes(echo.stdout)), (0, str(5 * MB).encode()))
        ignores = _run_child("pass", data=data, timeout=20)
        self.assertEqual(ignores.returncode, 0)
        dies = _run_child("import os; os._exit(3)", data=data, timeout=20)
        self.assertEqual(dies.returncode, 3)

    def test_no_threads_or_children_are_left_behind(self):
        import threading
        before = threading.active_count()
        for code in ("pass", "import sys; sys.stdout.write('x' * 300000)", "import time; time.sleep(30)"):
            _run_child(code, max_stdout=100000, timeout=1)
        time.sleep(0.2)
        self.assertEqual(threading.active_count(), before)
        self.assertEqual(_worker_processes(), 0)

    def test_the_real_parser_still_works_through_the_runner(self):
        grid = F.grid(F.CANON_HEADERS, F.base_rows(14))
        with mock.patch.object(settings, "PARSE_ISOLATION", True):
            for name, data in (("a.xlsx", F.make_xlsx({"Data": grid})), ("a.docx", F.make_docx([("table", grid)])),
                               ("a.pdf", F.make_pdf([("table", grid)]))):
                self.assertTrue(ee.list_tables(name, data)["tables"], name)


class TestOutcomeMapping(unittest.TestCase):
    def _parse(self, **kw):
        with mock.patch.object(sandboxed_parse, "_run_bounded", return_value=_outcome(**kw)):
            return sandboxed_parse.parse("xlsx", b"x")

    def test_oversized_output_has_its_own_message(self):
        with self.assertRaises(ValidationError) as cm:
            self._parse(too_large=True, stdout=b"x" * 10, returncode=-9)
        self.assertEqual(cm.exception.message, sandboxed_parse.OUTPUT_TOO_LARGE_MESSAGE)
        self.assertNotEqual(sandboxed_parse.OUTPUT_TOO_LARGE_MESSAGE, sandboxed_parse.TOO_HEAVY_MESSAGE)

    def test_partial_output_from_a_killed_child_is_never_parsed(self):
        valid_looking = json.dumps({"ok": {"tables": [{"ref": "t"}], "warnings": []}}).encode()
        with self.assertRaises(ValidationError):
            self._parse(too_large=True, stdout=valid_looking, returncode=-9)
        with self.assertRaises(ValidationError):
            self._parse(timed_out=True, stdout=valid_looking, returncode=-9)

    def test_malformed_output_is_too_heavy_never_a_crash(self):
        good = json.dumps({"ok": {"tables": [], "warnings": []}}).encode()
        for label, stdout in (("empty", b""), ("truncated", good[:-5]), ("not json", b"not json"), ("binary", os.urandom(200)),
                              ("array", b"[]"), ("null", b"null"), ("string", b'"ok"'), ("number", b"42"),
                              ("trailing junk", good + b"junk"), ("bad utf-8", b'{"ok": "\xff\xfe"}'), ("nan only", b"NaN")):
            with self.assertRaises(ValidationError, msg=label) as cm:
                self._parse(stdout=stdout)
            self.assertEqual(cm.exception.message, sandboxed_parse.TOO_HEAVY_MESSAGE, label)

    def test_a_nonzero_exit_wins_over_any_output_and_is_logged_without_content(self):
        good = json.dumps({"ok": {"tables": [], "warnings": []}}).encode()
        with self.assertLogs("rdopt", level="WARNING") as cm:
            logging.getLogger("rdopt").disabled = False
            with self.assertRaises(ValidationError):
                self._parse(returncode=-9, stdout=good, stderr=b"SECRET CUSTOMER TEXT")
        self.assertIn("exit status -9", cm.output[0])
        self.assertNotIn("SECRET", cm.output[0])

    def test_normal_answers_pass_through_unchanged(self):
        tables = [{"ref": "t1", "rows": [{"src_row": 1, "cells": ["a", 1, None, 2.5]}]}]
        out = self._parse(stdout=json.dumps({"ok": {"tables": tables, "warnings": ["w1"]}}).encode())
        self.assertEqual(out, (tables, ["w1"]))
        self.assertEqual(self._parse(stdout=json.dumps({"ok": {"tables": [], "warnings": []}}).encode()), ([], []))

    def test_messages_from_the_child_are_cut_to_a_safe_length(self):
        with self.assertRaises(ValidationError) as cm:
            self._parse(stdout=json.dumps({"validation": "m" * 5000}).encode())
        self.assertEqual(len(cm.exception.message), sandboxed_parse.MAX_MESSAGE_CHARS)
        with self.assertRaises(RuntimeError) as cm:
            self._parse(stdout=json.dumps({"error": "E" * 5000}).encode())
        self.assertLess(len(str(cm.exception)), 120)
        with self.assertRaises(RuntimeError):
            self._parse(stdout=b"{}")
        with self.assertRaises(ValidationError):
            self._parse(stdout=json.dumps({"validation": 12345}).encode())  # a non-string message is still a refusal

    def test_the_cap_is_read_from_settings_and_passed_to_the_runner(self):
        seen = {}

        def fake(cmd, data, timeout, env, max_stdout, max_stderr):
            seen.update(max_stdout=max_stdout, max_stderr=max_stderr, timeout=timeout)
            return _outcome(stdout=json.dumps({"ok": {"tables": [], "warnings": []}}).encode())

        with mock.patch.object(sandboxed_parse, "_run_bounded", side_effect=fake), mock.patch.object(settings, "PARSE_MAX_OUTPUT_MB", 7), \
             mock.patch.object(settings, "PARSE_TIMEOUT_SECONDS", 9):
            sandboxed_parse.parse("pdf", b"x")
        self.assertEqual(seen, {"max_stdout": 7 * MB, "max_stderr": sandboxed_parse.MAX_STDERR_BYTES, "timeout": 14})

    def test_default_and_override(self):
        self.assertEqual(_settings().PARSE_MAX_OUTPUT_MB, 32)
        self.assertEqual(_settings(PARSE_MAX_OUTPUT_MB="8").PARSE_MAX_OUTPUT_MB, 8)

    def test_the_default_cap_is_well_above_the_largest_legitimate_table(self):
        # Measured in review: 20,000 rows x 60 columns of realistic cells (a 9.3 MB workbook) answers with 17.7 MB of JSON.
        self.assertGreaterEqual(_settings().PARSE_MAX_OUTPUT_MB, int(17.7 * 1.5))


class TestOversizedResultsEndToEnd(unittest.TestCase):
    def test_a_workbook_with_enormous_cells_is_refused_cleanly_and_a_normal_one_still_reads(self):
        data = _xlsx_with_sheet(
            f'<?xml version="1.0"?><worksheet {NS}><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>a</t></is></c></row>'
            f'<row r="2"><c r="A2" t="inlineStr"><is><t>{"x" * (3 * MB)}</t></is></c></row></sheetData></worksheet>')
        self.assertLess(len(data), 50_000)
        with mock.patch.object(settings, "PARSE_ISOLATION", True), mock.patch.object(settings, "PARSE_MAX_OUTPUT_MB", 1):
            with self.assertRaises(ValidationError) as cm:
                ee.list_tables("cells.xlsx", data)
            self.assertEqual(cm.exception.message, sandboxed_parse.OUTPUT_TOO_LARGE_MESSAGE)
            ok = F.make_xlsx({"Data": F.grid(F.CANON_HEADERS, F.base_rows(14))})
            self.assertTrue(ee.list_tables("ok.xlsx", ok)["tables"])  # the same tiny cap still reads an ordinary sheet

    def test_a_result_just_inside_the_cap_is_returned_intact(self):
        text = "q" * (400 * 1024)
        data = _xlsx_with_sheet(
            f'<?xml version="1.0"?><worksheet {NS}><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>a</t></is></c></row>'
            f'<row r="2"><c r="A2" t="inlineStr"><is><t>{text}</t></is></c></row></sheetData></worksheet>')
        with mock.patch.object(settings, "PARSE_ISOLATION", True), mock.patch.object(settings, "PARSE_MAX_OUTPUT_MB", 1):
            tables = ee.list_tables("inside.xlsx", data)["tables"]
        self.assertIn(text, json.dumps(tables))


class TestChildEnvironmentPerPlatform(unittest.TestCase):
    def test_windows_keeps_the_variables_python_needs_to_start(self):
        with mock.patch.dict(os.environ, {"SYSTEMROOT": "C:\\Windows", "TEMP": "C:\\Temp"}), mock.patch.object(sandboxed_parse.os, "name", "nt"):
            env = sandboxed_parse.child_environment()
        self.assertEqual((env["SYSTEMROOT"], env["TEMP"]), ("C:\\Windows", "C:\\Temp"))
        self.assertNotIn("REAL", json.dumps(env))

    def test_other_platforms_do_not_get_them_and_secrets_never_pass_on_any(self):
        with mock.patch.dict(os.environ, {"SYSTEMROOT": "C:\\Windows", "SECRET_KEY": "REAL-SECRET"}):
            env = sandboxed_parse.child_environment()
        if os.name != "nt":
            self.assertNotIn("SYSTEMROOT", env)
        self.assertNotIn("REAL-SECRET", json.dumps(env))

    def test_the_memory_limit_is_only_requested_where_it_is_enforced(self):
        import resource
        calls = []
        from backend.app.services import parse_worker
        real = resource.setrlimit
        with mock.patch.object(resource, "setrlimit", side_effect=lambda lim, val: calls.append(lim)):
            for platform in ("linux", "darwin"):
                calls.clear()
                with mock.patch.object(parse_worker.sys, "platform", platform):
                    parse_worker._apply_limits(512 * MB, 20)
                self.assertIn(resource.RLIMIT_CPU, calls, platform)  # the CPU limit is requested on every POSIX platform
                self.assertEqual(resource.RLIMIT_AS in calls, platform == "linux", platform)


# ---------------------------------------------------------------- process lifecycle (descendants of the parser)
def _proc_state(pid):
    """'R'/'S'/... while alive, 'Z' once killed but not yet reaped by its parent, 'gone' when it no longer exists."""
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[0]
    except OSError:
        return "gone"


def _is_running(pid):
    return _proc_state(pid) not in ("Z", "gone")


def _wait_until_dead(pid, seconds=3.0):
    end = time.time() + seconds
    while time.time() < end:
        if not _is_running(pid):
            return True
        time.sleep(0.02)
    return False


def _child_code(pidfile, grandchild_args="['sleep', '60']", body="sys.stdout.write('{\\\"ok\\\": 1}'); sys.stdout.flush()", new_session=False):
    """A stand-in parser that starts a long-running grandchild (inheriting stdout and stderr), then runs `body` and exits."""
    return ("import subprocess, sys, time\n"
            f"g = subprocess.Popen({grandchild_args}{', start_new_session=True' if new_session else ''})\n"
            f"open({pidfile!r}, 'w').write(str(g.pid))\n"
            f"{body}\n")


class _LifecycleCase(unittest.TestCase):
    def setUp(self):
        self._pids = []
        self.addCleanup(self._reap)
        self.threads_before = {t.ident for t in threading.enumerate()}

    def _reap(self):
        for pid in self._pids:
            try:
                os.kill(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass

    def _pidfile(self):
        fd, path = tempfile.mkstemp()
        os.close(fd)
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))
        return path

    def _run(self, code, pidfile, timeout=15, max_stdout=1 * MB, data=b""):
        started = time.time()
        out = sandboxed_parse._run_bounded([sys.executable, "-c", code], data, timeout, dict(os.environ), max_stdout, 4096)
        elapsed = time.time() - started
        text = open(pidfile).read()
        pid = int(text) if text else None
        if pid:
            self._pids.append(pid)
        return out, pid, elapsed

    def _assert_no_leaked_threads(self):
        for _ in range(30):
            leaked = [t for t in threading.enumerate() if t.ident not in self.threads_before]
            if not leaked:
                return
            time.sleep(0.1)
        self.fail(f"threads left behind: {[t.name for t in leaked]}")


@unittest.skipUnless(sys.platform.startswith("linux"), "process-group behaviour is verified on Linux")
class TestParserDescendants(_LifecycleCase):
    def test_a_parser_that_exits_normally_but_leaves_a_grandchild_returns_promptly_with_its_full_answer(self):
        pf = self._pidfile()
        code = _child_code(pf, body="sys.stdout.write('{\"ok\": 1}'); sys.stdout.flush(); sys.stderr.write('note'); sys.stderr.flush()")
        out, gc, took = self._run(code, pf)
        self.assertLess(took, 3, f"returned after {took:.1f}s: it waited for the grandchild (was 60 s: the grandchild's whole life)")
        self.assertEqual((out.returncode, bytes(out.stdout), bytes(out.stderr)), (0, b'{"ok": 1}', b"note"))
        self.assertEqual((out.timed_out, out.output_too_large, out.pipes_held_open), (False, False, False))
        self.assertTrue(_wait_until_dead(gc), "the grandchild was left running")
        self._assert_no_leaked_threads()

    def test_the_parser_slot_is_released_so_the_next_file_is_not_refused_as_busy(self):
        pf = self._pidfile()
        code = _child_code(pf, body="sys.stdout.write('{\"ok\": {\"tables\": [], \"warnings\": []}}'); sys.stdout.flush()")
        real = sandboxed_parse._run_bounded
        with mock.patch.object(settings, "MAX_CONCURRENT_PARSES", 1), mock.patch.object(sandboxed_parse, "QUEUE_WAIT_SECONDS", 1.0), \
             mock.patch.object(sandboxed_parse, "_run_bounded", side_effect=lambda cmd, *a, **k: real([sys.executable, "-c", code], *a, **k)):
            started = time.time()
            for _ in range(3):
                self.assertEqual(sandboxed_parse.parse("pdf", b"x"), ([], []))
            self.assertLess(time.time() - started, 8)
        self._pids.append(int(open(pf).read()))
        self._assert_no_leaked_threads()

    def test_a_normal_parser_with_no_descendants_keeps_all_of_its_output(self):
        pf = self._pidfile()
        body = f"sys.stdout.write('v' * {900 * 1024}); sys.stdout.flush()"
        code = "import sys, os\nopen(%r, 'w').write(str(os.getpid()))\n%s\n" % (pf, body)
        out, _, took = self._run(code, pf, max_stdout=1 * MB)
        self.assertEqual((out.returncode, len(out.stdout), out.output_too_large, out.pipes_held_open), (0, 900 * 1024, False, False))
        self.assertLess(took, 3)

    def test_output_near_the_cap_is_kept_intact_even_with_a_grandchild(self):
        pf = self._pidfile()
        body = f"sys.stdout.write('v' * {1 * MB}); sys.stdout.flush()"
        out, gc, took = self._run(_child_code(pf, body=body), pf, max_stdout=1 * MB)
        self.assertEqual((out.returncode, len(out.stdout), out.output_too_large), (0, 1 * MB, False))
        self.assertTrue(_wait_until_dead(gc))
        self.assertLess(took, 3)

    def test_timeout_with_a_grandchild_stops_both(self):
        pf = self._pidfile()
        out, gc, took = self._run(_child_code(pf, body="time.sleep(60)"), pf, timeout=1)
        self.assertTrue(out.timed_out)
        self.assertLess(took, 6)
        self.assertTrue(_wait_until_dead(gc))
        self._assert_no_leaked_threads()

    def test_oversized_output_with_a_grandchild_stops_both(self):
        pf = self._pidfile()
        body = "while True: sys.stdout.write('x' * 65536)"
        out, gc, took = self._run(_child_code(pf, body=body), pf, max_stdout=1 * MB)
        self.assertTrue(out.output_too_large)
        self.assertLessEqual(len(out.stdout), 1 * MB + sandboxed_parse.CHUNK_BYTES)
        self.assertLess(took, 6)
        self.assertTrue(_wait_until_dead(gc))
        self._assert_no_leaked_threads()

    def test_a_parser_that_never_reads_its_input_and_leaves_a_grandchild_does_not_hang(self):
        pf = self._pidfile()
        out, gc, took = self._run(_child_code(pf), pf, data=os.urandom(5 * MB))
        self.assertEqual(out.returncode, 0)
        self.assertLess(took, 4)
        self.assertTrue(_wait_until_dead(gc))
        self._assert_no_leaked_threads()

    def test_a_failing_parser_with_a_grandchild_reports_its_exit_status(self):
        pf = self._pidfile()
        out, gc, took = self._run(_child_code(pf, body="sys.exit(3)"), pf)
        self.assertEqual(out.returncode, 3)
        self.assertLess(took, 3)
        self.assertTrue(_wait_until_dead(gc))

    def test_malformed_output_with_a_grandchild_is_refused_quickly(self):
        pf = self._pidfile()
        code = _child_code(pf, body="sys.stdout.write('not json'); sys.stdout.flush()")
        real = sandboxed_parse._run_bounded
        started = time.time()
        with mock.patch.object(sandboxed_parse, "_run_bounded", side_effect=lambda cmd, *a, **k: real([sys.executable, "-c", code], *a, **k)):
            with self.assertRaises(ValidationError) as cm:
                sandboxed_parse.parse("pdf", b"x")
        self.assertEqual(cm.exception.message, sandboxed_parse.TOO_HEAVY_MESSAGE)
        self.assertLess(time.time() - started, 4)
        gc = int(open(pf).read())
        self._pids.append(gc)
        self.assertTrue(_wait_until_dead(gc))

    def test_the_real_parser_still_works_and_leaves_nothing_behind(self):
        grid = F.grid(F.CANON_HEADERS, F.base_rows(14))
        with mock.patch.object(settings, "PARSE_ISOLATION", True):
            for name, data in (("a.xlsx", F.make_xlsx({"Data": grid})), ("a.docx", F.make_docx([("table", grid)])),
                               ("a.pdf", F.make_pdf([("table", grid)]))):
                self.assertTrue(ee.list_tables(name, data)["tables"], name)
        self._assert_no_leaked_threads()
        self.assertEqual(_worker_processes(), 0)


@unittest.skipUnless(sys.platform.startswith("linux"), "process-group behaviour is verified on Linux")
class TestProcessGroupSafety(_LifecycleCase):
    def test_the_parser_runs_in_its_own_group_and_the_kill_targets_exactly_that_group(self):
        pf = self._pidfile()
        code = "import os, sys\nopen(%r, 'w').write(str(os.getpid()))\nsys.stdout.write(str(os.getpgrp()))\n" % pf
        out, pid, _ = self._run(code, pf)
        self.assertEqual(int(bytes(out.stdout)), pid)          # it leads its own group
        self.assertNotEqual(pid, os.getpgrp())                  # which is not the web process's group
        seen = []
        real = os.killpg
        with mock.patch.object(sandboxed_parse.os, "killpg", side_effect=lambda pg, sig: (seen.append((pg, sig)), real(pg, sig))[1]):
            out, pid, _ = self._run(code, pf)
        self.assertEqual(seen, [(pid, signal.SIGKILL)])

    @unittest.skipUnless(sandboxed_parse._CAN_PEEK_EXIT, "needs waitid(WNOWAIT)")
    def test_the_group_is_killed_while_the_leader_is_still_unreaped_so_its_id_cannot_be_recycled(self):
        pf = self._pidfile()
        states, real = [], os.killpg

        def spy(pg, sig):
            states.append(_proc_state(pg))  # the leader's state at the moment of the kill
            return real(pg, sig)

        with mock.patch.object(sandboxed_parse.os, "killpg", side_effect=spy):
            out, gc, _ = self._run(_child_code(pf), pf)
        self.assertEqual(out.returncode, 0)
        self.assertEqual(states, ["Z"], "the leader must be an unreaped zombie (its pid and group id still reserved) when the group is killed")

    @unittest.skipUnless(sandboxed_parse._CAN_PEEK_EXIT, "needs waitid(WNOWAIT)")
    def test_noticing_the_exit_does_not_reap_the_parser(self):
        p = subprocess.Popen([sys.executable, "-c", "pass"], start_new_session=True)
        try:
            for _ in range(100):
                if sandboxed_parse._leader_exited(p):
                    break
                time.sleep(0.02)
            self.assertTrue(sandboxed_parse._leader_exited(p))
            self.assertIsNone(p.returncode)                    # Popen has not reaped it
            self.assertEqual(_proc_state(p.pid), "Z")          # still a zombie: pid and group id reserved
            self.assertEqual(p.wait(), 0)
            self.assertEqual(_proc_state(p.pid), "gone")
        finally:
            p.kill()
            p.wait()

    def test_an_empty_group_is_a_harmless_no_op(self):
        p = subprocess.Popen([sys.executable, "-c", "pass"], start_new_session=True)
        p.wait()
        sandboxed_parse._kill_group(p)  # the group no longer exists: nothing is signalled, nothing raises

    def test_unrelated_processes_are_never_touched(self):
        bystander_same_group = subprocess.Popen(["sleep", "30"])                          # shares the web process's group
        bystander_other_group = subprocess.Popen(["sleep", "30"], start_new_session=True)  # a different session entirely
        self.addCleanup(lambda: [b.kill() or b.wait() for b in (bystander_same_group, bystander_other_group)])
        for scenario in ("normal", "timeout", "oversize"):
            pf = self._pidfile()
            body = {"normal": "sys.stdout.write('{}'); sys.stdout.flush()", "timeout": "time.sleep(60)",
                    "oversize": "while True: sys.stdout.write('x' * 65536)"}[scenario]
            self._run(_child_code(pf, body=body), pf, timeout=1)
        for b in (bystander_same_group, bystander_other_group):
            self.assertIsNone(b.poll())
            self.assertTrue(_is_running(b.pid))

    def test_without_waitid_the_same_cleanup_still_works(self):
        pf = self._pidfile()
        with mock.patch.object(sandboxed_parse, "_CAN_PEEK_EXIT", False):
            out, gc, took = self._run(_child_code(pf), pf)
        self.assertEqual((out.returncode, bytes(out.stdout)), (0, b'{"ok": 1}'))
        self.assertLess(took, 3)
        self.assertTrue(_wait_until_dead(gc))


@unittest.skipUnless(sys.platform.startswith("linux"), "process-group behaviour is verified on Linux")
class TestProcessThatLeavesTheGroup(_LifecycleCase):
    """A descendant that deliberately starts its own session cannot be reached by killing the group. The runner must still
    return in bounded time, keep what the parser wrote, end its threads, and the file must be refused."""

    def _escape_code(self, pf):
        return _child_code(pf, new_session=True)

    def test_bounded_time_threads_ended_and_the_flag_is_set(self):
        pf = self._pidfile()
        out, escaped, took = self._run(self._escape_code(pf), pf)
        self.assertLess(took, sandboxed_parse.DRAIN_SECONDS + 4, f"took {took:.1f}s")
        self.assertTrue(out.pipes_held_open)
        self.assertEqual((out.returncode, bytes(out.stdout)), (0, b'{"ok": 1}'))  # what it wrote before exiting is kept
        self.assertTrue(_is_running(escaped), "premise: the escaped process is out of reach of the group kill")
        self._assert_no_leaked_threads()  # reader and feeder threads have ended; only the stray process remains

    def test_the_file_is_refused_and_the_event_is_logged_without_content(self):
        pf = self._pidfile()
        real = sandboxed_parse._run_bounded
        logging.getLogger("rdopt").disabled = False
        with mock.patch.object(sandboxed_parse, "_run_bounded", side_effect=lambda cmd, *a, **k: real([sys.executable, "-c", self._escape_code(pf)], *a, **k)):
            with self.assertLogs("rdopt", level="WARNING") as cm:
                with self.assertRaises(ValidationError) as err:
                    sandboxed_parse.parse("pdf", b"x")
        self.assertEqual(err.exception.message, sandboxed_parse.TOO_HEAVY_MESSAGE)
        self.assertIn("still held open", cm.output[0])
        self._pids.append(int(open(pf).read()))

    def test_a_well_behaved_parser_never_sets_the_flag(self):
        for code in ("import sys; sys.stdout.write('{}')", "import sys; sys.exit(2)", "import time; time.sleep(30)"):
            self.assertFalse(_run_child(code, timeout=1).pipes_held_open, code)


class TestHostileUploadsThroughTheApplication(IsolatedApiTestCase):
    dir_name = "_test_env_level5_upload"

    def test_a_trial_account_cannot_exhaust_the_server_with_a_small_file_and_the_next_request_works(self):
        org_id, client, case_id = self.setup_case("Bomb")  # NOT paid: the default trial plan can reach the parsers
        big = _xlsx_blank_rows(5_000_000, dimension=False)
        self.assertLess(len(big), 1_000_000)
        with mock.patch.object(settings, "MAX_ARCHIVE_UNCOMPRESSED_BYTES", 20 * 1024 * 1024):
            for send in (lambda: self.upload(client, case_id, "bomb.xlsx", big), lambda: self.extract(client, case_id, "bomb.xlsx", big)):
                started = time.time()
                r = send()
                self.assertEqual(r.status_code, 400, r.text)
                self.assertIn("expands to far more data", r.json()["error"])
                self.assertLess(time.time() - started, 10)
        self.assertEqual(client.get("/health").status_code, 200)
        self.assertEqual(client.get("/api/auth/me").status_code, 200)

    def test_an_inflating_pdf_is_a_400_not_a_crash(self):
        org_id, client, case_id = self.setup_case("PdfBomb")
        with mock.patch.object(settings, "PARSE_TIMEOUT_SECONDS", 3):
            r = self.extract(client, case_id, "bomb.pdf", _pdf_inflating(10))
        self.assertEqual(r.status_code, 400, r.text)
        self.assertIn("too large or complex to read safely", r.json()["error"])
        self.assertEqual(client.get("/api/auth/me").status_code, 200)

    def test_normal_workbooks_still_upload_through_the_sandbox(self):
        org_id, client, case_id = self.setup_case("Normal")
        data = F.make_xlsx({"Data": F.grid(F.CANON_HEADERS, F.base_rows(14))})
        self.assertEqual(self.upload(client, case_id, "ok.xlsx", data).status_code, 201)


# ---------------------------------------------------------------- 2. login
class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class TestLoginThrottleUnit(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.t = LoginThrottle(clock=self.clock)

    def test_the_fifth_failure_locks_that_address_and_email_for_the_window(self):
        for _ in range(4):
            self.t.record_failure("1.1.1.1", "a@x.com")
            self.assertEqual(self.t.retry_after("1.1.1.1", "a@x.com"), 0)
        self.t.record_failure("1.1.1.1", "a@x.com")
        wait = self.t.retry_after("1.1.1.1", "a@x.com")
        self.assertTrue(0 < wait <= login_throttle.WINDOW_SECONDS + 1, wait)

    def test_the_lock_expires_exactly_when_the_oldest_failure_leaves_the_window(self):
        for _ in range(5):
            self.t.record_failure("1.1.1.1", "a@x.com")
        self.clock.advance(login_throttle.WINDOW_SECONDS - 1)
        self.assertGreater(self.t.retry_after("1.1.1.1", "a@x.com"), 0)
        self.clock.advance(1)
        self.assertEqual(self.t.retry_after("1.1.1.1", "a@x.com"), 0)

    def test_email_case_and_spaces_do_not_dodge_the_limit(self):
        for variant in ("A@X.com", " a@x.com", "a@x.COM ", "a@x.com", "A@x.com"):
            self.t.record_failure("1.1.1.1", variant)
        self.assertGreater(self.t.retry_after("1.1.1.1", "a@x.com"), 0)

    def test_one_persons_failures_do_not_lock_out_other_people(self):
        for _ in range(5):
            self.t.record_failure("1.1.1.1", "a@x.com")
        self.assertEqual(self.t.retry_after("2.2.2.2", "a@x.com"), 0)   # same email, another address
        self.assertEqual(self.t.retry_after("1.1.1.1", "b@x.com"), 0)   # same address, another email

    def test_one_address_guessing_many_accounts_is_stopped(self):
        for i in range(login_throttle.LIMIT_ADDRESS):
            self.t.record_failure("9.9.9.9", f"user{i}@x.com")
        self.assertGreater(self.t.retry_after("9.9.9.9", "someone-new@x.com"), 0)
        self.assertEqual(self.t.retry_after("8.8.8.8", "someone-new@x.com"), 0)

    def test_guessing_one_account_from_many_addresses_is_stopped_by_the_email_backstop(self):
        for i in range(login_throttle.LIMIT_EMAIL):
            self.t.record_failure(f"10.0.{i // 250}.{i % 250}", "victim@x.com")
        self.assertGreater(self.t.retry_after("77.7.7.7", "victim@x.com"), 0)

    def test_a_correct_login_clears_only_that_pair(self):
        for _ in range(4):
            self.t.record_failure("1.1.1.1", "a@x.com")
        self.t.record_success("1.1.1.1", "a@x.com")
        self.t.record_failure("1.1.1.1", "a@x.com")
        self.assertEqual(self.t.retry_after("1.1.1.1", "a@x.com"), 0)  # the count restarted

    def test_memory_stays_bounded(self):
        with mock.patch.object(login_throttle, "MAX_TRACKED_KEYS", 50):
            for i in range(500):
                self.t.record_failure(f"a{i}", f"u{i}@x.com")
            self.assertLessEqual(len(self.t._events), 50 * 3 + 3)
        self.clock.advance(login_throttle.WINDOW_SECONDS + 1)
        self.t.record_failure("z", "z@x.com")
        with mock.patch.object(login_throttle, "MAX_TRACKED_KEYS", 1):
            self.t.record_failure("y", "y@x.com")
        self.assertLessEqual(len(self.t._events), 6)

    def test_nothing_recorded_means_no_wait(self):
        self.assertEqual(self.t.retry_after("1.1.1.1", ""), 0)
        self.assertEqual(self.t.retry_after("1.1.1.1", None), 0)


class TestLoginTimingEqualised(IsolatedApiTestCase):
    dir_name = "_test_env_level5_timing"

    def setUp(self):
        login_throttle.throttle.reset()

    def test_every_failure_path_does_the_same_password_work(self):
        from backend.app.repositories import users_repo
        org, client = self.org_and_client("Timing")
        email = next(u for u in [users_repo.get_user_by_email(f"{self.dir_name}1@example.com")] if u)["email"]
        real = auth_service.verify_password
        for label, who in (("unknown email", "nobody@example.com"), ("wrong password", email)):
            with mock.patch.object(auth_service, "verify_password", wraps=real) as spy:
                r = client.post("/api/auth/login", json={"email": who, "password": "wrong-password-123"})
            self.assertEqual(r.status_code, 400, label)
            self.assertEqual(spy.call_count, 1, label)
        from backend.app.config.database import db_connection
        from sqlalchemy import text
        with db_connection() as conn:
            conn.execute(text("UPDATE users SET is_active = 0 WHERE email = :e"), {"e": email})
        with mock.patch.object(auth_service, "verify_password", wraps=real) as spy:
            r = client.post("/api/auth/login", json={"email": email, "password": "wrong-password-123"})
        self.assertEqual((r.status_code, spy.call_count), (400, 1), "inactive account")
        self.assertEqual(r.json()["error"], "Incorrect email or password.")

    def test_the_stand_in_hash_is_well_formed_and_never_matches(self):
        dummy = auth_service._dummy_password_hash()
        self.assertEqual(dummy, auth_service._dummy_password_hash())  # made once
        self.assertEqual(len(dummy.split("$")), 4)
        self.assertFalse(auth_service.verify_password("anything", dummy))

    def test_unknown_and_known_emails_now_take_comparable_time(self):
        org, client = self.org_and_client("Timing2")
        email = f"{self.dir_name}1@example.com"

        def median(who):
            xs = []
            for _ in range(3):
                login_throttle.throttle.reset()
                s = time.perf_counter()
                client.post("/api/auth/login", json={"email": who, "password": "wrong-password-123"})
                xs.append(time.perf_counter() - s)
            return sorted(xs)[1]

        known, unknown = median(email), median("nobody@example.com")
        self.assertGreater(unknown, 0.4 * known, f"unknown {unknown:.3f}s vs known {known:.3f}s (was 3 ms vs 224 ms)")


class TestLoginThrottleThroughTheApplication(IsolatedApiTestCase):
    dir_name = "_test_env_level5_login"

    def setUp(self):
        login_throttle.throttle.reset()
        self.clock = FakeClock()
        self._saved = login_throttle.throttle._clock
        login_throttle.throttle._clock = self.clock
        self.addCleanup(lambda: setattr(login_throttle.throttle, "_clock", self._saved))
        self.org, self.client = self.org_and_client("Throttle")
        self.email = f"{self.dir_name}1@example.com"

    def _login(self, password, email=None):
        return self.client.post("/api/auth/login", json={"email": email or self.email, "password": password})

    def test_five_wrong_guesses_then_even_the_right_password_is_refused_with_a_retry_after(self):
        for i in range(5):
            self.assertEqual(self._login(f"wrong-guess-{i}-xxxx").status_code, 400)
        r = self._login("correct-horse-battery-staple")
        self.assertEqual(r.status_code, 429, r.text)
        self.assertRegex(r.headers["Retry-After"], r"^\d+$")
        self.assertGreater(int(r.headers["Retry-After"]), 0)
        self.assertIn("Too many sign-in attempts", r.json()["error"])

    def test_a_refused_attempt_costs_no_password_work(self):
        for i in range(5):
            self._login(f"wrong-guess-{i}-xxxx")
        with mock.patch.object(auth_service, "verify_password") as spy:
            self.assertEqual(self._login("whatever-whatever").status_code, 429)
        spy.assert_not_called()

    def test_waiting_out_the_window_restores_access(self):
        for i in range(5):
            self._login(f"wrong-guess-{i}-xxxx")
        self.clock.advance(login_throttle.WINDOW_SECONDS + 1)
        self.assertEqual(self._login("correct-horse-battery-staple").status_code, 200)

    def test_a_correct_login_resets_the_count_and_other_accounts_are_unaffected(self):
        for i in range(4):
            self._login(f"wrong-guess-{i}-xxxx")
        self.assertEqual(self._login("correct-horse-battery-staple").status_code, 200)
        for i in range(4):
            self.assertEqual(self._login(f"wrong-guess-{i}-yyyy").status_code, 400)  # fresh allowance
        for i in range(5):
            self._login(f"wrong-guess-{i}-zzzz", email="someone-else@example.com")
        self.assertEqual(self._login("correct-horse-battery-staple").status_code, 200)  # my account is untouched by the other one

    def test_the_account_of_another_person_is_not_locked_by_my_failures(self):
        for i in range(5):
            self._login(f"wrong-guess-{i}-xxxx")
        self.assertEqual(self._login("wrong-guess-again-x", email="nobody-yet@example.com").status_code, 400)

    def test_signup_and_other_routes_are_not_throttled(self):
        for i in range(5):
            self._login(f"wrong-guess-{i}-xxxx")
        self.assertEqual(self.client.get("/health").status_code, 200)
        self.assertEqual(self.client.get("/api/auth/me").status_code, 200)  # already signed in; a throttled login does not log it out


# ---------------------------------------------------------------- 3. settings
GOOD = "a" * 40
OTHER = "b" * 40


def _settings(**env):
    base = {"SECRET_KEY": GOOD, "PASSWORD_PEPPER": OTHER, "ENV": "development", "DATABASE_URL": "sqlite:///x.db",
            "CORS_ALLOWED_ORIGIN": "http://localhost:3000", "SESSION_COOKIE_SECURE": "false"}
    base.update(env)
    with mock.patch.dict(os.environ, base, clear=False):
        return Settings()


class TestProductionSecrets(unittest.TestCase):
    def test_short_secrets_are_refused_in_production(self):
        for name in ("SECRET_KEY", "PASSWORD_PEPPER"):
            with self.assertRaises(ConfigError) as cm:
                _settings(ENV="production", SESSION_COOKIE_SECURE="true", **{name: "short-secret"})
            self.assertIn(name, str(cm.exception))
            self.assertIn("at least 32", str(cm.exception))

    def test_the_boundary_is_32_characters(self):
        _settings(ENV="production", SESSION_COOKIE_SECURE="true", SECRET_KEY="k" * 32, PASSWORD_PEPPER="p" * 32)
        with self.assertRaises(ConfigError):
            _settings(ENV="production", SESSION_COOKIE_SECURE="true", SECRET_KEY="k" * 31)

    def test_the_two_secrets_must_differ(self):
        with self.assertRaises(ConfigError) as cm:
            _settings(ENV="production", SESSION_COOKIE_SECURE="true", SECRET_KEY=GOOD, PASSWORD_PEPPER=GOOD)
        self.assertIn("must be different", str(cm.exception))

    def test_development_and_tests_may_keep_short_secrets(self):
        s = _settings(SECRET_KEY="x", PASSWORD_PEPPER="y")
        self.assertFalse(s.is_production)

    def test_the_existing_production_rules_still_apply(self):
        with self.assertRaises(ConfigError) as cm:
            _settings(ENV="production", SESSION_COOKIE_SECURE="false")
        self.assertIn("SESSION_COOKIE_SECURE", str(cm.exception))
        with self.assertRaises(ConfigError):
            _settings(SECRET_KEY="")

    def test_the_new_limits_have_safe_defaults_and_can_be_set(self):
        s = _settings()
        self.assertEqual((s.PARSE_ISOLATION, s.PARSE_TIMEOUT_SECONDS, s.PARSE_MEMORY_MB, s.MAX_CONCURRENT_PARSES), (True, 30, 2048, 2))
        self.assertEqual((s.MAX_ARCHIVE_UNCOMPRESSED_BYTES, s.MAX_ARCHIVE_ENTRIES), (100 * 1024 * 1024, 2000))
        s = _settings(PARSE_ISOLATION="false", PARSE_TIMEOUT_SECONDS="7")
        self.assertEqual((s.PARSE_ISOLATION, s.PARSE_TIMEOUT_SECONDS), (False, 7))


class TestDeploymentWarning(unittest.TestCase):
    def test_a_postgres_or_public_origin_outside_production_logs_a_warning(self):
        for env in ({"DATABASE_URL": "postgresql+psycopg2://u:p@h/db"}, {"CORS_ALLOWED_ORIGIN": "https://app.example.com"}):
            with self.assertLogs("backend.app.config.settings", level="WARNING") as cm:
                _settings(**env)
            self.assertIn("Set ENV=production", cm.output[0])
            self.assertNotIn("p@h", cm.output[0])  # never print the connection string

    def test_local_development_and_production_stay_quiet(self):
        logger = logging.getLogger("backend.app.config.settings")
        with mock.patch.object(logger, "warning") as warn:
            _settings()
            _settings(ENV="production", SESSION_COOKIE_SECURE="true", DATABASE_URL="postgresql://u:p@h/db")
            _settings(CORS_ALLOWED_ORIGIN="http://127.0.0.1:3000")
        warn.assert_not_called()

    def test_looks_like_a_deployment(self):
        self.assertFalse(_settings().looks_like_a_deployment)
        self.assertTrue(_settings(CORS_ALLOWED_ORIGIN="https://x.example").looks_like_a_deployment)
        self.assertTrue(_settings(DATABASE_URL="postgresql://u:p@h/db").looks_like_a_deployment)


# ---------------------------------------------------------------- 4. errors and headers
def _tiny_app(production: bool) -> TestClient:
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)
    register_error_handlers(app)

    @app.get("/api/index")
    def index():
        return [][3]

    @app.get("/api/key")
    def key():
        return {}["secret_field"]

    @app.get("/api/lookup")
    def lookup():
        raise LookupError("Change case not found.")

    @app.get("/api/limited")
    def limited():
        raise TooManyAttempts(42)

    @app.get("/api/boom")
    def boom():
        raise RuntimeError("password=hunter2 in a stack trace")

    @app.get("/api/own-cache")
    def own_cache():
        from fastapi.responses import JSONResponse
        return JSONResponse({"ok": True}, headers={"Cache-Control": "max-age=60"})

    @app.get("/public")
    def public():
        return {"ok": True}

    return TestClient(app, raise_server_exceptions=False)


class TestErrorDetail(unittest.TestCase):
    def test_an_accidental_index_error_is_a_500_not_a_404_with_python_text(self):
        for production in (True, False):
            with mock.patch.object(type(settings), "is_production", new_callable=mock.PropertyMock, return_value=production):
                r = _tiny_app(production).get("/api/index")
            self.assertEqual(r.status_code, 500)
            self.assertEqual("dev_detail" in r.json(), not production)
            if production:
                self.assertEqual(r.json(), {"error": "Something went wrong. Please try again."})
                self.assertNotIn("index", r.text)

    def test_intended_not_found_and_other_handlers_are_unchanged(self):
        c = _tiny_app(True)
        self.assertEqual((c.get("/api/lookup").status_code, c.get("/api/lookup").json()), (404, {"error": "Change case not found."}))
        self.assertEqual(c.get("/api/key").status_code, 500)
        with mock.patch.object(type(settings), "is_production", new_callable=mock.PropertyMock, return_value=True):
            self.assertNotIn("hunter2", _tiny_app(True).get("/api/boom").text)

    def test_the_429_shape(self):
        r = _tiny_app(True).get("/api/limited")
        self.assertEqual((r.status_code, r.headers["Retry-After"]), (429, "42"))
        self.assertEqual(r.json(), {"error": "Too many attempts. Please wait and try again."})
        self.assertEqual(TooManyAttempts(0).retry_after_seconds, 1)
        self.assertEqual(TooManyAttempts(-5).retry_after_seconds, 1)


class TestCacheControl(unittest.TestCase):
    def test_api_responses_are_not_cacheable_and_other_paths_are_untouched(self):
        c = _tiny_app(True)
        self.assertEqual(c.get("/api/lookup").headers["Cache-Control"], "no-store")
        self.assertEqual(c.get("/api/index").headers["Cache-Control"], "no-store")   # a handled 500
        self.assertEqual(c.get("/api/limited").headers["Cache-Control"], "no-store")  # and the 429
        self.assertNotIn("Cache-Control", c.get("/public").headers)

    def test_a_route_that_sets_its_own_cache_policy_keeps_it(self):
        self.assertEqual(_tiny_app(True).get("/api/own-cache").headers["Cache-Control"], "max-age=60")

    def test_the_existing_headers_are_still_sent(self):
        h = _tiny_app(True).get("/api/lookup").headers
        self.assertEqual((h["X-Content-Type-Options"], h["X-Frame-Options"]), ("nosniff", "DENY"))


class TestReportFileDoesNotLinger(IsolatedApiTestCase):
    dir_name = "_test_env_level5_report"

    def test_the_generated_document_is_removed_after_it_has_been_sent(self):
        from backend.app.services import report_builder
        org_id, client, case_id = self.setup_case("Linger")
        self.paid(org_id)
        self.assertEqual(self.upload(client, case_id, "h.csv", F.csv_bytes(F.CANON_HEADERS, F.base_rows(14))).status_code, 201)
        client.post(f"/api/change-cases/{case_id}/candidates", json={"name": "InA", "features": {"crosslinker_ratio": 0.12, "cure_temp_c": 158}})
        self.assertEqual(client.post(f"/api/change-cases/{case_id}/rank").status_code, 200)
        before = set(os.listdir(report_builder.REPORTS_ROOT))
        r = client.post(f"/api/change-cases/{case_id}/report")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.headers["Cache-Control"], "no-store")
        self.assertGreater(len(Document(io.BytesIO(r.content)).paragraphs), 10)  # the download itself is intact
        self.assertEqual(set(os.listdir(report_builder.REPORTS_ROOT)), before, "a copy of the customer's report was left on disk")

    def test_a_failed_removal_is_logged_not_raised(self):
        from backend.app.api.routes import change_case_routes
        logger = logging.getLogger("rdopt")
        was_disabled, logger.disabled = logger.disabled, False  # alembic's fileConfig() may have disabled it in this process
        self.addCleanup(setattr, logger, "disabled", was_disabled)
        with self.assertLogs("rdopt", level="WARNING") as cm:
            change_case_routes._remove_generated_file("/nonexistent/dir/report.docx")
        self.assertIn("could not remove generated report report.docx", cm.output[0])
        self.assertNotIn("/nonexistent", cm.output[0])


# ---------------------------------------------------------------- 5. dependencies
class TestDependencyPins(unittest.TestCase):
    def _lines(self, name):
        with open(os.path.join(ROOT, "backend", name), encoding="utf-8") as f:
            return [l.strip() for l in f if l.strip() and not l.startswith("#")]

    def test_direct_imports_that_were_only_installed_by_accident_are_now_named(self):
        names = {re.split(r"[=\[]", l)[0].lower() for l in self._lines("requirements.txt")}
        for needed in ("python-dotenv", "httpx", "openpyxl", "pdfplumber", "python-docx", "sqlalchemy", "alembic"):
            self.assertIn(needed, names)

    def test_every_requirement_is_pinned_exactly_and_agrees_with_the_constraints(self):
        pinned = {}
        for l in self._lines("requirements.txt"):
            self.assertRegex(l, r"^[A-Za-z0-9_.\-]+(\[[a-z,]+\])?==[0-9][^\s]*$", l)
            pinned[re.split(r"[=\[]", l)[0].lower().replace("_", "-")] = l.split("==")[1]
        constraints = {}
        for l in self._lines("constraints.txt"):
            self.assertRegex(l, r"^[A-Za-z0-9_.\-]+==[0-9][^\s]*$", l)
            constraints[l.split("==")[0].lower().replace("_", "-")] = l.split("==")[1]
        for name, version in pinned.items():
            self.assertEqual(constraints.get(name), version, f"{name}: requirements.txt says {version}, constraints say {constraints.get(name)}")
        self.assertGreater(len(constraints), len(pinned), "the constraints must also pin the transitive packages")



class TestDeploymentGuide(unittest.TestCase):
    """The notes must keep naming the settings the code actually enforces."""

    def setUp(self):
        with open(os.path.join(ROOT, "DEPLOYMENT.md"), encoding="utf-8") as f:
            raw = f.read()
        self.text = " ".join(raw.split())  # one line: the file is wrapped for reading
        self.lower = self.text.lower()

    def test_it_names_the_enforced_production_settings(self):
        for needed in ("ENV", "production", "SECRET_KEY", "PASSWORD_PEPPER", "at least 32", "SESSION_COOKIE_SECURE",
                       "CORS_ALLOWED_ORIGIN", "--proxy-headers", "--forwarded-allow-ips", "one worker process"):
            self.assertIn(needed.lower(), self.lower, needed)

    def test_the_documented_defaults_match_the_code(self):
        s = _settings()
        for name in ("PARSE_TIMEOUT_SECONDS", "PARSE_MEMORY_MB", "MAX_CONCURRENT_PARSES", "MAX_ARCHIVE_UNCOMPRESSED_BYTES", "MAX_ARCHIVE_ENTRIES",
                     "PARSE_MAX_OUTPUT_MB"):
            self.assertIn(f"`{name}={getattr(s, name)}`", self.text, name)
        self.assertIn(f"{login_throttle.LIMIT_ADDRESS_EMAIL} failures per address and account per 15 minutes, {login_throttle.LIMIT_ADDRESS} per address, {login_throttle.LIMIT_EMAIL} per account", self.text)
        self.assertEqual(login_throttle.WINDOW_SECONDS, 15 * 60)
        self.assertEqual(settings_module.MIN_PRODUCTION_SECRET_LENGTH, 32)

    def test_the_platform_guarantees_are_stated_honestly(self):
        for needed in ("finishes **or** is stopped", "starts its own session escapes the group kill", "setsid", "refuses the file",
                       "still reserved", "WNOWAIT", "processes the parser starts",
                       "Run production on Linux", "macOS", "Windows is not supported", "not enforce", "PARSE_MAX_OUTPUT_MB",
                       "process group", "cgroup", "requested, **not run on macOS**", "none** (no `resource` module)"):
            self.assertIn(needed, self.text, needed)
        # what the code actually does on each platform must match the table
        import resource
        from backend.app.services import parse_worker
        with open(parse_worker.__file__, encoding="utf-8") as f:
            source = f.read()
        self.assertIn('sys.platform.startswith("linux")', source)  # the memory limit is Linux-only
        self.assertIn("RLIMIT_CPU", source)
        self.assertTrue(hasattr(resource, "RLIMIT_AS") and hasattr(resource, "RLIMIT_CPU"))

    def test_it_documents_backups_of_both_stores_and_says_what_was_not_verified(self):
        for needed in ("pg_dump", "pg_restore", "data/qualification_uploads", "data/uploads", "reports/generated", "together",
                       "Not verified", "Verified"):
            self.assertIn(needed, self.text, needed)
        for path in (os.path.join("data", "qualification_uploads"), os.path.join("data", "uploads")):
            self.assertTrue(path in self.text.replace("/", os.sep))
        from backend.app.services import dataset_service, qualification_dataset_service
        self.assertTrue(qualification_dataset_service.UPLOAD_ROOT.endswith(os.path.join("data", "qualification_uploads")))
        self.assertTrue(dataset_service.UPLOAD_ROOT.endswith(os.path.join("data", "uploads")))


if __name__ == "__main__":
    unittest.main()
