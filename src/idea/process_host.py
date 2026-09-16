"""Own one provider process group and retain IDEA's inherited run lock.

This is a standalone stdlib program, launched with isolated Python. Provider
arguments, environment, and filesystem permissions are passed through unchanged.
The provider does not inherit the lock descriptor; this trusted host owns it.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys


def _publish_result(path: Path, exit_code: int) -> None:
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump({"exit_code": exit_code}, handle, separators=(",", ":"))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def main() -> None:
    # The parent creates a new session for this host. Refuse to signal a process
    # group shared with a caller if this program is invoked incorrectly.
    if os.getpgrp() != os.getpid():
        raise RuntimeError("IDEA process host requires its own process group")
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    if len(sys.argv) < 3 or sys.argv[2] != "--":
        raise ValueError("expected RESULT_PATH -- PROVIDER_COMMAND")
    result_path = Path(sys.argv[1])
    command = sys.argv[3:]
    exit_code = 127
    try:
        if not command:
            raise ValueError("provider command is empty")
        # Reset ignored termination signals in the actual CLI without using
        # preexec_fn. Python Popen restore_signals only resets SIGPIPE/SIGXFSZ,
        # so a tiny second host mode performs the reset immediately before exec.
        process = subprocess.Popen(
            [sys.executable, "-I", str(Path(__file__).resolve()), "--exec", *command],
            close_fds=True,
        )
        exit_code = process.wait()
    except (OSError, ValueError) as error:
        print(f"launcher error: {error}", file=sys.stderr, flush=True)
    finally:
        try:
            _publish_result(result_path, exit_code)
        finally:
            # The CLI has exited, but its tools can still hold stdout open. The
            # host ignores TERM, then kills the group including itself while it
            # still owns the run-lock FD. No third-party FD retention is assumed.
            os.killpg(os.getpid(), signal.SIGTERM)
            os.killpg(os.getpid(), signal.SIGKILL)


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "--exec":
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        try:
            os.execvpe(sys.argv[2], sys.argv[2:], os.environ)
        except (IndexError, OSError) as error:
            print(f"launcher error: {error}", file=sys.stderr, flush=True)
            raise SystemExit(127)
    main()
