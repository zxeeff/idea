"""Bounded, file-based RPC between a workspace and its trusted launcher.

The launcher owns the command dispatcher and the shared database. A peer only
writes its own mailbox. Requests are journalled before dispatch: retries reuse
the saved response, while a crash during dispatch leaves an explicit unknown
outcome instead of executing a potentially committed mutation twice.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import stat
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

from .forum import Forum, _now


MAX_BYTES = 8 * 1024 * 1024
MAX_ATTACHMENT_BYTES = 4 * 1024 * 1024
DEFAULT_COMMANDS = frozenset({
    "peers", "recent", "search", "read", "changes", "inbox", "discover", "follow",
    "unfollow", "following", "post", "reply", "reply-trigger", "retire", "attach",
    "approach", "approaches", "read-approach", "join", "leave", "members",
    "report", "reports", "read-report", "adopt",
})
_FORBIDDEN_COMMANDS = frozenset({"integrate", "run", "resume", "serve", "doctor"})
_IDENTITY_FIELDS = frozenset({
    "agent_id", "agent-id", "author", "run_id", "run", "state_dir", "state-dir",
    "workspace", "bridge_dir",
})
_PRIVATE_FIELDS = frozenset({
    "pid", "session_id", "session", "session_token", "password", "api_key",
    "stored_path", "patch_path", "log_path", "workspace", "state_dir", "bridge_dir",
    "state_path", "workspace_path", "run_dir", "log_dir", "shared_state_dir",
    "last_activity_id", "last_wake_scan_id",
})
_REQUEST_ID = re.compile(r"^[a-f0-9]{32}$")
_REQUEST_FILE = re.compile(r"^([a-f0-9]{32})\.json$")
_NOFOLLOW = os.O_NOFOLLOW | os.O_NONBLOCK


class BridgeError(RuntimeError):
    """A public transport/validation error with no internal filesystem details."""


class BridgeRemoteError(BridgeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class BridgeTimeoutError(BridgeError):
    def __init__(self, request_id: str):
        super().__init__(
            f"forum request timed out; retry with request_id={request_id} to retrieve "
            "its outcome without submitting the operation again"
        )
        self.request_id = request_id


def _json_bytes(value: Any, maximum: int) -> bytes:
    _validate_json(value)
    try:
        data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    except (TypeError, ValueError, UnicodeError, RecursionError) as error:
        raise BridgeError("request or response must contain valid JSON") from error
    if len(data) > maximum:
        raise BridgeError("request or response exceeds the bridge size limit")
    return data


def _validate_json(value: Any, depth: int = 0) -> None:
    if depth > 24:
        raise BridgeError("JSON nesting exceeds the bridge depth limit")
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise BridgeError("JSON object keys must be strings")
            _validate_json(item, depth + 1)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _validate_json(item, depth + 1)
    elif value is not None and not isinstance(value, (str, bool, int, float)):
        raise BridgeError("request or response must contain valid JSON")


def _decode_json(data: bytes) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def constant(value: str) -> None:
        raise ValueError("non-finite JSON number")

    try:
        result = json.loads(data, object_pairs_hook=pairs, parse_constant=constant)
        _validate_json(result)
        return result
    except (UnicodeError, ValueError, RecursionError) as error:
        raise BridgeError("invalid bridge JSON") from error


def _public_result(value: Any, depth: int = 0) -> Any:
    if depth > 24:
        raise BridgeError("JSON nesting exceeds the bridge depth limit")
    if isinstance(value, dict):
        return {
            key: _public_result(item, depth + 1)
            for key, item in value.items()
            if key not in _PRIVATE_FIELDS
        }
    if isinstance(value, (tuple, list)):
        return [_public_result(item, depth + 1) for item in value]
    return value


def _validate_call(command: Any, payload: Any, allowed: Iterable[str]) -> None:
    if not isinstance(command, str) or command not in allowed or command in _FORBIDDEN_COMMANDS:
        raise BridgeError("command is not available through the peer bridge")
    if not isinstance(payload, dict):
        raise BridgeError("bridge payload must be a JSON object")
    if _IDENTITY_FIELDS.intersection(payload):
        raise BridgeError("peer identity and run are fixed by the launcher")
    if command == "attach":
        if "path" in payload or "stored_path" in payload:
            raise BridgeError("attachments must carry file contents, not a filesystem path")
        name = payload.get("filename")
        try:
            name_size = len(name.encode()) if isinstance(name, str) else 0
        except UnicodeError as error:
            raise BridgeError("attachment filename must be valid UTF-8") from error
        if (
            not isinstance(name, str) or not name or name in {".", ".."}
            or name_size > 255 or any(character in name for character in ("/", "\\", "\0"))
        ):
            raise BridgeError("attachment filename must be a single basename")
        contents = payload.get("data_base64")
        if not isinstance(contents, str) or len(contents) > ((MAX_ATTACHMENT_BYTES + 2) // 3) * 4:
            raise BridgeError("attachment exceeds the bridge size limit")
        try:
            data = base64.b64decode(contents, validate=True)
        except (ValueError, UnicodeError) as error:
            raise BridgeError("attachment contents must be valid base64") from error
        if len(data) > MAX_ATTACHMENT_BYTES:
            raise BridgeError("attachment exceeds the bridge size limit")


def _read_file(directory: int, name: str, maximum: int) -> bytes:
    descriptor = os.open(name, os.O_RDONLY | _NOFOLLOW, dir_fd=directory)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise BridgeError("bridge files must be regular files without additional links")
        if info.st_size > maximum:
            raise BridgeError("request or response exceeds the bridge size limit")
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        if len(data) > maximum:
            raise BridgeError("request or response exceeds the bridge size limit")
        return data
    finally:
        os.close(descriptor)


def _atomic_write(directory: int, name: str, data: bytes) -> None:
    temporary = f".tmp-{uuid.uuid4().hex}"
    descriptor = os.open(
        temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW, 0o600, dir_fd=directory
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
        os.fsync(directory)
    finally:
        try:
            os.unlink(temporary, dir_fd=directory)
        except FileNotFoundError:
            pass


def _unlink(directory: int, name: str) -> None:
    try:
        os.unlink(name, dir_fd=directory)
    except FileNotFoundError:
        pass


def _open_child(parent: int, name: str, *, create: bool) -> int:
    if create:
        try:
            os.mkdir(name, 0o700, dir_fd=parent)
        except FileExistsError:
            pass
    return os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)


def _open_directory(path: Path) -> int:
    """Walk an absolute canonical path without following any new symlinks."""
    if not path.is_absolute() or ".." in path.parts:
        raise BridgeError("bridge directory must use an absolute path without traversal")
    descriptor = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in path.parts[1:]:
            child = _open_child(descriptor, component, create=False)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


@dataclass
class _Mailbox:
    path: Path
    workspace_fd: int
    root_fd: int
    requests_fd: int
    responses_fd: int

    @classmethod
    def open(cls, path: Path, *, create: bool = False) -> _Mailbox:
        # Only the trusted workspace parent is resolved. Never resolve or follow
        # the mailbox or either peer-writable queue directory.
        parent = path.parent
        if path.name != ".idea-peer":
            raise BridgeError("bridge directory must be the workspace .idea-peer mailbox")
        descriptors: list[int] = []
        try:
            workspace = _open_directory(parent)
            descriptors.append(workspace)
            root = _open_child(workspace, ".idea-peer", create=create)
            descriptors.append(root)
            requests = _open_child(root, "requests", create=create)
            descriptors.append(requests)
            responses = _open_child(root, "responses", create=create)
            descriptors.append(responses)
            return cls(parent / ".idea-peer", workspace, root, requests, responses)
        except OSError as error:
            for descriptor in reversed(descriptors):
                os.close(descriptor)
            raise BridgeError("bridge mailbox is missing or contains an unsafe directory") from error

    def valid(self) -> bool:
        try:
            for parent, name, descriptor in (
                (self.workspace_fd, ".idea-peer", self.root_fd),
                (self.root_fd, "requests", self.requests_fd),
                (self.root_fd, "responses", self.responses_fd),
            ):
                current = os.stat(name, dir_fd=parent, follow_symlinks=False)
                original = os.fstat(descriptor)
                if not stat.S_ISDIR(current.st_mode) or (
                    current.st_dev, current.st_ino
                ) != (original.st_dev, original.st_ino):
                    return False
            return True
        except OSError:
            return False

    def close(self) -> None:
        for descriptor in (self.responses_fd, self.requests_fd, self.root_fd, self.workspace_fd):
            os.close(descriptor)


@dataclass(frozen=True)
class _Registration:
    """Persistent identity without permanently retaining directory handles."""

    path: Path
    fingerprints: tuple[tuple[int, int], ...]

    @classmethod
    def capture(cls, mailbox: _Mailbox) -> _Registration:
        return cls(mailbox.path, tuple(
            (info.st_dev, info.st_ino)
            for info in (
                os.fstat(mailbox.workspace_fd), os.fstat(mailbox.root_fd),
                os.fstat(mailbox.requests_fd), os.fstat(mailbox.responses_fd),
            )
        ))

    @contextmanager
    def opened(self) -> Iterator[_Mailbox]:
        mailbox = _Mailbox.open(self.path)
        try:
            if self.capture(mailbox).fingerprints != self.fingerprints or not mailbox.valid():
                raise BridgeError("peer mailbox registration changed")
            yield mailbox
        finally:
            mailbox.close()


class BridgeClient:
    def __init__(
        self, path: str | Path, *, timeout: float = 30,
        poll_interval: float = 0.05, max_bytes: int = MAX_BYTES,
    ):
        if timeout <= 0 or poll_interval <= 0 or not 1024 <= max_bytes <= MAX_BYTES:
            raise ValueError("bridge timeout, poll interval, or size limit is invalid")
        self.path = Path(path).expanduser().absolute()
        self.timeout = timeout
        self.poll_interval = poll_interval
        self.max_bytes = max_bytes

    def call(self, command: str, payload: dict[str, Any], *, request_id: str | None = None) -> Any:
        identifier = request_id or uuid.uuid4().hex
        if not isinstance(identifier, str) or not _REQUEST_ID.fullmatch(identifier):
            raise BridgeError("bridge request id must be 32 lowercase hexadecimal characters")
        # The server owns the allowlist. Client validation still rejects identity
        # spoofing, unsafe attachment names, and privileged commands early.
        _validate_call(command, payload, {command})
        data = _json_bytes({"version": 1, "id": identifier, "command": command, "payload": payload}, self.max_bytes)
        digest = hashlib.sha256(data).hexdigest()
        mailbox = _Mailbox.open(self.path)
        name = f"{identifier}.json"
        deadline = time.monotonic() + self.timeout
        try:
            _atomic_write(mailbox.requests_fd, name, data)
            while True:
                if not mailbox.valid():
                    raise BridgeError("bridge mailbox changed while waiting for a response")
                try:
                    response = _decode_json(_read_file(mailbox.responses_fd, name, self.max_bytes))
                except FileNotFoundError:
                    response = None
                except OSError as error:
                    raise BridgeError("bridge response is not a safe regular file") from error
                if response is not None:
                    if not isinstance(response, dict) or response.get("id") != identifier:
                        raise BridgeError("bridge response does not match the request")
                    if response.get("request_digest") != digest:
                        raise BridgeError("bridge request id was already used for a different operation")
                    _unlink(mailbox.responses_fd, name)
                    if response.get("ok") is True:
                        return response.get("result")
                    error = response.get("error", {})
                    if not isinstance(error, dict):
                        raise BridgeError("invalid bridge error response")
                    raise BridgeRemoteError(str(error.get("code", "remote_error")), str(error.get("message", "forum request failed")))
                if time.monotonic() >= deadline:
                    raise BridgeTimeoutError(identifier)
                time.sleep(min(self.poll_interval, max(0, deadline - time.monotonic())))
        finally:
            mailbox.close()


class BridgeServer:
    def __init__(
        self, forum: Forum, run_id: str,
        handler: Callable[[str, str, dict[str, Any]], Any], *,
        allowed_commands: Iterable[str] = DEFAULT_COMMANDS,
        max_bytes: int = MAX_BYTES, max_batch: int = 100,
    ):
        forum.get_run(run_id)
        if not 1024 <= max_bytes <= MAX_BYTES or not 1 <= max_batch <= 1000:
            raise ValueError("bridge size or batch limit is invalid")
        commands = frozenset(allowed_commands)
        if commands.intersection(_FORBIDDEN_COMMANDS):
            raise ValueError("privileged launcher commands cannot be enabled on the bridge")
        self.forum, self.run_id, self.handler = forum, run_id, handler
        self.allowed_commands, self.max_bytes, self.max_batch = commands, max_bytes, max_batch
        self._mailboxes: dict[str, _Registration] = {}
        self._lock = threading.Lock()
        self._round_robin = 0
        with forum._connection() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS bridge_workspaces (
                    agent_id TEXT PRIMARY KEY REFERENCES agents(id),
                    run_id TEXT NOT NULL REFERENCES runs(id),
                    workspace TEXT NOT NULL UNIQUE,
                    fingerprint_json TEXT
                );
                CREATE TABLE IF NOT EXISTS bridge_requests (
                    run_id TEXT NOT NULL REFERENCES runs(id),
                    agent_id TEXT NOT NULL REFERENCES agents(id),
                    request_id TEXT NOT NULL,
                    request_digest TEXT NOT NULL,
                    state TEXT NOT NULL,
                    response_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (run_id, agent_id, request_id)
                );
                CREATE INDEX IF NOT EXISTS bridge_response_retention
                    ON bridge_requests(run_id,state,updated_at);
            """)
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(bridge_workspaces)")}
            if "fingerprint_json" not in columns:
                connection.execute("ALTER TABLE bridge_workspaces ADD COLUMN fingerprint_json TEXT")

    def register(self, agent_id: str, workspace: str | Path) -> Path:
        agent = self.forum.get_agent(agent_id)
        if agent["run_id"] != self.run_id:
            raise BridgeError("peer does not belong to this run")
        raw_path = Path(workspace).expanduser().absolute()
        if raw_path.is_symlink():
            raise BridgeError("peer workspace must not be a symbolic link")
        # Resolve the trusted containing directory, but open the workspace leaf
        # itself without following a symlink installed between these checks.
        path = raw_path.parent.resolve(strict=True) / raw_path.name
        with self._lock:
            existing = self._mailboxes.get(agent_id)
            if existing is not None:
                if existing.path.parent != path:
                    raise BridgeError("peer mailbox registration changed")
                with existing.opened():
                    pass
                return existing.path
            mailbox = None
            try:
                with self.forum._connection() as connection:
                    connection.execute("BEGIN IMMEDIATE")
                    previous_fingerprint = None
                    already_registered = False
                    for row in connection.execute("SELECT * FROM bridge_workspaces"):
                        previous = Path(row["workspace"])
                        if row["agent_id"] == agent_id:
                            already_registered = True
                            if previous != path:
                                raise BridgeError("peer workspace registration changed")
                            previous_fingerprint = row["fingerprint_json"]
                        elif previous == path or previous in path.parents or path in previous.parents:
                            raise BridgeError("peer workspaces must be separate and must not overlap")
                    mailbox = _Mailbox.open(path / ".idea-peer", create=not already_registered)
                    registration = _Registration.capture(mailbox)
                    fingerprint = json.dumps(registration.fingerprints)
                    if previous_fingerprint is not None and previous_fingerprint != fingerprint:
                        raise BridgeError("peer mailbox registration changed")
                    connection.execute(
                        "INSERT INTO bridge_workspaces(agent_id,run_id,workspace,fingerprint_json) VALUES (?,?,?,?) "
                        "ON CONFLICT(agent_id) DO UPDATE SET fingerprint_json=excluded.fingerprint_json",
                        (agent_id, self.run_id, str(path), fingerprint),
                    )
            finally:
                if mailbox is not None:
                    mailbox.close()
            self._mailboxes[agent_id] = registration
            return registration.path

    @staticmethod
    def _error(identifier: str, code: str, message: str, digest: str | None = None) -> dict[str, Any]:
        return {"version": 1, "id": identifier, "request_digest": digest, "ok": False,
                "error": {"code": code, "message": message}}

    def _dispatch(self, agent_id: str, identifier: str, request: dict[str, Any]) -> bytes:
        canonical = _json_bytes(request, self.max_bytes)
        digest = hashlib.sha256(canonical).hexdigest()
        identity = (self.run_id, agent_id, identifier)
        with self.forum._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            previous = connection.execute(
                "SELECT * FROM bridge_requests WHERE run_id=? AND agent_id=? AND request_id=?", identity
            ).fetchone()
            if previous is not None:
                if previous["request_digest"] != digest:
                    return _json_bytes(self._error(identifier, "request_conflict", "request id was already used for a different operation", digest), self.max_bytes)
                if previous["response_json"] is not None:
                    return previous["response_json"].encode()
                code = "outcome_unknown" if previous["state"] == "processing" else "response_expired"
                message = (
                    "the launcher stopped during this operation; inspect the forum before issuing a new request"
                    if code == "outcome_unknown" else
                    "the saved response expired; this operation will not be executed again"
                )
                response = _json_bytes(self._error(identifier, code, message, digest), self.max_bytes)
                connection.execute(
                    "UPDATE bridge_requests SET state='completed',response_json=?,updated_at=? "
                    "WHERE run_id=? AND agent_id=? AND request_id=?",
                    (response.decode(), _now(), *identity),
                )
                return response
            connection.execute(
                "INSERT INTO bridge_requests VALUES (?,?,?,?, 'processing',NULL,?,?)",
                (*identity, digest, _now(), _now()),
            )
        try:
            result = self.handler(agent_id, request["command"], request["payload"])
            response = _json_bytes({"version": 1, "id": identifier, "request_digest": digest,
                                    "ok": True, "result": _public_result(result)}, self.max_bytes)
        except BridgeError as error:
            response = _json_bytes(self._error(identifier, "command_rejected", str(error), digest), self.max_bytes)
        except Exception as error:
            # Tracebacks and arbitrary exception text can contain host paths,
            # credentials, or provider session metadata. The dispatcher may
            # raise BridgeError when it has a safe, useful public explanation.
            response = _json_bytes(self._error(identifier, "command_failed", f"forum command failed ({type(error).__name__})", digest), self.max_bytes)
        with self.forum._connection() as connection:
            connection.execute(
                "UPDATE bridge_requests SET state='completed',response_json=?,updated_at=? "
                "WHERE run_id=? AND agent_id=? AND request_id=?",
                (response.decode(), _now(), *identity),
            )
        return response

    def _poll_one(self, agent_id: str, mailbox: _Mailbox) -> int:
        with os.scandir(mailbox.requests_fd) as entries:
            name = None
            for index, entry in enumerate(entries):
                if index >= 256:
                    break
                if _REQUEST_FILE.fullmatch(entry.name):
                    name = entry.name
                    break
        if name is None:
            return 0
        identifier = name.removesuffix(".json")
        digest = None
        try:
            request = _decode_json(_read_file(mailbox.requests_fd, name, self.max_bytes))
            digest = hashlib.sha256(_json_bytes(request, self.max_bytes)).hexdigest()
            if (
                not isinstance(request, dict) or set(request) != {"version", "id", "command", "payload"}
                or type(request.get("version")) is not int or request.get("version") != 1
                or request.get("id") != identifier
            ):
                raise BridgeError("invalid bridge request envelope")
            _validate_call(request["command"], request["payload"], self.allowed_commands)
            response = self._dispatch(agent_id, identifier, request)
        except (BridgeError, OSError) as error:
            message = str(error) if isinstance(error, BridgeError) else "bridge request is not a safe regular file"
            response = _json_bytes(self._error(identifier, "invalid_request", message, digest), self.max_bytes)
        if not mailbox.valid():
            return 0
        _atomic_write(mailbox.responses_fd, name, response)
        _unlink(mailbox.requests_fd, name)
        return 1

    def poll(self) -> int:
        """Process a fair batch with only one mailbox's directory handles open."""
        with self._lock:
            peers = list(self._mailboxes.items())
            if not peers:
                return 0
            start = self._round_robin % len(peers)
            peers = peers[start:] + peers[:start]
            processed = 0
            examined = 0
            # One request per peer per pass keeps a busy peer from starving its
            # neighbors, even when its queue contains thousands of requests.
            for agent_id, registration in peers:
                if processed >= self.max_batch:
                    break
                examined += 1
                try:
                    with registration.opened() as mailbox:
                        processed += self._poll_one(agent_id, mailbox)
                except (BridgeError, OSError):
                    # A completed result remains in the trusted journal. A peer
                    # can repair its mailbox and retry the same request id.
                    continue
            self._round_robin = (start + examined) % len(peers)
            return processed

    def prune_responses(self, *, older_than: str) -> int:
        """Drop old saved bodies, retaining permanent deduplication tombstones."""
        with self._lock:
            with self.forum._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                rows = connection.execute(
                    "SELECT agent_id,request_id FROM bridge_requests WHERE run_id=? "
                    "AND state='completed' AND updated_at<? AND response_json IS NOT NULL",
                    (self.run_id, older_than),
                ).fetchall()
                connection.execute(
                    "UPDATE bridge_requests SET response_json=NULL,state='expired' "
                    "WHERE run_id=? AND state='completed' AND updated_at<? AND response_json IS NOT NULL",
                    (self.run_id, older_than),
                )
            for row in rows:
                registration = self._mailboxes.get(row["agent_id"])
                if registration is not None:
                    try:
                        with registration.opened() as mailbox:
                            _unlink(mailbox.responses_fd, f"{row['request_id']}.json")
                    except (BridgeError, OSError):
                        pass
            return len(rows)

    def close(self) -> None:
        with self._lock:
            self._mailboxes.clear()

    def __enter__(self) -> BridgeServer:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()
