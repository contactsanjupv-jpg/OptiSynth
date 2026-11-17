"""Reads an uploaded .xlsx / .docx / .pdf in a separate, resource-limited process.

Why: these formats are compressed containers. A file well under the upload limit can expand while it is parsed into
gigabytes of memory or hours of CPU (millions of empty spreadsheet rows, millions of empty Word paragraphs, a PDF page
stream that inflates enormously); measured in review, a 51 KB PDF was enough to exhaust 8 GB. Doing that inside the web
process would let any signed-in user take the whole API down. Here it costs one child process: it is killed at the
wall-clock limit, capped on CPU and (on Linux) memory, only a few run at a time, and it receives no secrets.

The result crosses the process boundary as JSON, never pickle, so a compromised child cannot make the parent execute
anything. What comes back is also bounded: the parent reads the child's output in small pieces, kills the child the moment
the answer passes PARSE_MAX_OUTPUT_MB, and keeps only the start of its error output (subprocess.run(capture_output=True)
held everything: a child writing 400 MB made the web process grow by 800 MB).

Lifecycle: when the parser exits (normally or not) or is stopped, everything it started is killed too, then its pipes are
drained for at most DRAIN_SECONDS. Without that, a descendant that inherited the pipes kept the web process waiting for as
long as it lived, holding a parser slot, and the parser's finished answer was not even delivered until it ended.
"""
import json
import logging
import os
import select
import signal
import subprocess
import sys
import threading
import time

from backend.app.config.settings import settings
from backend.app.schemas.errors import ValidationError

TOO_HEAVY_MESSAGE = (
    "This file is too large or complex to read safely (it needed too much time or memory). Export just the table you "
    "need to a CSV file, or upload a smaller workbook, document or PDF."
)
OUTPUT_TOO_LARGE_MESSAGE = (
    "This file holds far more table data than can be read safely (a single cell, or the table as a whole, is "
    "extremely large). Export just the table you need to a CSV file, or remove the oversized content."
)
BUSY_MESSAGE = "The server is busy reading other files. Please try again in a moment."
QUEUE_WAIT_SECONDS = 15
CHUNK_BYTES = 64 * 1024
POLL_SECONDS = 0.1          # how often a reader re-checks whether it has been told to stop
LEADER_POLL_SECONDS = 0.005  # how often the parser's exit is checked
DRAIN_SECONDS = 2.0         # after the parser is gone, how long to wait for its pipes to reach end-of-stream
MAX_STDERR_BYTES = 64 * 1024  # only used to tell how the process ended; its content is never shown or logged
MAX_MESSAGE_CHARS = 500       # a message the child asks us to show a customer
logger = logging.getLogger("rdopt")

_slots = None
_slots_size = None
_slots_lock = threading.Lock()


def _semaphore() -> threading.BoundedSemaphore:
    global _slots, _slots_size
    with _slots_lock:
        if _slots is None or _slots_size != settings.MAX_CONCURRENT_PARSES:
            _slots, _slots_size = threading.BoundedSemaphore(max(1, settings.MAX_CONCURRENT_PARSES)), settings.MAX_CONCURRENT_PARSES
        return _slots


def child_environment() -> dict:
    """What the parser process may see: how to find the code, and the row limit. No real secrets, no database."""
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": os.pathsep.join(p for p in sys.path if p),
        "PYTHONDONTWRITEBYTECODE": "1",
        "SECRET_KEY": "parser-process-holds-no-secret",
        "PASSWORD_PEPPER": "parser-process-holds-no-pepper",
        "DATABASE_URL": "sqlite://",
        "MAX_UPLOAD_ROWS": str(settings.MAX_UPLOAD_ROWS),
        "MAX_ARCHIVE_UNCOMPRESSED_BYTES": str(settings.MAX_ARCHIVE_UNCOMPRESSED_BYTES),
        "MAX_ARCHIVE_ENTRIES": str(settings.MAX_ARCHIVE_ENTRIES),
        "PARSE_ISOLATION": "false",  # the child parses directly; it must not start another child
    }
    if os.name == "nt":  # Python cannot start on Windows without these
        for name in ("SYSTEMROOT", "SystemRoot", "TEMP", "TMP"):
            if name in os.environ:
                env[name] = os.environ[name]
    return env


class Outcome:
    """What a parser process did, with its output held to a fixed size."""

    def __init__(self, returncode, stdout, stderr, timed_out, output_too_large, pipes_held_open=False):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr
        self.timed_out, self.output_too_large = timed_out, output_too_large
        # True when something outside the parser's process group still held its output pipes after the parser was gone
        # and its group was killed (a process that deliberately left the group). That cannot happen from a well-behaved parser.
        self.pipes_held_open = pipes_held_open


# Where the OS can report that a child has exited WITHOUT reaping it (Linux), the parser's process-group id stays
# reserved until we have killed the group, so the kill can never reach an unrelated process that reused the number.
_CAN_PEEK_EXIT = hasattr(os, "waitid") and hasattr(os, "WNOWAIT") and hasattr(os, "WNOHANG")


def _leader_exited(proc: subprocess.Popen) -> bool:
    """True once the parser process itself has exited (it is NOT reaped here when the OS lets us avoid that)."""
    if _CAN_PEEK_EXIT:
        try:
            return os.waitid(os.P_PID, proc.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None
        except ChildProcessError:  # already reaped elsewhere
            return True
    return proc.poll() is not None


def _kill_group(proc: subprocess.Popen) -> None:
    """SIGKILL the parser and every process it started.

    On POSIX the parser is the leader of its own session and process group (start_new_session), so signalling that group
    reaches its descendants and nothing else. It is done while the leader is still unreaped where the OS allows (see
    _CAN_PEEK_EXIT); a group with no members left answers ProcessLookupError and nothing is signalled. A process that
    deliberately leaves the group (setsid) cannot be reached this way: see pipes_held_open and DEPLOYMENT.md."""
    if os.name == "posix":
        try:
            os.killpg(proc.pid, signal.SIGKILL)
            return
        except ProcessLookupError:
            return
        except OSError:
            pass
    try:
        proc.kill()
    except OSError:
        pass


def _read_some(stream, stop: threading.Event):
    """One piece of output: bytes (b"" means end of stream) or None when told to stop. On POSIX it waits in short slices
    and re-checks `stop`, so a reader can never be stuck behind a pipe that a stray process keeps open."""
    if os.name != "posix":
        return stream.read(CHUNK_BYTES)  # Windows is unsupported (DEPLOYMENT.md): plain blocking read
    fd = stream.fileno()
    while not stop.is_set():
        ready, _, _ = select.select([fd], [], [], POLL_SECONDS)
        if ready:
            return os.read(fd, CHUNK_BYTES)
    return None


def _run_bounded(cmd, data: bytes, timeout: float, env: dict, max_stdout: int, max_stderr: int) -> Outcome:
    """Runs `cmd` with `data` on stdin and returns its output, never holding more than max_stdout + max_stderr bytes.

    Guarantees, whatever the parser does:
      * memory: stdout is read in small pieces and the parser is killed the moment it passes max_stdout; stderr is drained
        but only its first max_stderr bytes are kept (subprocess.run(capture_output=True) kept everything: 400 MB written
        became +800 MB in the web process);
      * time: the parser is stopped at `timeout`; after it exits (or is stopped) everything it started is killed, and the
        pipes are drained for at most DRAIN_SECONDS, so this function returns in bounded time even if a descendant holds
        the pipes (before: it blocked for as long as the descendant lived, holding a parser slot);
      * results: output the parser wrote before exiting is kept, however long a descendant lingers.
    """
    kwargs = {"stdin": subprocess.PIPE, "stdout": subprocess.PIPE, "stderr": subprocess.PIPE, "env": env,
              "close_fds": True, "bufsize": 0}  # unbuffered: data is delivered as it is written, not when 64 KB accumulate
    if os.name == "posix":
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(cmd, **kwargs)
    out, err = bytearray(), bytearray()
    flags = {"too_large": False}
    stop = threading.Event()
    ended = {"stdout": False, "stderr": False}  # reached end-of-stream (every writer has closed it)

    def read_stdout():
        try:
            while True:
                chunk = _read_some(proc.stdout, stop)
                if chunk is None:
                    return
                if not chunk:
                    ended["stdout"] = True
                    return
                out.extend(chunk)
                if len(out) > max_stdout:
                    flags["too_large"] = True
                    return
        except (OSError, ValueError):
            ended["stdout"] = True

    def read_stderr():
        try:
            while True:
                chunk = _read_some(proc.stderr, stop)
                if chunk is None:
                    return
                if not chunk:
                    ended["stderr"] = True
                    return
                if len(err) < max_stderr:
                    err.extend(chunk[: max_stderr - len(err)])
        except (OSError, ValueError):
            ended["stderr"] = True

    def feed_stdin():
        try:
            if os.name != "posix":
                proc.stdin.write(data)
                return
            fd = proc.stdin.fileno()
            os.set_blocking(fd, False)
            view, sent = memoryview(data), 0
            while sent < len(view) and not stop.is_set():
                try:
                    sent += os.write(fd, view[sent: sent + CHUNK_BYTES])
                except BlockingIOError:
                    select.select([], [fd], [], POLL_SECONDS)
        except (BrokenPipeError, OSError, ValueError):  # the parser exited or was killed before reading it all
            pass
        finally:
            try:
                proc.stdin.close()
            except (BrokenPipeError, OSError, ValueError):
                pass

    readers = [threading.Thread(target=read_stdout, daemon=True), threading.Thread(target=read_stderr, daemon=True)]
    feeder = threading.Thread(target=feed_stdin, daemon=True)
    threads = readers + [feeder]
    for t in threads:
        t.start()

    timed_out = False
    try:
        deadline = time.monotonic() + timeout
        while not (flags["too_large"] or _leader_exited(proc)):
            if time.monotonic() >= deadline:
                timed_out = True
                break
            time.sleep(LEADER_POLL_SECONDS)
    finally:
        # Always, not only on timeout or overflow: a parser that exits normally can still have left a process behind that
        # holds the output pipes. The leader is still unreaped here where the OS allows, so the group id is still ours.
        _kill_group(proc)
        proc.wait()
        drain_until = time.monotonic() + DRAIN_SECONDS
        for t in readers:
            t.join(timeout=max(0.0, drain_until - time.monotonic()))
        held_open = any(t.is_alive() for t in readers)
        stop.set()  # readers and the feeder notice within POLL_SECONDS
        for t in threads:
            t.join(timeout=5)
        for pipe in (proc.stdout, proc.stderr, proc.stdin):
            try:
                pipe.close()
            except (OSError, ValueError):
                pass
    return Outcome(proc.returncode, out, err, timed_out, flags["too_large"], pipes_held_open=held_open)


def parse(kind: str, data: bytes, limits: dict = None):
    """-> (tables, warnings), exactly what evidence_extraction.parse_binary returns, computed in a child process."""
    slots = _semaphore()
    if not slots.acquire(timeout=QUEUE_WAIT_SECONDS):
        raise ValidationError(BUSY_MESSAGE)
    try:
        timeout = settings.PARSE_TIMEOUT_SECONDS
        cmd = [sys.executable, "-m", "backend.app.services.parse_worker", kind,
               str(settings.PARSE_MEMORY_MB * 1024 * 1024), str(timeout), json.dumps(limits or {})]
        done = _run_bounded(cmd, data, timeout + 5, child_environment(),
                            max_stdout=settings.PARSE_MAX_OUTPUT_MB * 1024 * 1024, max_stderr=MAX_STDERR_BYTES)
    finally:
        slots.release()
    if done.output_too_large:
        raise ValidationError(OUTPUT_TOO_LARGE_MESSAGE)
    if done.pipes_held_open:  # something outside the parser's process group is still holding its output: fail closed
        logger.warning("parser output pipes were still held open after the parser and its process group were killed")
        raise ValidationError(TOO_HEAVY_MESSAGE)
    if done.timed_out or done.returncode != 0:  # out of time, killed (memory, CPU limit) or crashed before it could answer
        if done.returncode not in (0, None):
            logger.warning("parser process ended abnormally (exit status %s, %d bytes on stderr)", done.returncode, len(done.stderr))
        raise ValidationError(TOO_HEAVY_MESSAGE)
    try:
        out = json.loads(done.stdout)
    except ValueError:
        raise ValidationError(TOO_HEAVY_MESSAGE)
    if not isinstance(out, dict):
        raise ValidationError(TOO_HEAVY_MESSAGE)
    if "ok" in out:
        result = out["ok"]
        if not isinstance(result, dict):
            raise ValidationError(TOO_HEAVY_MESSAGE)

        tables = result.get("tables")
        warnings = result.get("warnings")

        if not isinstance(tables, list) or not all(isinstance(t, dict) for t in tables):
            raise ValidationError(TOO_HEAVY_MESSAGE)

        if not isinstance(warnings, list) or not all(isinstance(w, str) for w in warnings):
            raise ValidationError(TOO_HEAVY_MESSAGE)

        return tables, warnings

    if "validation" in out:
        raise ValidationError(str(out["validation"])[:MAX_MESSAGE_CHARS])
    if "too_heavy" in out:
        raise ValidationError(TOO_HEAVY_MESSAGE)
    raise RuntimeError(f"file parser failed ({str(out.get('error', 'unknown error'))[:60]})")