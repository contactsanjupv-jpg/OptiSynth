"""Child-process entry point: parses ONE untrusted file under resource limits and prints one JSON result.

Run only by services/sandboxed_parse.py:   python -m backend.app.services.parse_worker KIND MEMORY_BYTES CPU_SECONDS LIMITS_JSON
The file's bytes arrive on stdin. The process holds no real secrets and no database connection (the parent passes a
stripped environment), so a parser bug triggered by a hostile file has little to reach.
"""
import json
import sys


def _apply_limits(memory_bytes: int, cpu_seconds: int) -> None:
    try:
        import resource
    except ImportError:  # not available on this platform: the parent's wall-clock timeout still applies
        return
    for limit, value in ((resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 5)),):
        try:
            resource.setrlimit(limit, value)
        except (ValueError, OSError):
            pass
    if sys.platform.startswith("linux"):  # macOS does not enforce an address-space limit
        try:
            resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
        except (ValueError, OSError):
            pass


def main() -> int:
    kind, memory_bytes, cpu_seconds = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
    limits = json.loads(sys.argv[4]) if len(sys.argv) > 4 else {}
    data = sys.stdin.buffer.read()
    _apply_limits(memory_bytes, cpu_seconds)
    from backend.app.schemas.errors import ValidationError
    from backend.app.services import evidence_extraction
    for name, value in limits.items():  # the parent's current limits, so an override there applies here
        if name in evidence_extraction.LIMIT_NAMES:
            setattr(evidence_extraction, name, int(value))
    try:
        tables, warnings = evidence_extraction.parse_binary(kind, data)
        out = {"ok": {"tables": tables, "warnings": warnings}}
    except ValidationError as err:
        out = {"validation": err.message}
    except MemoryError:
        out = {"too_heavy": "memory"}
    except Exception as err:  # a parser bug: report the type only, never file contents
        out = {"error": type(err).__name__}
    sys.stdout.write(json.dumps(out))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
