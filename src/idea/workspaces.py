"""Independent working copies and explicit, revision-checked artifact integration."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import posixpath
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Any, Iterator

from .forum import Forum, _now


_EXCLUDED = frozenset({
    ".git", ".idea", ".idea-swarm", ".idea-peer", "__pycache__", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", ".cache", ".venv", "venv", "node_modules", ".DS_Store",
})
_RUNTIME_DIRECTORIES = frozenset({".venv", "venv", "node_modules"})


class WorkspaceStore:
    def __init__(self, forum: Forum, run_id: str):
        self.forum = forum
        self.run_id = run_id
        self.origin = Path(str(forum.get_run(run_id)["workspace"])).resolve()
        self.root = forum.state_dir / "runs" / run_id / "workspaces"
        self.seed = self.root / "seed"
        self.runtime_seed = self.root / "runtime-seed"
        self.root.mkdir(parents=True, exist_ok=True)
        with forum._connection() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS workspace_policies (
                    run_id TEXT PRIMARY KEY REFERENCES runs(id),
                    mode TEXT NOT NULL,
                    base_revision TEXT NOT NULL,
                    base_fingerprint TEXT NOT NULL,
                    source_head TEXT,
                    manifest_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS agent_workspaces (
                    agent_id TEXT PRIMARY KEY REFERENCES agents(id),
                    run_id TEXT NOT NULL REFERENCES runs(id),
                    path TEXT NOT NULL,
                    base_revision TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    needs_fresh_session INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS workspace_artifacts (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(id),
                    agent_id TEXT NOT NULL REFERENCES agents(id),
                    base_revision TEXT NOT NULL,
                    patch_path TEXT NOT NULL,
                    patch_sha256 TEXT NOT NULL,
                    files_json TEXT NOT NULL,
                    note TEXT NOT NULL,
                    validation TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    integrated_at TEXT
                );
                CREATE INDEX IF NOT EXISTS artifacts_by_run
                    ON workspace_artifacts(run_id, id);
            """)
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(agent_workspaces)")}
            if "needs_fresh_session" not in columns:
                connection.execute("BEGIN IMMEDIATE")
                columns = {row["name"] for row in connection.execute("PRAGMA table_info(agent_workspaces)")}
                if "needs_fresh_session" not in columns:
                    connection.execute("ALTER TABLE agent_workspaces ADD COLUMN needs_fresh_session INTEGER NOT NULL DEFAULT 0")

    @contextmanager
    def _lock(self, path: Path | None = None) -> Iterator[None]:
        import fcntl
        with (path or self.root / "workspaces.lock").open("a+b") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            yield

    @staticmethod
    def _git_environment(**extra: str) -> dict[str, str]:
        env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                   GIT_TERMINAL_PROMPT="0", GIT_AUTHOR_NAME="IDEA snapshot",
                   GIT_AUTHOR_EMAIL="snapshot@idea.invalid", GIT_COMMITTER_NAME="IDEA snapshot",
                   GIT_COMMITTER_EMAIL="snapshot@idea.invalid", **extra)
        return env

    def _git(self, *args: str, cwd: Path | None = None, env: dict[str, str] | None = None,
             input_data: bytes | None = None) -> bytes:
        result = subprocess.run(
            ["git", "-c", "core.hooksPath=" + os.devnull, "-c", "core.autocrlf=false",
             "-c", "core.attributesFile=" + os.devnull, *args],
            cwd=cwd or self.root, env=env or self._git_environment(),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, input=input_data,
        )
        if result.returncode:
            detail = result.stderr.decode("utf-8", errors="replace")[:2000].strip()
            raise RuntimeError(f"Git workspace operation failed: {detail}")
        return result.stdout

    def _head(self) -> str | None:
        result = subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD"], cwd=self.origin,
            env=self._git_environment(), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            check=False,
        )
        return result.stdout.decode().strip() if result.returncode == 0 else None

    @staticmethod
    def _exact_bytes(repository: Path) -> None:
        # Snapshot copies must retain current bytes, even when the project has
        # attributes for line-ending conversion, clean filters, or encodings.
        attributes = repository / ".git" / "info" / "attributes"
        attributes.parent.mkdir(parents=True, exist_ok=True)
        attributes.write_text("* -text -filter -ident -working-tree-encoding\n", encoding="utf-8")
        (attributes.parent / "exclude").write_text(
            ".idea-peer/\n.venv/\nvenv/\nnode_modules/\n", encoding="utf-8")

    def _excluded(self, path: Path, relative: PurePosixPath, root: Path) -> bool:
        return (any(part in _EXCLUDED for part in relative.parts)
                or (root == self.origin and
                    (path == self.forum.state_dir or path.is_relative_to(self.forum.state_dir))))

    @staticmethod
    def _directory_fd(path: Path | str, *, parent: int | None = None) -> int:
        return os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)

    @staticmethod
    def _file_identity(info: os.stat_result) -> tuple[int, ...]:
        return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns,
                info.st_nlink, stat.S_IMODE(info.st_mode))

    @staticmethod
    def _open_file(parent: int, name: str, *, allow_hardlinks: bool = False) -> tuple[int, os.stat_result]:
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or (info.st_nlink != 1 and not allow_hardlinks):
            os.close(descriptor)
            raise ValueError(f"snapshot requires ordinary files without hard links: {name}")
        return descriptor, info

    def _runtime_roots(self) -> list[str]:
        roots: list[str] = []

        def walk(descriptor: int, prefix: PurePosixPath) -> None:
            with os.scandir(descriptor) as entries:
                names = sorted(entry.name for entry in entries)
            for name in names:
                relative = prefix / name
                path = self.origin / relative
                if path == self.forum.state_dir or path.is_relative_to(self.forum.state_dir):
                    continue
                if name in _RUNTIME_DIRECTORIES:
                    info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                    if not stat.S_ISDIR(info.st_mode):
                        raise ValueError(f"dependency root must be an ordinary directory: {relative}")
                    roots.append(str(relative))
                elif not self._excluded(path, relative, self.origin):
                    info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                    if stat.S_ISDIR(info.st_mode):
                        child = self._directory_fd(name, parent=descriptor)
                        try:
                            walk(child, relative)
                        finally:
                            os.close(child)

        descriptor = self._directory_fd(self.origin)
        try:
            walk(descriptor, PurePosixPath())
        finally:
            os.close(descriptor)
        return roots

    def _relocate_virtualenvs(self, workspace: Path, roots: list[str], *, target: Path) -> None:
        # Standard venv launchers/activation scripts embed the source environment
        # path. Adjust these generated scripts, never arbitrary package contents.
        for relative in roots:
            if PurePosixPath(relative).name not in {".venv", "venv"}:
                continue
            binary_dir = workspace / relative / "bin"
            if not binary_dir.is_dir() or binary_dir.is_symlink():
                continue
            original = os.fsencode(str(self.origin / relative))
            replacement = os.fsencode(str(target / relative))
            for path in binary_dir.iterdir():
                info = path.lstat()
                if not stat.S_ISREG(info.st_mode) or info.st_size > 1024 * 1024:
                    continue
                content = path.read_bytes()
                if (b"\0" not in content and original in content
                        and (content.startswith(b"#!") or path.name.startswith("activate"))):
                    path.write_bytes(content.replace(original, replacement))

    def _copy_runtime(self, source: Path, destination: Path, roots: list[str], *, python_links: bool = False) -> None:
        """Freeze dependencies as private files; retain only links inside that copy.

        Runtime directories are deliberately outside the Git baseline and artifact
        patches. Relative links inside them preserve pnpm's cyclic dependency graph.
        """
        allowed = [PurePosixPath(root) for root in roots]
        python_executables = {Path(sys.executable).resolve(),
                              Path(getattr(sys, "_base_executable", sys.executable)).resolve()}

        def copy_file(source_parent: int, name: str, destination_parent: int,
                      destination_name: str | None = None) -> None:
            descriptor, info = self._open_file(source_parent, name, allow_hardlinks=True)
            with os.fdopen(descriptor, "rb") as input_file:
                output_fd = os.open(destination_name or name,
                                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                    0o755 if info.st_mode & 0o111 else 0o644, dir_fd=destination_parent)
                with os.fdopen(output_fd, "wb") as output_file:
                    shutil.copyfileobj(input_file, output_file, 1024 * 1024)
                    os.fchmod(output_file.fileno(), 0o755 if info.st_mode & 0o111 else 0o644)
                if self._file_identity(info) != self._file_identity(os.fstat(input_file.fileno())):
                    raise RuntimeError("dependency file changed during snapshot copy")

        def copy_python(path: Path, destination_parent: int, name: str) -> None:
            # Resolve only to compare against the running interpreter. Open every
            # component without following links so a replacement cannot redirect IO.
            resolved = path.resolve(strict=True)
            if not python_links or resolved not in python_executables:
                raise ValueError(f"dependency symlink leaves the private dependency roots: {path.relative_to(source)}")
            descriptor = self._directory_fd(resolved.anchor)
            try:
                for component in resolved.parts[1:-1]:
                    child = self._directory_fd(component, parent=descriptor)
                    os.close(descriptor)
                    descriptor = child
                copy_file(descriptor, resolved.name, destination_parent, name)
            finally:
                os.close(descriptor)

        def walk(source_fd: int, destination_fd: int, prefix: PurePosixPath) -> None:
            before = os.fstat(source_fd)
            with os.scandir(source_fd) as entries:
                names = sorted(entry.name for entry in entries)
            for name in names:
                if name in {".git", ".idea-peer"}:
                    continue
                relative = prefix / name
                info = os.stat(name, dir_fd=source_fd, follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    child = self._directory_fd(name, parent=source_fd)
                    try:
                        os.mkdir(name, dir_fd=destination_fd)
                        output = self._directory_fd(name, parent=destination_fd)
                        try:
                            walk(child, output, relative)
                        finally:
                            os.close(output)
                    finally:
                        os.close(child)
                elif stat.S_ISREG(info.st_mode):
                    copy_file(source_fd, name, destination_fd)
                elif stat.S_ISLNK(info.st_mode):
                    target = os.readlink(name, dir_fd=source_fd)
                    normalized = PurePosixPath(posixpath.normpath(str(relative.parent / target)))
                    if (not os.path.isabs(target) and ".." not in normalized.parts
                            and not any(part in {".git", ".idea-peer"} for part in normalized.parts)
                            and any(normalized == root or normalized.is_relative_to(root) for root in allowed)):
                        os.symlink(target, name, dir_fd=destination_fd)
                    else:
                        copy_python(source / relative, destination_fd, name)
                else:
                    raise ValueError(f"unsupported special file in dependencies: {relative}")
            if self._file_identity(before) != self._file_identity(os.fstat(source_fd)):
                raise RuntimeError("dependency directory changed during snapshot copy")

        source_fd = self._directory_fd(source)
        try:
            destination_fd = self._directory_fd(destination)
            try:
                for root in allowed:
                    input_parent, output_parent = os.dup(source_fd), os.dup(destination_fd)
                    try:
                        for component in root.parts:
                            child = self._directory_fd(component, parent=input_parent)
                            os.close(input_parent)
                            input_parent = child
                            try:
                                os.mkdir(component, dir_fd=output_parent)
                            except FileExistsError:
                                pass
                            output = self._directory_fd(component, parent=output_parent)
                            os.close(output_parent)
                            output_parent = output
                        walk(input_parent, output_parent, root)
                    finally:
                        os.close(input_parent)
                        os.close(output_parent)
            finally:
                os.close(destination_fd)
        finally:
            os.close(source_fd)

    def _manifest(self, root: Path) -> dict[str, dict[str, Any]]:
        manifest: dict[str, dict[str, Any]] = {}

        def walk(descriptor: int, prefix: PurePosixPath) -> None:
            # Resolve every child relative to a held directory descriptor. A
            # concurrently replaced directory cannot redirect reads outside it.
            with os.scandir(descriptor) as entries:
                names = sorted(entry.name for entry in entries)
            for name in names:
                relative = prefix / name
                if self._excluded(root / relative, relative, root):
                    continue
                info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    child = self._directory_fd(name, parent=descriptor)
                    try:
                        opened = os.fstat(child)
                        if (info.st_dev, info.st_ino) != (opened.st_dev, opened.st_ino):
                            raise RuntimeError("workspace directory changed while capturing its snapshot")
                        walk(child, relative)
                    finally:
                        os.close(child)
                elif stat.S_ISLNK(info.st_mode):
                    target = os.readlink(name, dir_fd=descriptor)
                    normalized = PurePosixPath(posixpath.normpath(str(relative.parent / target)))
                    if (os.path.isabs(target) or ".." in normalized.parts
                            or self._excluded(root / normalized, normalized, root)):
                        raise ValueError(f"workspace symlink leaves the managed snapshot: {relative}")
                    manifest[str(relative)] = {"kind": "symlink", "target": target, "mode": "120000"}
                elif stat.S_ISREG(info.st_mode):
                    file_fd, opened = self._open_file(descriptor, name)
                    with os.fdopen(file_fd, "rb") as handle:
                        if self._file_identity(info) != self._file_identity(opened):
                            raise RuntimeError("workspace file changed while capturing its snapshot")
                        digest = hashlib.sha256()
                        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                            digest.update(chunk)
                        if self._file_identity(opened) != self._file_identity(os.fstat(handle.fileno())):
                            raise RuntimeError("workspace file changed while capturing its snapshot")
                    manifest[str(relative)] = {"kind": "file", "sha256": digest.hexdigest(),
                                               "mode": "100755" if opened.st_mode & 0o111 else "100644"}
                else:
                    raise ValueError(f"unsupported special file in workspace: {relative}")

        descriptor = self._directory_fd(root)
        try:
            walk(descriptor, PurePosixPath())
        finally:
            os.close(descriptor)
        return manifest

    @staticmethod
    def _fingerprint(manifest: dict[str, dict[str, Any]]) -> str:
        return hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def _copy_snapshot(self, source: Path, destination: Path) -> dict[str, dict[str, Any]]:
        before = self._manifest(source)
        destination.mkdir(parents=True, exist_ok=True)
        source_fd = self._directory_fd(source)
        try:
            destination_fd = self._directory_fd(destination)
        except BaseException:
            os.close(source_fd)
            raise
        try:
            for relative, entry in before.items():
                parts = PurePosixPath(relative).parts
                source_parent, destination_parent = os.dup(source_fd), os.dup(destination_fd)
                try:
                    for component in parts[:-1]:
                        next_source = self._directory_fd(component, parent=source_parent)
                        os.close(source_parent)
                        source_parent = next_source
                        try:
                            os.mkdir(component, dir_fd=destination_parent)
                        except FileExistsError:
                            pass
                        next_destination = self._directory_fd(component, parent=destination_parent)
                        os.close(destination_parent)
                        destination_parent = next_destination
                    if entry["kind"] == "symlink":
                        if os.readlink(parts[-1], dir_fd=source_parent) != entry["target"]:
                            raise RuntimeError("workspace symlink changed during snapshot copy")
                        os.symlink(entry["target"], parts[-1], dir_fd=destination_parent)
                    else:
                        file_fd, opened = self._open_file(source_parent, parts[-1])
                        with os.fdopen(file_fd, "rb") as input_file:
                            output_fd = os.open(parts[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                                0o755 if entry["mode"] == "100755" else 0o644,
                                                dir_fd=destination_parent)
                            digest = hashlib.sha256()
                            with os.fdopen(output_fd, "wb") as output_file:
                                for chunk in iter(lambda: input_file.read(1024 * 1024), b""):
                                    digest.update(chunk)
                                    output_file.write(chunk)
                                os.fchmod(output_file.fileno(), 0o755 if entry["mode"] == "100755" else 0o644)
                            if (digest.hexdigest() != entry["sha256"]
                                    or self._file_identity(opened) != self._file_identity(os.fstat(input_file.fileno()))):
                                raise RuntimeError("workspace file changed during snapshot copy")
                finally:
                    os.close(source_parent)
                    os.close(destination_parent)
        finally:
            os.close(source_fd)
            os.close(destination_fd)
        if before != self._manifest(source) or before != self._manifest(destination):
            raise RuntimeError("workspace changed while capturing its snapshot; retry after edits settle")
        return before

    def _policy(self) -> dict[str, Any] | None:
        with self.forum._connection() as connection:
            row = connection.execute("SELECT * FROM workspace_policies WHERE run_id = ?", (self.run_id,)).fetchone()
        return dict(row) if row is not None else None

    def configured(self) -> bool:
        return self._policy() is not None

    def configure(self, mode: str | None = None) -> dict[str, Any]:
        if mode is not None and mode not in {"isolated", "shared"}:
            raise ValueError("workspace mode must be 'isolated' or 'shared'")
        with self._lock():
            existing = self._policy()
            if existing is not None:
                if mode is not None and mode != existing["mode"]:
                    with self.forum._connection() as connection:
                        count = connection.execute("SELECT COUNT(*) FROM agent_workspaces WHERE run_id = ?", (self.run_id,)).fetchone()[0]
                        if count:
                            raise ValueError("workspace mode cannot change after a peer workspace is prepared")
                        connection.execute("UPDATE workspace_policies SET mode = ? WHERE run_id = ?", (mode, self.run_id))
                    existing["mode"] = mode
                return self.summary()
            source_head = self._head()
            temporary = Path(tempfile.mkdtemp(prefix="seed-", dir=self.root))
            runtime_temporary = Path(tempfile.mkdtemp(prefix="runtime-", dir=self.root))
            try:
                manifest = self._copy_snapshot(self.origin, temporary)
                runtime_roots = self._runtime_roots()
                self._copy_runtime(self.origin, runtime_temporary, runtime_roots, python_links=True)
                if source_head != self._head() or manifest != self._manifest(self.origin):
                    raise RuntimeError("source changed while capturing its snapshot")
                self._git("init", "--quiet", str(temporary))
                self._exact_bytes(temporary)
                self._git("add", "--all", "--force", "--", ".", cwd=temporary)
                self._git("commit", "--quiet", "--allow-empty", "-m", "IDEA initial workspace snapshot", cwd=temporary)
                base = self._git("rev-parse", "HEAD", cwd=temporary).decode().strip()
                if self.seed.exists():
                    # Only an unregistered interrupted seed can exist here.
                    shutil.rmtree(self.seed)
                os.replace(temporary, self.seed)
                if self.runtime_seed.exists():
                    shutil.rmtree(self.runtime_seed)
                os.replace(runtime_temporary, self.runtime_seed)
                (self.root / "runtime-roots.json").write_text(json.dumps(runtime_roots), encoding="utf-8")
                with self.forum._connection() as connection:
                    connection.execute("INSERT INTO workspace_policies VALUES (?, ?, ?, ?, ?, ?, ?)",
                                       (self.run_id, mode or "isolated", base, self._fingerprint(manifest),
                                        source_head, json.dumps(manifest), _now()))
            finally:
                if temporary.exists():
                    shutil.rmtree(temporary)
                if runtime_temporary.exists():
                    shutil.rmtree(runtime_temporary)
        return self.summary()

    def _agent(self, agent_id: str) -> dict[str, Any]:
        agent = self.forum.get_agent(agent_id)
        if agent["run_id"] != self.run_id:
            raise ValueError("agent must belong to this run")
        return agent

    def get(self, agent_id: str) -> dict[str, Any] | None:
        self._agent(agent_id)
        with self.forum._connection() as connection:
            row = connection.execute("SELECT * FROM agent_workspaces WHERE agent_id = ? AND run_id = ?", (agent_id, self.run_id)).fetchone()
        return dict(row) if row is not None else None

    def fresh_session_required(self, agent_id: str) -> bool:
        record = self.get(agent_id)
        return bool(record and record["needs_fresh_session"])

    def acknowledge_session_reset(self, agent_id: str) -> None:
        """Clear only after the launcher durably discarded the old session ID.

        A crash between clearing the session and this acknowledgment safely leaves
        the marker set, so a preview or interrupted migration cannot consume it.
        """
        self._agent(agent_id)
        with self.forum._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            record = connection.execute("""SELECT w.needs_fresh_session,a.session_id
                FROM agent_workspaces w JOIN agents a ON a.id=w.agent_id
                WHERE w.agent_id=? AND w.run_id=?""", (agent_id, self.run_id)).fetchone()
            if record is None or not record["needs_fresh_session"]:
                return
            if record["session_id"]:
                raise ValueError("clear the stored provider session before acknowledging its workspace reset")
            connection.execute("""UPDATE agent_workspaces SET needs_fresh_session=0
                WHERE agent_id=? AND run_id=?""", (agent_id, self.run_id))

    def prepare(self, agent_id: str) -> Path:
        self._agent(agent_id)
        if not self.configured():
            self.configure()
        with self._lock():
            existing = self.get(agent_id)
            if existing is not None:
                path = Path(existing["path"])
                if not path.is_dir():
                    raise RuntimeError("persisted peer workspace is missing; refusing to replace its work")
                return path
            policy = self._policy()
            assert policy is not None
            path = self.origin if policy["mode"] == "shared" else self.root / "peers" / agent_id
            if policy["mode"] == "isolated":
                path.parent.mkdir(parents=True, exist_ok=True)
                if not path.exists():
                    temporary = path.with_name(path.name + ".preparing-" + uuid.uuid4().hex)
                    try:
                        self._git("clone", "--quiet", "--local", "--no-hardlinks", "--no-checkout", str(self.seed), str(temporary))
                        self._exact_bytes(temporary)
                        self._git("checkout", "--quiet", "--detach", policy["base_revision"], cwd=temporary)
                        self._git("remote", "remove", "origin", cwd=temporary)
                        runtime_roots = json.loads((self.root / "runtime-roots.json").read_text(encoding="utf-8"))
                        self._copy_runtime(self.runtime_seed, temporary, runtime_roots)
                        # Write the final path even though the complete copy is
                        # published only after all preparation succeeds.
                        self._relocate_virtualenvs(temporary, runtime_roots, target=path)
                        os.replace(temporary, path)
                    finally:
                        if temporary.exists():
                            shutil.rmtree(temporary)
                elif not (path / ".git").is_dir():
                    raise RuntimeError("unregistered peer workspace is not an independent Git snapshot")
            with self.forum._connection() as connection:
                connection.execute("""INSERT INTO agent_workspaces
                    (agent_id,run_id,path,base_revision,created_at,needs_fresh_session)
                    SELECT ?,?,?,?,?,CASE WHEN ?='isolated' AND session_id IS NOT NULL
                        AND session_id!='' THEN 1 ELSE 0 END FROM agents WHERE id=?""",
                                   (agent_id, self.run_id, str(path), policy["base_revision"], _now(),
                                    policy["mode"], agent_id))
            return path

    def summary(self) -> dict[str, Any]:
        policy = self._policy()
        with self.forum._connection() as connection:
            count = connection.execute("SELECT COUNT(*) FROM agent_workspaces WHERE run_id = ?", (self.run_id,)).fetchone()[0]
            artifact_count = connection.execute("SELECT COUNT(*) FROM workspace_artifacts WHERE run_id = ?", (self.run_id,)).fetchone()[0]
        return {"mode": policy["mode"] if policy else None,
                "base_revision": policy["base_revision"] if policy else None,
                "prepared_count": int(count), "artifact_count": int(artifact_count)}

    def _safe_path(self, relative: str, root: Path) -> Path:
        path = PurePosixPath(relative)
        if (not relative or path.is_absolute() or ".." in path.parts or "\\" in relative
                or any(part in _EXCLUDED for part in path.parts)):
            raise ValueError(f"artifact has an unsafe path: {relative}")
        candidate = root.joinpath(*path.parts)
        original_target = self.origin.joinpath(*path.parts)
        if original_target == self.forum.state_dir or original_target.is_relative_to(self.forum.state_dir):
            raise ValueError("artifact cannot modify IDEA state")
        for parent in (candidate, *candidate.parents):
            if parent == root:
                break
            if parent.is_symlink():
                raise ValueError(f"artifact path traverses a symlink: {relative}")
        return candidate

    def publish(self, agent_id: str, *, note: str = "", validation: str = "") -> dict[str, Any]:
        record = self.get(agent_id)
        if record is None:
            raise ValueError("prepare the agent workspace before publishing an artifact")
        policy = self._policy()
        assert policy is not None
        source = Path(record["path"])
        identifier = "artifact_" + uuid.uuid4().hex[:16]
        with self._lock(), tempfile.TemporaryDirectory(prefix="artifact-", dir=self.root) as directory:
            temporary = Path(directory)
            snapshot = temporary / "snapshot"
            manifest = self._copy_snapshot(source, snapshot)
            baseline = json.loads(policy["manifest_json"])
            changed = sorted(path for path in set(baseline) | set(manifest) if baseline.get(path) != manifest.get(path))
            if not changed:
                raise ValueError("workspace has no changes to publish")
            for relative in changed:
                self._safe_path(relative, snapshot)
                if (baseline.get(relative, {}).get("kind") == "symlink"
                        or manifest.get(relative, {}).get("kind") == "symlink"):
                    raise ValueError("artifacts cannot add, replace, or delete symbolic links")
            env = self._git_environment(GIT_INDEX_FILE=str(temporary / "index"))
            options = ("--git-dir=" + str(self.seed / ".git"), "--work-tree=" + str(snapshot))
            self._git(*options, "read-tree", policy["base_revision"], cwd=snapshot, env=env)
            self._git(*options, "add", "--all", "--force", "--", ".", cwd=snapshot, env=env)
            patch = self._git(*options, "diff", "--cached", "--binary", "--full-index", "--no-renames",
                              "--no-ext-diff", "--no-textconv", policy["base_revision"], "--", cwd=snapshot, env=env)
            files = self._git(*options, "diff", "--cached", "--name-only", "-z", "--no-renames",
                              policy["base_revision"], "--", cwd=snapshot, env=env).decode("utf-8", errors="surrogateescape").split("\0")[:-1]
            if not patch or set(files) != set(changed):
                raise RuntimeError("artifact patch does not match the captured workspace changes")
            artifact_dir = self.root / "artifacts"
            artifact_dir.mkdir(exist_ok=True)
            patch_path = artifact_dir / (identifier + ".patch")
            patch_path.write_bytes(patch)
            with self.forum._connection() as connection:
                connection.execute("INSERT INTO workspace_artifacts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
                                   (identifier, self.run_id, agent_id, policy["base_revision"], str(patch_path),
                                    hashlib.sha256(patch).hexdigest(), json.dumps(files), str(note), str(validation), _now()))
        return self.get_artifact(identifier)

    @staticmethod
    def _artifact_record(row: Any) -> dict[str, Any]:
        item = dict(row)
        item["files"] = json.loads(item.pop("files_json"))
        return item

    def get_artifact(self, artifact_id: str) -> dict[str, Any]:
        with self.forum._connection() as connection:
            row = connection.execute("SELECT * FROM workspace_artifacts WHERE run_id = ? AND id = ?", (self.run_id, artifact_id)).fetchone()
        if row is None:
            raise KeyError(f"unknown artifact in run: {artifact_id}")
        return self._artifact_record(row)

    def _patch(self, artifact: dict[str, Any]) -> bytes:
        path = Path(artifact["patch_path"])
        if path != self.root / "artifacts" / (artifact["id"] + ".patch"):
            raise ValueError("artifact patch is outside its registry directory")
        directory = self._directory_fd(path.parent)
        try:
            descriptor, info = self._open_file(directory, path.name)
            with os.fdopen(descriptor, "rb") as handle:
                patch = handle.read()
                if self._file_identity(info) != self._file_identity(os.fstat(handle.fileno())):
                    raise ValueError("artifact patch changed after publication")
        finally:
            os.close(directory)
        if hashlib.sha256(patch).hexdigest() != artifact["patch_sha256"]:
            raise ValueError("artifact patch changed after publication")
        return patch

    def read_artifact(self, artifact_id: str) -> dict[str, Any]:
        artifact = self.get_artifact(artifact_id)
        patch = self._patch(artifact)
        return {key: value for key, value in artifact.items() if key != "patch_path"} | {
            "patch_base64": base64.b64encode(patch).decode("ascii"),
        }

    def list_artifacts(self, *, limit: int = 50, after: str | None = None,
                       agent_id: str | None = None) -> dict[str, Any]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        if after is not None and not isinstance(after, str):
            raise ValueError("after must be an artifact ID")
        if agent_id is not None:
            self._agent(agent_id)
        with self.forum._connection() as connection:
            rows = connection.execute("""
                SELECT * FROM workspace_artifacts WHERE run_id = ? AND id > ?
                AND (? IS NULL OR agent_id = ?) ORDER BY id LIMIT ?
            """, (self.run_id, after or "", agent_id, agent_id, limit + 1)).fetchall()
        items = [self._artifact_record(row) for row in rows[:limit]]
        return {"items": items, "next_cursor": items[-1]["id"] if len(rows) > limit else None}

    def integrate(self, artifact_id: str) -> dict[str, Any]:
        """Called only by the user's explicit integration command, never peers."""
        # Different runs can target the same original directory. Serialize their
        # checks and application too, rather than locking only this run's copies.
        with self._lock(), self._lock(self.forum.state_dir / "workspace-integration.lock"):
            artifact = self.get_artifact(artifact_id)
            if artifact["integrated_at"]:
                return artifact
            policy = self._policy()
            if policy is None or artifact["base_revision"] != policy["base_revision"]:
                raise ValueError("artifact does not belong to this workspace baseline")
            patch = self._patch(artifact)
            if (self._head() != policy["source_head"]
                    or self._fingerprint(self._manifest(self.origin)) != policy["base_fingerprint"]):
                raise ValueError("original workspace changed since the baseline; reconcile the artifact manually")
            for relative in artifact["files"]:
                self._safe_path(relative, self.origin)
            options = ("--git-dir=" + str(self.seed / ".git"), "--work-tree=" + str(self.origin))
            self._git(*options, "apply", "--check", "-", cwd=self.origin, input_data=patch)
            self._git(*options, "apply", "-", cwd=self.origin, input_data=patch)
            with self.forum._connection() as connection:
                connection.execute("UPDATE workspace_artifacts SET integrated_at = ? WHERE run_id = ? AND id = ?",
                                   (_now(), self.run_id, artifact_id))
        return self.get_artifact(artifact_id)
