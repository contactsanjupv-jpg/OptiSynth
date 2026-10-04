"""
Writes the adversarial evidence fixtures (XLSX / DOCX / PDF files from different
imaginary customers) to a folder, so an operator can try the evidence review
screen against them.

    python3 scripts/make_evidence_fixtures.py ./fixtures_out

The files are for the case defined as: input features crosslinker_ratio,
cure_temp_c; target salt_spray_hours (maximize). Each file name says what it
is meant to exercise. They are SYNTHETIC test inputs, not customer data.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.tests import evidence_fixtures  # noqa: E402


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print("usage: python3 scripts/make_evidence_fixtures.py <output folder>")
        return 1
    out = argv[0]
    os.makedirs(out, exist_ok=True)
    for name, build in evidence_fixtures.ALL_FIXTURES.items():
        with open(os.path.join(out, name), "wb") as f:
            f.write(build())
    print(f"Wrote {len(evidence_fixtures.ALL_FIXTURES)} fixture files to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
