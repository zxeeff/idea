"""Single-host ownership for an IDEA run."""

from __future__ import annotations

import os
from pathlib import Path


class RunLock:
    """An OS-owned lock; stale files are harmless and are never PID leases."""

    def __init__(self, state_dir: Path, run_id: str):
        self.path = state_dir / "runs" / run_id / "execution.lock"
        self.fd: int | None = None

    def __enter__(self) -> "RunLock":
        import fcntl

        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            raise RuntimeError(
                "this run already has a launcher or live provider process; "
                "stop it before resuming the same run"
            ) from None
        self.fd = fd
        return self

    def __exit__(self, *args: object) -> None:
        if self.fd is not None:
            # Do not unlock explicitly: a child may still hold this inherited
            # open-file description while its provider process is alive.
            os.close(self.fd)
            self.fd = None
