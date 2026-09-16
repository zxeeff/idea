from __future__ import annotations

import base64
import os
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from idea.domain import AgentProfile, Effort, ProcessState, Provider
from idea.forum import Forum
from idea.workspaces import WorkspaceStore


class WorkspacesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / "source"
        self.root.mkdir()
        (self.root / "document.txt").write_text("baseline\n")
        self.forum = Forum(self.root / "internal-state")
        self.run = self.forum.create_run("Independent workspace edits", self.root)
        self.store = WorkspaceStore(self.forum, self.run["id"])

    def peer(self, name: str = "peer") -> dict:
        return self.forum.register_agent(self.run["id"], AgentProfile(name, Provider.OPENAI, "test", Effort.HIGH))

    def git(self, *args: str, cwd: Path | None = None) -> bytes:
        return subprocess.check_output(["git", *args], cwd=cwd or self.root,
                                       env=self.store._git_environment(), stderr=subprocess.PIPE)

    def test_plain_directory_copies_are_independent_preserved_and_share_one_baseline(self) -> None:
        peer = self.peer()
        (self.root / "untracked.txt").write_text("included")
        for excluded in (".idea-peer", "__pycache__"):
            (self.root / excluded).mkdir()
            (self.root / excluded / "ignore.txt").write_text("excluded")
        self.assertFalse(self.store.configured())
        self.store.configure("isolated")
        copy = self.store.prepare(peer["id"])
        self.assertEqual("included", (copy / "untracked.txt").read_text())
        self.assertTrue((copy / ".git").is_dir())
        self.assertFalse((copy / "internal-state").exists())
        for excluded in (".idea-peer", "__pycache__"):
            self.assertFalse((copy / excluded).exists())
        (copy / "document.txt").write_text("private work")
        (self.root / "document.txt").write_text("new original work")
        reopened = WorkspaceStore(self.forum, self.run["id"])
        self.assertEqual(copy, reopened.prepare(peer["id"]))
        self.assertEqual("private work", (copy / "document.txt").read_text())
        other = reopened.prepare(self.peer("other")["id"])
        self.assertEqual("baseline\n", (other / "document.txt").read_text())
        self.assertEqual(self.git("rev-parse", "HEAD", cwd=copy), self.git("rev-parse", "HEAD", cwd=other))
        objects = [path for path in (self.store.seed / ".git" / "objects").glob("*/*") if path.is_file()]
        self.assertTrue(objects)
        for seed_object in objects:
            clone_object = copy / seed_object.relative_to(self.store.seed)
            if clone_object.exists():
                self.assertNotEqual(seed_object.stat().st_ino, clone_object.stat().st_ino)

    def test_current_dirty_and_untracked_bytes_are_the_git_snapshot(self) -> None:
        self.git("init", "--quiet")
        self.git("add", "document.txt")
        self.git("commit", "--quiet", "-m", "original")
        (self.root / "document.txt").write_text("dirty current content\n")
        (self.root / "new.txt").write_text("untracked current content")
        copy = self.store.prepare(self.peer()["id"])
        self.assertEqual("dirty current content\n", (copy / "document.txt").read_text())
        self.assertEqual("untracked current content", (copy / "new.txt").read_text())
        self.assertEqual(b"", self.git("status", "--porcelain", cwd=copy))

    def test_dependencies_are_frozen_private_and_excluded_from_artifacts(self) -> None:
        package = self.root / "node_modules" / ".pnpm" / "package"
        package.mkdir(parents=True)
        (package / "index.js").write_text("original dependency")
        (package / "self").symlink_to(".")
        (self.root / "node_modules" / "package").symlink_to(".pnpm/package")
        nested = self.root / "frontend" / "node_modules"
        nested.mkdir(parents=True)
        (nested / "extra.js").write_text("nested dependency")
        virtualenv = self.root / ".venv" / "bin"
        virtualenv.mkdir(parents=True)
        (virtualenv / "python").symlink_to(Path(sys.executable).resolve())
        peer = self.peer()
        copy = self.store.prepare(peer["id"])
        (package / "index.js").write_text("later dependency")
        other = self.store.prepare(self.peer("other")["id"])
        self.assertEqual("original dependency", (other / "node_modules" / "package" / "index.js").read_text())
        self.assertTrue((copy / "node_modules" / "package").is_symlink())
        self.assertTrue((copy / "node_modules" / ".pnpm" / "package" / "self").is_symlink())
        self.assertFalse((copy / ".venv" / "bin" / "python").is_symlink())
        self.assertTrue(os.access(copy / ".venv" / "bin" / "python", os.X_OK))
        self.assertEqual("nested dependency", (copy / "frontend" / "node_modules" / "extra.js").read_text())
        relative = Path("node_modules/.pnpm/package/index.js")
        self.assertNotEqual((copy / relative).stat().st_ino, (other / relative).stat().st_ino)
        self.assertNotEqual((copy / relative).stat().st_ino, (self.root / relative).stat().st_ino)
        self.assertEqual(1, (copy / relative).stat().st_nlink)
        self.assertEqual(b"", self.git("status", "--porcelain", cwd=copy))
        (copy / relative).write_text("private dependency change")
        (copy / "document.txt").write_text("proposal")
        artifact = self.store.publish(peer["id"])
        self.assertEqual(["document.txt"], artifact["files"])
        self.assertEqual("original dependency", (other / relative).read_text())
        self.assertEqual("later dependency", (self.root / relative).read_text())

    def test_dependency_links_cannot_refer_to_external_writable_files(self) -> None:
        dependencies = self.root / "node_modules"
        dependencies.mkdir()
        outside = self.root.parent / "outside.txt"
        outside.write_text("outside")
        (dependencies / "escape").symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "leaves the private dependency roots"):
            self.store.configure()
        self.assertFalse(self.store.configured())

    def test_virtualenv_interpreter_and_generated_launcher_use_the_private_copy(self) -> None:
        virtualenv = self.root / ".venv"
        subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(virtualenv)], check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        launcher = virtualenv / "bin" / "peer-check"
        launcher.write_text(f"#!{virtualenv}/bin/python\nimport sys\nprint(sys.prefix)\n")
        launcher.chmod(0o755)
        copy = self.store.prepare(self.peer()["id"])
        result = subprocess.check_output([str(copy / ".venv" / "bin" / "peer-check")], text=True).strip()
        self.assertEqual(str(copy / ".venv"), result)
        self.assertNotIn(str(virtualenv), (copy / ".venv" / "bin" / "activate").read_text())

    def test_managed_hardlinks_are_rejected_and_file_replacement_cannot_redirect_reads(self) -> None:
        outside = self.root.parent / "outside.txt"
        outside.write_text("outside")
        os.link(outside, self.root / "hardlink.txt")
        with self.assertRaisesRegex(ValueError, "without hard links"):
            self.store.configure()
        (self.root / "hardlink.txt").unlink()
        real_open = os.open
        replaced = False

        def replacing_open(path, flags, *args, **kwargs):
            nonlocal replaced
            if path == "document.txt" and flags & os.O_NOFOLLOW and not replaced:
                replaced = True
                (self.root / "document.txt").unlink()
                (self.root / "document.txt").symlink_to(outside)
            return real_open(path, flags, *args, **kwargs)

        with patch("idea.workspaces.os.open", side_effect=replacing_open):
            with self.assertRaises(OSError):
                self.store.configure()
        self.assertTrue(replaced)
        self.assertEqual("outside", outside.read_text())
        self.assertFalse(self.store.configured())

    def test_binary_artifact_is_explicit_and_preserves_original_head_and_index(self) -> None:
        self.git("init", "--quiet")
        self.git("add", "document.txt")
        self.git("commit", "--quiet", "-m", "original")
        head = self.git("rev-parse", "HEAD")
        (self.root / "document.txt").write_text("staged original\n")
        self.git("add", "document.txt")
        staged = self.git("show", ":document.txt")
        (self.root / "document.txt").write_text("unstaged baseline\n")
        (self.root / "binary.bin").write_bytes(b"\x00\xfforiginal")
        (self.root / "delete.txt").write_text("old")
        copy = self.store.prepare(self.peer()["id"])
        peer_id = self.forum.list_agents(self.run["id"])[0]["id"]
        (copy / "document.txt").write_text("proposal\n")
        (copy / "binary.bin").write_bytes(b"\x00\xffupdated\x01")
        (copy / "added file.txt").write_text("new")
        (copy / "delete.txt").unlink()
        artifact = self.store.publish(peer_id, note="Peer proposal", validation="Author reports a local check")
        self.assertEqual("unstaged baseline\n", (self.root / "document.txt").read_text())
        self.assertIn(b"GIT binary patch", base64.b64decode(self.store.read_artifact(artifact["id"])["patch_base64"]))
        self.assertNotIn("patch_path", self.store.read_artifact(artifact["id"]))
        self.assertEqual({"document.txt", "binary.bin", "added file.txt", "delete.txt"}, set(artifact["files"]))
        result = self.store.integrate(artifact["id"])
        self.assertIsNotNone(result["integrated_at"])
        self.assertEqual("proposal\n", (self.root / "document.txt").read_text())
        self.assertEqual(b"\x00\xffupdated\x01", (self.root / "binary.bin").read_bytes())
        self.assertFalse((self.root / "delete.txt").exists())
        self.assertEqual(head, self.git("rev-parse", "HEAD"))
        self.assertEqual(staged, self.git("show", ":document.txt"))

    def test_conflict_or_patch_tampering_changes_no_original_files(self) -> None:
        peer = self.peer()
        copy = self.store.prepare(peer["id"])
        (copy / "document.txt").write_text("proposal")
        artifact = self.store.publish(peer["id"])
        (self.root / "human.txt").write_text("new human work")
        with self.assertRaisesRegex(ValueError, "original workspace changed"):
            self.store.integrate(artifact["id"])
        self.assertEqual("baseline\n", (self.root / "document.txt").read_text())
        self.assertEqual("new human work", (self.root / "human.txt").read_text())
        (self.root / "human.txt").unlink()
        Path(artifact["patch_path"]).write_text("tampered patch")
        with self.assertRaisesRegex(ValueError, "changed after publication"):
            self.store.integrate(artifact["id"])
        self.assertEqual("baseline\n", (self.root / "document.txt").read_text())

    def test_different_runs_serialize_integration_into_the_same_original(self) -> None:
        peer = self.peer()
        copy = self.store.prepare(peer["id"])
        other_run = self.forum.create_run("Another proposed change", self.root)
        other = WorkspaceStore(self.forum, other_run["id"])
        other_peer = self.forum.register_agent(other_run["id"], AgentProfile("other", Provider.ANTHROPIC, "test", Effort.HIGH))
        other_copy = other.prepare(other_peer["id"])
        (copy / "document.txt").write_text("first proposal")
        (other_copy / "document.txt").write_text("second proposal")
        first_artifact = self.store.publish(peer["id"])
        second_artifact = other.publish(other_peer["id"])

        def integrate_proposal(arguments):
            store, artifact = arguments
            try:
                return store.integrate(artifact["id"])["id"]
            except ValueError as error:
                self.assertIn("original workspace changed", str(error))
                return None

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(integrate_proposal, [(self.store, first_artifact), (other, second_artifact)]))
        self.assertEqual(1, sum(result is not None for result in results))
        self.assertIn((self.root / "document.txt").read_text(), {"first proposal", "second proposal"})

    def test_bridge_metadata_is_excluded_and_state_paths_cannot_be_published(self) -> None:
        peer = self.peer()
        copy = self.store.prepare(peer["id"])
        (copy / ".idea-peer").mkdir()
        (copy / ".idea-peer" / "request.json").write_text("bridge data")
        (copy / "document.txt").write_text("proposal")
        artifact = self.store.publish(peer["id"])
        self.assertEqual(["document.txt"], artifact["files"])
        (copy / "internal-state").mkdir()
        (copy / "internal-state" / "forum.sqlite3").write_text("must not enter original state")
        with self.assertRaisesRegex(ValueError, "cannot modify IDEA state"):
            self.store.publish(peer["id"])

    def test_attributes_and_peer_git_filters_do_not_change_snapshot_bytes(self) -> None:
        (self.root / ".gitattributes").write_text("* text eol=lf filter=evil\n")
        (self.root / "document.txt").write_bytes(b"original\r\nbytes\r\n")
        peer = self.peer()
        copy = self.store.prepare(peer["id"])
        self.assertEqual(b"original\r\nbytes\r\n", (copy / "document.txt").read_bytes())
        self.git("config", "filter.evil.clean", "false", cwd=copy)
        self.git("config", "filter.evil.required", "true", cwd=copy)
        (copy / "document.txt").write_bytes(b"updated\r\nbytes\r\n")
        artifact = self.store.publish(peer["id"])
        self.store.integrate(artifact["id"])
        self.assertEqual(b"updated\r\nbytes\r\n", (self.root / "document.txt").read_bytes())

    def test_symlinks_and_parent_traversal_are_rejected(self) -> None:
        outside = self.root.parent / "outside.txt"
        outside.write_text("outside")
        peer = self.peer()
        copy = self.store.prepare(peer["id"])
        (copy / "escape").symlink_to(outside)
        with self.assertRaises(ValueError):
            self.store.publish(peer["id"])
        (copy / "escape").unlink()
        (copy / "link").symlink_to("document.txt")
        with self.assertRaises(ValueError):
            self.store.publish(peer["id"])
        for path in ("../outside.txt", "/absolute", ".git/config", ".idea-peer/request", "folder\\escape"):
            with self.assertRaises(ValueError):
                self.store._safe_path(path, self.root)
        self.assertEqual("outside", outside.read_text())

    def test_concurrent_prepare_is_idempotent_and_missing_copies_are_not_replaced(self) -> None:
        peer = self.peer()
        self.store.configure()
        with ThreadPoolExecutor(max_workers=4) as executor:
            paths = list(executor.map(lambda _: self.store.prepare(peer["id"]), range(8)))
        self.assertEqual(1, len(set(paths)))
        self.assertEqual(1, self.store.summary()["prepared_count"])
        moved = paths[0].with_name("preserved-away")
        paths[0].rename(moved)
        with self.assertRaisesRegex(RuntimeError, "refusing to replace"):
            self.store.prepare(peer["id"])
        self.assertTrue(moved.is_dir())

    def test_shared_mode_and_registry_pagination(self) -> None:
        self.store.configure("shared")
        peer = self.peer()
        self.assertEqual(self.root, self.store.prepare(peer["id"]))
        self.assertEqual("shared", self.store.configure()["mode"])
        with self.assertRaises(ValueError):
            self.store.configure("isolated")
        artifacts = []
        for number in range(3):
            (self.root / "document.txt").write_text(f"version {number}")
            artifacts.append(self.store.publish(peer["id"], validation="Author's note"))
        first = self.store.list_artifacts(limit=1)
        rest = self.store.list_artifacts(after=first["next_cursor"])
        self.assertEqual({item["id"] for item in artifacts}, {item["id"] for item in first["items"] + rest["items"]})
        self.assertEqual(3, self.store.summary()["artifact_count"])
        other = self.forum.create_run("Another run", self.root)
        other_store = WorkspaceStore(self.forum, other["id"])
        with self.assertRaises(KeyError):
            other_store.get_artifact(artifacts[0]["id"])
        with self.assertRaises(ValueError):
            other_store.prepare(peer["id"])

    def test_session_migration_marker_survives_preview_restart_and_partial_preparation(self) -> None:
        first, second = self.peer("first"), self.peer("second")
        for peer in (first, second):
            self.forum.set_process_state(peer["id"], ProcessState.DORMANT,
                                         session_id="old-shared-" + peer["name"])
        self.store.configure("isolated")
        first_copy = self.store.prepare(first["id"])
        self.assertTrue(self.store.fresh_session_required(first["id"]))
        reopened = WorkspaceStore(self.forum, self.run["id"])
        self.assertEqual(first_copy, reopened.prepare(first["id"]))
        self.assertTrue(reopened.fresh_session_required(first["id"]))
        with self.assertRaisesRegex(ValueError, "clear the stored provider session"):
            reopened.acknowledge_session_reset(first["id"])
        self.assertTrue(reopened.fresh_session_required(first["id"]))
        self.forum.reset_process_observation(first["id"], clear_session=True)
        # A crash after clearing the old session leaves a harmless pending reset.
        reopened = WorkspaceStore(self.forum, self.run["id"])
        self.assertTrue(reopened.fresh_session_required(first["id"]))
        reopened.acknowledge_session_reset(first["id"])
        self.assertFalse(reopened.fresh_session_required(first["id"]))
        self.forum.set_process_state(first["id"], ProcessState.DORMANT, session_id="new-private-session")
        reopened.prepare(first["id"])
        self.assertFalse(reopened.fresh_session_required(first["id"]))
        self.assertIsNone(reopened.get(second["id"]))
        reopened.prepare(second["id"])
        self.assertTrue(reopened.fresh_session_required(second["id"]))

    def test_session_marker_migration_preserves_existing_copies_and_new_peers_need_no_reset(self) -> None:
        peer = self.peer()
        copy = self.store.prepare(peer["id"])
        self.assertFalse(self.store.fresh_session_required(peer["id"]))
        (copy / "document.txt").write_text("preserved private work")
        with self.forum._connection() as connection:
            connection.execute("""CREATE TABLE agent_workspaces_legacy AS
                SELECT agent_id,run_id,path,base_revision,created_at FROM agent_workspaces""")
            connection.execute("DROP TABLE agent_workspaces")
            connection.execute("ALTER TABLE agent_workspaces_legacy RENAME TO agent_workspaces")
        migrated = WorkspaceStore(self.forum, self.run["id"])
        self.assertFalse(migrated.fresh_session_required(peer["id"]))
        self.assertEqual(copy, migrated.prepare(peer["id"]))
        self.assertEqual("preserved private work", (copy / "document.txt").read_text())
        migrated.acknowledge_session_reset(peer["id"])

    def test_shared_workspaces_keep_their_existing_session(self) -> None:
        peer = self.peer()
        self.forum.set_process_state(peer["id"], ProcessState.DORMANT, session_id="shared-session")
        self.store.configure("shared")
        self.store.prepare(peer["id"])
        self.assertFalse(self.store.fresh_session_required(peer["id"]))
        self.assertEqual("shared-session", self.forum.get_agent(peer["id"])["session_id"])


if __name__ == "__main__":
    unittest.main()
