from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import threading
import unittest
import uuid
from pathlib import Path

from idea.bridge import (
    DEFAULT_COMMANDS,
    BridgeClient,
    BridgeError,
    BridgeRemoteError,
    BridgeServer,
    BridgeTimeoutError,
)
from idea.domain import AgentProfile, Effort, Provider
from idea.forum import Forum


class BridgeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.forum = Forum(self.root / "state")
        self.run = self.forum.create_run("Isolated peer forum", self.root)
        self.agents = [
            self.forum.register_agent(
                self.run["id"], AgentProfile(f"peer-{n}", Provider.OPENAI, "test", Effort.HIGH)
            ) for n in range(2)
        ]
        self.workspaces = [self.root / f"workspace-{n}" for n in range(2)]
        for path in self.workspaces:
            path.mkdir()
        self.calls: list[tuple[str, str, dict]] = []

        def handler(agent_id: str, command: str, payload: dict):
            self.calls.append((agent_id, command, payload))
            return {"agent": agent_id, "command": command, "payload": payload}

        self.handler = handler
        self.server = BridgeServer(self.forum, self.run["id"], handler)
        self.paths = [
            self.server.register(agent["id"], path)
            for agent, path in zip(self.agents, self.workspaces)
        ]

    def tearDown(self) -> None:
        self.server.close()
        self.temp.cleanup()

    def request(self, number: int = 0, *, command="peers", payload=None, identifier=None) -> str:
        identifier = identifier or uuid.uuid4().hex
        data = {"version": 1, "id": identifier, "command": command, "payload": payload or {}}
        (self.paths[number] / "requests" / f"{identifier}.json").write_text(json.dumps(data))
        return identifier

    def response(self, identifier: str, number: int = 0) -> dict:
        return json.loads((self.paths[number] / "responses" / f"{identifier}.json").read_text())

    def exchange(self, client: BridgeClient, command: str, payload: dict, **kwargs):
        result: dict = {}
        finished = threading.Event()

        def call():
            try:
                result["value"] = client.call(command, payload, **kwargs)
            except BaseException as error:
                result["error"] = error
            finally:
                finished.set()

        worker = threading.Thread(target=call)
        worker.start()
        while not finished.wait(0.01):
            self.server.poll()
        worker.join(timeout=1)
        if "error" in result:
            raise result["error"]
        return result["value"]

    def test_real_client_roundtrip_cleans_response_and_uses_registered_identity(self) -> None:
        value = self.exchange(BridgeClient(self.paths[0], timeout=2), "post", {"title": "Hello", "body": "Shared evidence"})
        self.assertEqual(self.agents[0]["id"], value["agent"])
        self.assertEqual("post", value["command"])
        self.assertEqual([], list((self.paths[0] / "requests").iterdir()))
        self.assertEqual([], list((self.paths[0] / "responses").iterdir()))
        self.assertEqual(0o700, self.paths[0].stat().st_mode & 0o777)

    def test_request_identity_and_command_override_are_rejected_server_side(self) -> None:
        for field in ("agent_id", "author", "run_id", "run", "state_dir"):
            with self.subTest(field=field):
                identifier = self.request(payload={field: self.agents[1]["id"]})
                self.assertEqual(1, self.server.poll())
                self.assertFalse(self.response(identifier)["ok"])
        for command in ("integrate", "run", "unknown", "calls"):
            identifier = self.request(command=command)
            self.server.poll()
            self.assertFalse(self.response(identifier)["ok"])
        self.assertEqual([], self.calls)
        with self.assertRaises(ValueError):
            BridgeServer(self.forum, self.run["id"], self.handler, allowed_commands={"integrate"})

    def test_extra_commands_require_explicit_allowlist(self) -> None:
        self.server.close()
        self.server = BridgeServer(self.forum, self.run["id"], self.handler, allowed_commands=DEFAULT_COMMANDS | {"custom"})
        self.server.register(self.agents[0]["id"], self.workspaces[0])
        identifier = self.request(command="custom", payload={"reason": "Independent approach"})
        self.server.poll()
        self.assertTrue(self.response(identifier)["ok"])

    def test_duplicate_request_is_replayed_after_server_restart_without_second_dispatch(self) -> None:
        identifier = self.request(command="post", payload={"body": "Once"})
        self.server.poll()
        original = self.response(identifier)
        self.server.close()
        self.server = BridgeServer(self.forum, self.run["id"], self.handler)
        self.server.register(self.agents[0]["id"], self.workspaces[0])
        self.request(command="post", payload={"body": "Once"}, identifier=identifier)
        self.server.poll()
        self.assertEqual(original, self.response(identifier))
        self.assertEqual(1, len(self.calls))
        self.request(command="post", payload={"body": "Different"}, identifier=identifier)
        self.server.poll()
        self.assertEqual("request_conflict", self.response(identifier)["error"]["code"])
        self.assertEqual(1, len(self.calls))

    def test_interrupted_mutation_is_never_automatically_repeated(self) -> None:
        class SimulatedCrash(BaseException):
            pass

        def interrupted(agent_id, command, payload):
            self.calls.append((agent_id, command, payload))
            self.forum.create_thread(self.run["id"], "peer-0", "Already committed", "Result")
            raise SimulatedCrash()

        self.server.handler = interrupted
        identifier = self.request(command="post", payload={"body": "Committed before crash"})
        with self.assertRaises(SimulatedCrash):
            self.server.poll()
        self.server.close()
        self.server = BridgeServer(self.forum, self.run["id"], self.handler)
        self.server.register(self.agents[0]["id"], self.workspaces[0])
        self.server.poll()
        self.assertEqual("outcome_unknown", self.response(identifier)["error"]["code"])
        self.assertEqual(1, len(self.calls))
        self.assertEqual(1, self.forum.count_threads(self.run["id"]))

    def test_timeout_provides_retry_id_and_same_operation_is_not_duplicated(self) -> None:
        client = BridgeClient(self.paths[0], timeout=0.03, poll_interval=0.01)
        with self.assertRaises(BridgeTimeoutError) as timed_out:
            client.call("post", {"body": "Once"})
        self.server.poll()
        result = self.exchange(client, "post", {"body": "Once"}, request_id=timed_out.exception.request_id)
        self.assertEqual("post", result["command"])
        self.server.poll()
        self.assertEqual(1, len(self.calls))
        with self.assertRaises(BridgeError):
            self.exchange(client, "post", {"body": "Changed"}, request_id=timed_out.exception.request_id)

    def test_response_expiry_keeps_dedup_tombstone(self) -> None:
        identifier = self.request(command="post")
        self.server.poll()
        self.assertEqual(1, self.server.prune_responses(older_than="9999"))
        self.assertFalse((self.paths[0] / "responses" / f"{identifier}.json").exists())
        self.request(command="post", identifier=identifier)
        self.server.poll()
        self.assertEqual("response_expired", self.response(identifier)["error"]["code"])
        self.assertEqual(1, len(self.calls))

    def test_peer_mailboxes_and_request_ids_are_isolated(self) -> None:
        identifier = self.request(0, payload={"query": "one"})
        self.request(1, payload={"query": "two"}, identifier=identifier)
        self.assertEqual(2, self.server.poll())
        self.assertEqual(self.agents[0]["id"], self.response(identifier, 0)["result"]["agent"])
        self.assertEqual(self.agents[1]["id"], self.response(identifier, 1)["result"]["agent"])
        with self.assertRaises(BridgeError):
            self.server.register(self.agents[1]["id"], self.workspaces[0])
        other_run = self.forum.create_run("Other run", self.root)
        other = self.forum.register_agent(other_run["id"], AgentProfile("other", Provider.OPENAI, "test", Effort.HIGH))
        with self.assertRaises(BridgeError):
            self.server.register(other["id"], self.workspaces[0])

    def test_overlapping_workspace_registration_is_rejected_without_creating_mailbox(self) -> None:
        extra = self.forum.register_agent(self.run["id"], AgentProfile("nested", Provider.OPENAI, "test", Effort.HIGH))
        nested = self.workspaces[0] / "nested"
        nested.mkdir()
        with self.assertRaises(BridgeError):
            self.server.register(extra["id"], nested)
        self.assertFalse((nested / ".idea-peer").exists())

    def test_symlink_request_and_response_do_not_read_or_overwrite_target(self) -> None:
        secret = self.root / "outside.txt"
        secret.write_text("host data")
        identifier = uuid.uuid4().hex
        (self.paths[0] / "requests" / f"{identifier}.json").symlink_to(secret)
        self.server.poll()
        self.assertFalse(self.response(identifier)["ok"])
        self.assertEqual([], self.calls)
        self.assertEqual("host data", secret.read_text())
        identifier = self.request()
        (self.paths[0] / "responses" / f"{identifier}.json").symlink_to(secret)
        self.server.poll()
        self.assertTrue(self.response(identifier)["ok"])
        self.assertEqual("host data", secret.read_text())

    def test_hardlinked_request_is_rejected(self) -> None:
        identifier = self.request()
        request = self.paths[0] / "requests" / f"{identifier}.json"
        os.link(request, self.root / "hardlink.json")
        self.server.poll()
        self.assertFalse(self.response(identifier)["ok"])
        self.assertEqual([], self.calls)

    def test_fifo_request_does_not_block_and_traversal_id_is_rejected(self) -> None:
        identifier = uuid.uuid4().hex
        os.mkfifo(self.paths[0] / "requests" / f"{identifier}.json")
        self.assertEqual(1, self.server.poll())
        self.assertFalse(self.response(identifier)["ok"])
        with self.assertRaises(BridgeError):
            BridgeClient(self.paths[0]).call("peers", {}, request_id="../other")
        self.assertEqual([], self.calls)

    def test_duplicate_json_keys_and_invalid_unicode_are_rejected(self) -> None:
        identifier = uuid.uuid4().hex
        request = self.paths[0] / "requests" / f"{identifier}.json"
        request.write_text('{"version":1,"id":"' + identifier + '","command":"peers","command":"post","payload":{}}')
        self.server.poll()
        self.assertFalse(self.response(identifier)["ok"])
        identifier = self.request(payload={"query": "\ud800"})
        self.server.poll()
        self.assertFalse(self.response(identifier)["ok"])
        self.assertEqual([], self.calls)

    def test_equivalent_json_key_order_keeps_same_request_identity(self) -> None:
        identifier = self.request(command="post", payload={"title": "Same", "body": "Same"})
        self.server.poll()
        self.request(command="post", payload={"body": "Same", "title": "Same"}, identifier=identifier)
        self.server.poll()
        self.assertTrue(self.response(identifier)["ok"])
        self.assertEqual(1, len(self.calls))

    def test_swapping_queue_directory_cannot_impersonate_another_peer(self) -> None:
        identifier = self.request(1)
        queue = self.paths[0] / "requests"
        queue.rename(self.paths[0] / "old-requests")
        queue.symlink_to(self.paths[1] / "requests", target_is_directory=True)
        self.assertEqual(1, self.server.poll())
        self.assertEqual(self.agents[1]["id"], self.calls[0][0])
        self.assertFalse((self.paths[0] / "responses" / f"{identifier}.json").exists())
        with self.assertRaises(BridgeError):
            BridgeClient(self.paths[0]).call("peers", {})

    def test_replaced_regular_queue_directory_is_rejected_even_after_restart(self) -> None:
        queue = self.paths[0] / "requests"
        queue.rename(self.paths[0] / "old-requests")
        queue.mkdir()
        self.request()
        self.assertEqual(0, self.server.poll())
        self.assertEqual([], self.calls)
        self.server.close()
        self.server = BridgeServer(self.forum, self.run["id"], self.handler)
        with self.assertRaises(BridgeError):
            self.server.register(self.agents[0]["id"], self.workspaces[0])

    def test_symlink_mailbox_and_workspace_are_rejected_on_registration(self) -> None:
        self.server.close()
        self.server = BridgeServer(self.forum, self.run["id"], self.handler)
        link = self.root / "linked-workspace"
        link.symlink_to(self.workspaces[0], target_is_directory=True)
        with self.assertRaises(BridgeError):
            self.server.register(self.agents[0]["id"], link)
        mailbox = self.paths[0]
        mailbox.rename(self.workspaces[0] / "old-mailbox")
        mailbox.symlink_to(self.paths[1], target_is_directory=True)
        with self.assertRaises(BridgeError):
            self.server.register(self.agents[0]["id"], self.workspaces[0])

    def test_attachment_contents_are_bounded_and_paths_are_never_accepted(self) -> None:
        encoded = base64.b64encode(b"evidence\x00bytes").decode()
        identifier = self.request(command="attach", payload={"filename": "evidence.bin", "data_base64": encoded})
        self.server.poll()
        self.assertTrue(self.response(identifier)["ok"])
        for payload in (
            {"path": "/outside/file", "filename": "evidence.bin", "data_base64": encoded},
            {"filename": "../outside", "data_base64": encoded},
            {"filename": "nested/file", "data_base64": encoded},
            {"filename": "nested\\file", "data_base64": encoded},
            {"filename": "file", "data_base64": "not base64!"},
            {"filename": "file", "data_base64": "a" * (6 * 1024 * 1024)},
        ):
            identifier = self.request(command="attach", payload=payload)
            self.server.poll()
            self.assertFalse(self.response(identifier)["ok"])
        self.assertEqual(1, len(self.calls))

    def test_request_response_and_json_depth_limits(self) -> None:
        self.server.max_bytes = 1024
        identifier = self.request(payload={"query": "x" * 2000})
        self.server.poll()
        self.assertFalse(self.response(identifier)["ok"])
        deep = {}
        for _ in range(30):
            deep = {"nested": deep}
        identifier = self.request(payload=deep)
        self.server.poll()
        self.assertFalse(self.response(identifier)["ok"])
        self.server.handler = lambda *_: {"huge": "x" * 2000}
        identifier = self.request()
        self.server.poll()
        self.assertEqual("command_rejected", self.response(identifier)["error"]["code"])
        self.assertEqual([], self.calls)
        with self.assertRaises(BridgeError):
            BridgeClient(self.paths[0], max_bytes=1024).call("post", {"body": "x" * 2000})

    def test_private_metadata_and_arbitrary_exception_details_are_not_exposed(self) -> None:
        self.server.handler = lambda *_: {
            "items": [{"name": "peer", "pid": 999, "session_id": "provider-secret", "patch_path": "/host/private.patch"}],
            "workspace": "/host/control", "stored_path": "/host/attachment", "body": "public evidence",
        }
        value = self.exchange(BridgeClient(self.paths[0], timeout=2), "peers", {})
        self.assertEqual({"items": [{"name": "peer"}], "body": "public evidence"}, value)

        def failure(*_):
            raise ValueError("secret session at /host/private")

        self.server.handler = failure
        with self.assertRaises(BridgeRemoteError) as failed:
            self.exchange(BridgeClient(self.paths[0], timeout=2), "peers", {})
        self.assertEqual("command_failed", failed.exception.code)
        self.assertNotIn("secret", str(failed.exception))

    def test_batch_limit_and_round_robin_prevent_one_peer_starvation(self) -> None:
        self.server.max_batch = 1
        for _ in range(5):
            self.request(0)
        second = self.request(1)
        self.assertEqual(1, self.server.poll())
        self.assertEqual(1, self.server.poll())
        self.assertTrue(self.response(second, 1)["ok"])

    def test_500_mailboxes_work_with_64_file_descriptor_limit(self) -> None:
        source = str(Path(__file__).resolve().parents[1] / "src")
        code = textwrap.dedent("""
            import json, resource, sys, tempfile, uuid
            from pathlib import Path
            sys.path.insert(0, sys.argv[1])
            from idea.bridge import BridgeServer
            from idea.domain import AgentProfile, Effort, Provider
            from idea.forum import Forum

            soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
            limit = min(64, hard) if hard != resource.RLIM_INFINITY else 64
            resource.setrlimit(resource.RLIMIT_NOFILE, (limit, hard))
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                forum = Forum(root / 'state')
                run = forum.create_run('500 isolated mailboxes', root)
                calls = []
                def handler(agent_id, command, payload):
                    calls.append(agent_id)
                    return {'agent': agent_id}
                with BridgeServer(forum, run['id'], handler, max_batch=73) as server:
                    for number in range(500):
                        agent = forum.register_agent(run['id'], AgentProfile(f'peer-{number}', Provider.OPENAI, 'test', Effort.HIGH))
                        workspace = root / f'workspace-{number}'
                        workspace.mkdir()
                        mailbox = server.register(agent['id'], workspace)
                        identifier = uuid.uuid4().hex
                        (mailbox / 'requests' / f'{identifier}.json').write_text(json.dumps({
                            'version':1, 'id':identifier, 'command':'peers', 'payload':{},
                        }))
                    for _ in range(10):
                        server.poll()
                    assert len(calls) == len(set(calls)) == 500, len(calls)
                print(json.dumps({'processed':len(calls), 'fd_limit':limit}))
        """)
        result = subprocess.run([sys.executable, "-c", code, source], capture_output=True, text=True, timeout=45)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual({"processed": 500, "fd_limit": 64}, json.loads(result.stdout))


if __name__ == "__main__":
    unittest.main()
