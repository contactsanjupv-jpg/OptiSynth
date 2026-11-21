"""Writing standard for the customer interface: natural wording, sentence case, no decorative separators.
These checks read the frontend source, so they run with the backend suite.
Run:  python3 -m unittest backend.tests.test_ui_copy -v"""
import os
import re
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "frontend"))
BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
LINE_COMMENT = re.compile(r"(?<![:\w\"'`])//.*$", re.M)
TITLE_PROP = re.compile(r'\b(?:title|label|header)="([^"]+)"')
NOT_TITLE_CASE_OK = {"OptiSynth", "SHA", "CSV", "XLSX", "PDF", "DOCX", "ID", "API"}


def sources():
    for base in ("app", "components"):
        for folder, _, files in os.walk(os.path.join(ROOT, base)):
            for name in files:
                if name.endswith(".tsx"):
                    path = os.path.join(folder, name)
                    with open(path, encoding="utf-8") as f:
                        text = f.read()
                    yield os.path.relpath(path, ROOT), LINE_COMMENT.sub("", BLOCK_COMMENT.sub("", text))


class TestCustomerCopy(unittest.TestCase):
    def offenders(self, pattern):
        rx = re.compile(pattern)
        return [f"{path}: {m.group(0)!r}" for path, text in sources() for m in rx.finditer(text)]

    def test_no_dashes_or_separators_between_phrases(self):
        for label, pattern in (("em dash", "\u2014"), ("en dash", "\u2013"), ("double hyphen", r" -- "), ("middle dot", r" \u00b7 ")):
            self.assertEqual(self.offenders(pattern), [], label)

    def test_headings_and_labels_use_sentence_case(self):
        bad = []
        for path, text in sources():
            for m in TITLE_PROP.finditer(text):
                words = m.group(1).split()
                if len(words) >= 2 and all(w[:1].isupper() for w in words[:2]) and not (set(words[:2]) & NOT_TITLE_CASE_OK):
                    bad.append(f"{path}: {m.group(1)!r}")
        self.assertEqual(bad, [])

    def test_headings_do_not_end_with_a_period(self):
        bad = [f"{p}: {m.group(1)!r}" for p, t in sources() for m in re.finditer(r'\btitle="([^"]+)"', t) if m.group(1).endswith(".")]
        self.assertEqual(bad, [])

    def test_pages_use_the_auth_hook_correctly(self):
        # useRequireAuth returns an object. Treating it as a boolean leaves a page blank, so require the destructured form.
        self.assertEqual(self.offenders(r"const checking = useRequireAuth\(\)"), [])

    def test_the_customer_interface_says_program_not_diagnostic(self):
        found = []
        for base in ("app", "components", "lib"):
            for folder, _, files in os.walk(os.path.join(ROOT, base)):
                for name in files:
                    if name.endswith((".ts", ".tsx")):
                        with open(os.path.join(folder, name), encoding="utf-8") as f:
                            if re.search(r"diagnostic", f.read(), re.I):
                                found.append(os.path.join(folder, name))
        self.assertEqual(found, [])


if __name__ == "__main__":
    unittest.main()
