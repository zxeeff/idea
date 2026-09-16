#!/usr/bin/env python3
"""Opt-in, real-model smoke check of IDEA's provider permission configuration.

Running this script invokes the selected installed CLI and may incur model usage.
Only temporary workspaces, a temporary forum, and a loopback HTTP server are used.
No CLI sessions or probe logs are retained, and no existing IDEA run is opened.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
import json
import os
from pathlib import Path
import shlex
import signal
import sys
import tempfile
import time
from typing import Any
import uuid


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from idea.bridge import BridgeServer
from idea.commands import dispatch_forum
from idea.domain import AgentProfile, Effort, Provider
from idea.forum import Forum
from idea.providers import build_invocation


def _forum_body(token: str) -> str:
    return (
        token + "\nLiteral transport check: `inline` $(touch mcp-shell-expanded) "
        "${HOME} \"double\" 'single' \\tail 한글🙂"
    )


def _prompt(python: str, url: str, token: str, title: str) -> str:
    # Values here are exclusively generated probe values, never credentials.
    forum_body = _forum_body(token)
    code = "\n".join((
        "import json, os, pathlib, subprocess, sys, urllib.request",
        f"token = {token!r}",
        "assert os.environ['IDEA_PERMISSION_PROBE'] == token",
        "marker = pathlib.Path('permission-marker.txt')",
        "marker.write_text(token, encoding='utf-8')",
        "observed = marker.read_text(encoding='utf-8')",
        "assert observed == token",
        f"with urllib.request.urlopen({url!r}, timeout=10) as response:",
        "    network = response.read(256).decode()",
        "assert network == token",
        "def forum(*args):",
        "    result = subprocess.run([sys.executable, '-m', 'idea', 'forum', *args, '--json'],",
        "                            check=True, capture_output=True, text=True, timeout=20)",
        "    return json.loads(result.stdout)",
        "recent = forum('recent', '--limit', '10')",
        f"posted = next(item for item in recent['items'] if item['title'] == {title!r})",
        "read = forum('read', posted['id'])",
        f"assert read['body'] == {forum_body!r}",
        "pathlib.Path('probe-result.json').write_text(json.dumps({",
        "    'file': observed, 'environment': os.environ['IDEA_PERMISSION_PROBE'],",
        "    'http': network, 'thread_id': read['id'], 'forum_body': read['body'],",
        "}), encoding='utf-8')",
        "print('PERMISSION_PROBE_OK')",
    ))
    command = (
        f'test "$IDEA_PERMISSION_PROBE" = {shlex.quote(token)} && '
        f"{shlex.quote(python)} - <<'IDEA_PERMISSION_PROBE_PY'\n"
        f"{code}\nIDEA_PERMISSION_PROBE_PY"
    )
    return (
        "First call the IDEA `post` tool exactly once with these arguments:\n"
        + json.dumps({"title": title, "body": forum_body}, ensure_ascii=False)
        + "\nThen run this single shell command once using your command tool and finish with a short result. "
        "It checks file writing/reading, a synthetic environment variable, a local HTTP GET, and "
        "reads the structured post from the disposable IDEA forum. All files and forum data are temporary. "
        "Do not inspect other files, credentials, or environment variables; do not delegate or retry.\n\n"
        + command
    )


def _log_summary(path: Path) -> dict[str, Any]:
    """Return only known diagnostic categories; never echo arbitrary CLI output."""
    modes: set[str] = set()
    denials = 0
    categories: set[str] = set()
    events: Counter[str] = Counter()
    rate_statuses: Counter[str] = Counter()
    overage_statuses: Counter[str] = Counter()
    assistant_errors: Counter[str] = Counter()
    stop_reasons: Counter[str] = Counter()
    result_subtypes: Counter[str] = Counter()
    result_errors = 0
    tool_uses = 0
    patterns = {
        "unknown configuration field": "invalid_config_override",
        "permission mode forced to default": "permission_mode_forced_to_default",
        "permission denied": "tool_permission_denied",
        "operation not permitted": "operation_not_permitted",
        "requires approval": "approval_required",
        "not logged in": "not_authenticated",
        "authentication failed": "authentication_failed",
        "model is not supported": "model_unavailable",
        "model_not_found": "model_unavailable",
        "you've hit your limit": "rate_limited",
        "you’ve hit your limit": "rate_limited",
        "you have hit your limit": "rate_limited",
        "rate limit exceeded": "rate_limited",
        "model_refusal_no_fallback": "model_refusal",
    }
    known_events = {"system", "assistant", "user", "result", "error", "rate_limit_event", "thread.started",
                    "turn.started", "turn.completed", "turn.failed", "item.started", "item.completed"}
    known_statuses = {"allowed", "allowed_warning", "rejected"}
    known_errors = {"authentication_failed", "billing_error", "rate_limit", "rate_limit_error",
                    "invalid_request", "server_error", "unknown", "overloaded_error",
                    "permission_error", "not_found_error", "model_refusal_no_fallback"}
    known_results = {"success", "error_max_turns", "error_during_execution", "error_max_budget_usd",
                     "error_max_structured_output_retries", "error_during_invocation"}
    if not path.exists():
        return {"permission_modes": [], "permission_denials": 0, "categories": [], "events": {}}
    with path.open(encoding="utf-8", errors="replace") as stream:
        for line in stream:
            lower = line.lower()
            categories.update(category for pattern, category in patterns.items() if pattern in lower)
            try:
                value = json.loads(line)
            except (json.JSONDecodeError, RecursionError):
                continue
            if not isinstance(value, dict):
                continue
            kind = value.get("type")
            if isinstance(kind, str) and kind in known_events:
                events[kind] += 1
            if value.get("subtype") == "init":
                mode = value.get("permissionMode")
                if mode in {"default", "manual", "acceptEdits", "auto", "plan", "dontAsk", "bypassPermissions"}:
                    modes.add(mode)
            if value.get("subtype") == "permission_denied":
                denials += 1
            rate_info = value.get("rate_limit_info")
            if isinstance(rate_info, dict):
                status = rate_info.get("status")
                overage = rate_info.get("overageStatus", rate_info.get("overage_status"))
                if isinstance(status, str) and status in known_statuses:
                    rate_statuses[status] += 1
                if isinstance(overage, str) and overage in known_statuses:
                    overage_statuses[overage] += 1
                # Overage can be unavailable while the included quota is still
                # allowed; that does not reject this request.
                if status == "rejected":
                    categories.add("rate_limited")
            if kind == "assistant":
                error = value.get("error")
                if isinstance(error, dict):
                    error = error.get("type", error.get("code"))
                if isinstance(error, str):
                    assistant_errors[error if error in known_errors else "unrecognized"] += 1
                    if error in {"rate_limit", "rate_limit_error"}:
                        categories.add("rate_limited")
                    if error == "model_refusal_no_fallback":
                        categories.add("model_refusal")
            if kind == "result":
                subtype = value.get("subtype")
                result_subtypes[subtype if isinstance(subtype, str) and subtype in known_results else "unrecognized"] += 1
                if value.get("is_error") is True:
                    result_errors += 1
            message = value.get("message")
            if kind == "assistant" and isinstance(message, dict):
                reason = message.get("stop_reason")
                if isinstance(reason, str) and reason in {
                    "end_turn", "max_tokens", "stop_sequence", "tool_use", "pause_turn", "refusal",
                    "model_context_window_exceeded",
                }:
                    stop_reasons[reason] += 1
                content = message.get("content")
                if isinstance(content, list):
                    tool_uses += sum(isinstance(item, dict) and item.get("type") == "tool_use" for item in content)
            if value.get("stop_reason") == "refusal" or (
                isinstance(message, dict) and message.get("stop_reason") == "refusal"
            ):
                categories.add("model_refusal")
    return {"permission_modes": sorted(modes), "permission_denials": denials,
            "categories": sorted(categories), "events": dict(events),
            "rate_limit_statuses": dict(rate_statuses), "overage_statuses": dict(overage_statuses),
            "assistant_errors": dict(assistant_errors), "assistant_stop_reasons": dict(stop_reasons),
            "result_subtypes": dict(result_subtypes), "result_is_error_count": result_errors,
            "tool_use_count": tool_uses}


async def _stop_process_group(process: asyncio.subprocess.Process) -> None:
    # Each probe has a new session, so cleanup cannot signal the user's launcher.
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        await asyncio.wait_for(process.wait(), timeout=3)
    except asyncio.TimeoutError:
        pass
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    await process.wait()


async def check_provider(name: str, root: Path, timeout: float) -> dict[str, Any]:
    started = time.monotonic()
    provider = Provider.OPENAI if name == "codex" else Provider.ANTHROPIC
    model = "gpt-5.6-luna" if name == "codex" else "sonnet"
    profile = AgentProfile(f"probe-{name}", provider, model, Effort.LOW)
    workspace = root / name / "workspace"
    workspace.mkdir(parents=True)
    forum = Forum(root / name / "state")
    run = forum.create_run("Disposable provider permission check", workspace)
    run_id = str(run["id"])
    agent = forum.register_agent(run_id, profile)
    token = uuid.uuid4().hex
    title = f"Permission smoke {name}"
    successful_commands: Counter[str] = Counter()
    request_count = 0
    http_tasks: set[asyncio.Task[Any]] = set()

    def dispatch(agent_id: str, command: str, payload: dict[str, Any]) -> Any:
        result = dispatch_forum(forum, run_id, agent_id, command, payload)
        successful_commands[command] += 1
        return result

    async def serve_http(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        nonlocal request_count
        task = asyncio.current_task()
        if task is not None:
            http_tasks.add(task)
        try:
            header = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=2)
            if header.split(b"\r\n", 1)[0] != f"GET /{token} HTTP/1.1".encode():
                writer.write(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            else:
                request_count += 1
                body = token.encode()
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: "
                             + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n" + body)
            await writer.drain()
        except (asyncio.TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, ConnectionError):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass
            if task is not None:
                http_tasks.discard(task)

    server = await asyncio.start_server(serve_http, "127.0.0.1", 0, limit=8192)
    port = server.sockets[0].getsockname()[1]
    process: asyncio.subprocess.Process | None = None
    pump: asyncio.Task[None] | None = None
    waiter: asyncio.Task[int] | None = None
    timed_out = False
    failure: str | None = None
    log_path = root / name / "provider.jsonl"
    with BridgeServer(forum, run_id, dispatch, allowed_commands={"post", "read", "recent"}) as bridge:
        mailbox = bridge.register(str(agent["id"]), workspace)
        ready = asyncio.Event()

        async def poll_bridge() -> None:
            while True:
                bridge.poll()
                ready.set()
                await asyncio.sleep(0.05)

        try:
            pump = asyncio.create_task(poll_bridge())
            await asyncio.wait_for(ready.wait(), timeout=3)
            invocation = build_invocation(
                profile=profile, system_prompt="Complete only the disposable permission smoke check.",
                task_prompt=_prompt(sys.executable, f"http://127.0.0.1:{port}/{token}", token, title),
                workspace=workspace, state_dir=forum.state_dir, run_id=run_id, agent=agent,
                bridge_dir=mailbox,
            )
            extra = ("--ephemeral",) if name == "codex" else ("--no-session-persistence", "--max-turns", "5")
            argv = (*invocation.argv[:-1], *extra, invocation.argv[-1])
            environment = invocation.env | {"IDEA_PERMISSION_PROBE": token}
            with log_path.open("wb") as log:
                process = await asyncio.create_subprocess_exec(
                    *argv, cwd=invocation.cwd, env=environment,
                    stdin=asyncio.subprocess.DEVNULL, stdout=log, stderr=asyncio.subprocess.STDOUT,
                    start_new_session=True,
                )
                waiter = asyncio.create_task(process.wait())
                done, _ = await asyncio.wait({waiter, pump}, timeout=timeout,
                                             return_when=asyncio.FIRST_COMPLETED)
                if not done:
                    timed_out = True
                elif pump in done:
                    failure = "mailbox_poll_failed"
                else:
                    await waiter
        except FileNotFoundError:
            failure = "executable_missing"
        except Exception as error:
            # Exception text can contain inherited account data; print only its type.
            failure = f"probe_error:{type(error).__name__}"
        finally:
            if process is not None:
                await _stop_process_group(process)
            for task in (waiter, pump):
                if task is not None and not task.done():
                    task.cancel()
            await asyncio.gather(*(task for task in (waiter, pump) if task is not None), return_exceptions=True)
            server.close()
            await server.wait_closed()
            for task in tuple(http_tasks):
                task.cancel()
            await asyncio.gather(*tuple(http_tasks), return_exceptions=True)

    try:
        observation = json.loads((workspace / "probe-result.json").read_text(encoding="utf-8"))
        if not isinstance(observation, dict):
            observation = {}
    except (OSError, ValueError):
        observation = {}
    try:
        file_matches = (workspace / "permission-marker.txt").read_text(encoding="utf-8") == token
    except OSError:
        file_matches = False
    threads = forum.list_threads(run_id, limit=10)
    forum_body = _forum_body(token)
    matching_threads = [row for row in threads if row["title"] == title and row["body"] == forum_body
                        and row["author"] == profile.name]
    log_summary = _log_summary(log_path)
    checks = {
        "file_write_read": file_matches and observation.get("file") == token,
        "environment": observation.get("environment") == token,
        "loopback_http": request_count > 0 and observation.get("http") == token,
        "forum_post": len(matching_threads) == 1 and successful_commands["post"] == 1,
        "forum_text_exact": observation.get("forum_body") == forum_body,
        "forum_shell_not_expanded": not (workspace / "mcp-shell-expanded").exists(),
        "forum_read": successful_commands["recent"] == 1 and successful_commands["read"] == 1
                      and any(row["id"] == observation.get("thread_id") for row in matching_threads),
        "no_permission_denials": log_summary["permission_denials"] == 0,
    }
    if name == "claude":
        checks["bypass_permissions"] = log_summary["permission_modes"] == ["bypassPermissions"]
    exit_code = process.returncode if process is not None else None
    return {
        "provider": name, "model": model, "effort": "low",
        "ok": not failure and not timed_out and exit_code == 0 and all(checks.values()),
        "exit_code": exit_code, "timed_out": timed_out, "failure": failure,
        "elapsed_seconds": round(time.monotonic() - started, 2), "checks": checks,
        "http_requests": request_count, "forum_commands": dict(successful_commands),
        "diagnostics": log_summary,
    }


async def _run(args: argparse.Namespace) -> list[dict[str, Any]]:
    names = ("codex", "claude") if args.provider == "both" else (args.provider,)
    results = []
    with tempfile.TemporaryDirectory(prefix="idea-provider-permissions-") as directory:
        for name in names:
            result = await check_provider(name, Path(directory).resolve(), args.timeout)
            results.append(result)
            if result["failure"] == "executable_missing":
                break
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=("codex", "claude", "both"), default="both")
    parser.add_argument("--timeout", type=float, default=120, help="Deadline per CLI in seconds, at most 120 (default: 120)")
    parser.add_argument("--json", action="store_true", help="Print a machine-readable result without raw provider output")
    args = parser.parse_args()
    if not 0 < args.timeout <= 120:
        parser.error("--timeout must be greater than zero and at most 120")
    try:
        results = asyncio.run(_run(args))
    except KeyboardInterrupt:
        return 130
    except Exception as error:
        result = {"ok": False, "failure": f"setup_error:{type(error).__name__}"}
        print(json.dumps(result) if args.json else result["failure"])
        return 1
    ok = all(item["ok"] for item in results)
    if args.json:
        print(json.dumps({"ok": ok, "results": results}, ensure_ascii=False, indent=2))
    else:
        for result in results:
            status = "PASS" if result["ok"] else "FAIL"
            missing = ", ".join(key for key, passed in result["checks"].items() if not passed)
            print(f"{result['provider']}: {status} ({result['elapsed_seconds']}s, exit={result['exit_code']})")
            if missing:
                print(f"  Unverified checks: {missing}")
            if result["failure"] or result["timed_out"]:
                print(f"  Failure: {result['failure'] or 'deadline_exceeded'}")
            if not result["ok"]:
                print("  Diagnostics: " + json.dumps(result["diagnostics"], ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
