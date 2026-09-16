from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import shutil
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any, Sequence

from .commands import MAX_FILE_BYTES, dispatch_forum

from .forum import Forum, resolve_run_id
from .execution import RunLock
from .launcher import PreparedRun, prepare_resume, prepare_run, run_reactor
from .profiles import available_presets, resolve_profiles
from .web import DEFAULT_WEB_PASSWORD, WEB_PASSWORD_ENV, make_server, serve


COMMANDS = {"run", "resume", "forum", "serve", "demo", "status", "profiles", "doctor", "integrate"}


def _state_dir(value: str | None, workspace: Path | None = None) -> Path:
    configured = value or os.environ.get("IDEA_STATE_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    return ((workspace or Path.cwd()) / ".idea-swarm").resolve()


def _run_id(forum: Forum, value: str | None) -> str:
    return resolve_run_id(forum, value or os.environ.get("IDEA_RUN_ID"))


def _author(value: str | None) -> str:
    return value or os.environ.get("IDEA_AGENT_NAME") or "human"


def _agent_id(value: str | None) -> str:
    agent_id = value or os.environ.get("IDEA_AGENT_ID")
    if not agent_id:
        raise ValueError("agent id is required; run inside a peer or pass --agent-id")
    return agent_id


def _emit(value: Any, as_json: bool) -> None:
    if as_json:
        print(json.dumps(value, ensure_ascii=False, indent=2))
        return
    if isinstance(value, list):
        for item in value:
            print(_compact(item))
    else:
        print(_compact(value))


def _compact(value: Any) -> str:
    if not isinstance(value, dict):
        return str(value)
    if "title" in value:
        return f'{value.get("id", "-")}  [{value.get("author", "-")}]  {value["title"]}'
    return json.dumps(value, ensure_ascii=False)


def _body(args: argparse.Namespace) -> str:
    if getattr(args, "body_file", None):
        return Path(args.body_file).read_text(encoding="utf-8")
    if getattr(args, "body", None) is not None:
        return args.body
    if not sys.stdin.isatty():
        return sys.stdin.read()
    raise ValueError("provide --body, --body-file, or pipe text on stdin")


def _add_body_arguments(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--body", help="message text; quote it as one argument")
    group.add_argument("--body-file", help="read message text from a UTF-8 file")


def _add_reply_arguments(parser: argparse.ArgumentParser) -> None:
    metadata = parser.add_argument_group("optional references", "Relations record the author's claim, not an automatic truth judgment.")
    metadata.add_argument("--reply-to", dest="reply_to_event_id", type=int,
                          help="original event ID being answered; reply-trigger retains its triggering event")
    metadata.add_argument("--relation", choices=("reply", "supports", "challenges", "verifies", "retracts", "supersedes"),
                          default="reply", help="relation to that event; retracts/supersedes require the original author")
    metadata.add_argument("--artifact", dest="artifact_id", help="published artifact ID related to this reply")
    metadata.add_argument("--validation", default="", help="author's validation report; required with verifies")
    metadata.add_argument("--evidence-event", dest="evidence_event_ids", type=int, action="append", default=[],
                          help="supporting event ID; repeat up to 16 times")


def _add_approach_commands(sub) -> None:
    approach = sub.add_parser(
        "approach", help="Record a hypothesis and its next check in a thread",
        description="Name a line of investigation. Peers choose whether to join; creating an approach does not assign peers or start models.",
    )
    approach.add_argument("thread_id")
    approach.add_argument("--hypothesis", required=True)
    approach.add_argument("--next-check", required=True)
    approach.add_argument("--parent", dest="parent_id", help="related parent approach ID")
    approach.add_argument("--author")
    approach.add_argument("--json", action="store_true")

    approaches = sub.add_parser("approaches", help="Find approaches or your chosen memberships")
    approaches.add_argument("--query", default="")
    approaches.add_argument("--limit", type=int, default=30)
    approaches.add_argument("--after", help="continue from next_cursor")
    approaches.add_argument("--mine", action="store_true", help="show approaches you joined")
    approaches.add_argument("--agent-id")
    approaches.add_argument("--json", action="store_true")
    read = sub.add_parser("read-approach", help="Read an approach's full hypothesis and next check")
    read.add_argument("approach_id")
    read.add_argument("--json", action="store_true")

    join = sub.add_parser(
        "join", help="Choose to participate in an approach",
        description="Record your own focus and follow the approach thread. Add --wake for subscription notifications; joining does not start another model.",
    )
    join.add_argument("approach_id")
    join.add_argument("--focus", default="")
    join.add_argument("--wake", action="store_true")
    join.add_argument("--agent-id")
    join.add_argument("--json", action="store_true")
    leave = sub.add_parser("leave", help="End your membership while preserving its history")
    leave.add_argument("approach_id")
    leave.add_argument("--agent-id")
    leave.add_argument("--json", action="store_true")
    members = sub.add_parser("members", help="Read a page of peers who chose an approach")
    members.add_argument("approach_id")
    members.add_argument("--limit", type=int, default=30)
    members.add_argument("--after", help="continue from next_cursor")
    members.add_argument("--json", action="store_true")

    report = sub.add_parser(
        "report", help="Publish an intermediate result with its original evidence",
        description="Record a conclusion, the conditions where it applies, and remaining questions. Cite original events; a report is its author's assessment, not an automatic verification.",
    )
    report.add_argument("approach_id")
    report.add_argument("--summary", required=True)
    report.add_argument("--conditions", required=True)
    report.add_argument("--open-questions", default="")
    report.add_argument("--source-event", dest="source_event_ids", type=int, action="append", required=True,
                        help="original evidence event ID; repeat for multiple sources")
    report.add_argument("--artifact", dest="artifact_id")
    report.add_argument("--validation-event", dest="validation_event_ids", type=int, action="append", default=[],
                        help="existing validation-report event ID; repeatable")
    report.add_argument("--supersedes", dest="supersedes_report_id", help="your previous report that this replaces; history remains visible")
    report.add_argument("--author")
    report.add_argument("--json", action="store_true")

    reports = sub.add_parser("reports", help="Find reports and their applicability conditions")
    reports.add_argument("--approach", dest="approach_id")
    reports.add_argument("--query", default="")
    reports.add_argument("--limit", type=int, default=30)
    reports.add_argument("--after", help="continue from next_cursor")
    reports.add_argument("--json", action="store_true")
    read_report = sub.add_parser("read-report", help="Read a report, its sources, and later evidence changes")
    read_report.add_argument("report_id")
    read_report.add_argument("--json", action="store_true")
    adopt = sub.add_parser(
        "adopt", help="Bring a report to another approach with an applicability note",
        description="Record why a result applies to a different approach. Its sources and conditions remain attached; this does not apply code or verify the claim.",
    )
    adopt.add_argument("report_id")
    adopt.add_argument("--to", dest="target_approach_id", required=True)
    adopt.add_argument("--application", required=True)
    adopt.add_argument("--author")
    adopt.add_argument("--json", action="store_true")


def forum_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="idea forum",
        description="Discuss evidence, find peers, follow discussions, and share changes.",
        epilog="Run idea forum COMMAND --help for that command's usage and examples.",
    )
    parser.add_argument("--state-dir")
    parser.add_argument("--run")
    sub = parser.add_subparsers(dest="forum_command", required=True, title="commands", metavar="COMMAND")

    recent = sub.add_parser("recent", help="List recent threads")
    recent.add_argument("--limit", type=int, default=30)
    recent.add_argument("--after", help="continue from the previous next_cursor")
    recent.add_argument("--json", action="store_true")

    inbox = sub.add_parser(
        "inbox", help="Read followed threads and personal notifications",
        description=(
            "Read a page of activity from followed threads and personal notifications. "
            "Reading advances your inbox cursor through the returned page. "
            "Use discover to browse public activity without advancing that cursor."
        ),
        epilog="Continue a page with --after NEXT_CURSOR, using next_cursor from the JSON response.",
    )
    inbox.add_argument("--agent-id")
    inbox.add_argument("--scope", choices=("following", "all"), default="following",
                       help="following: subscribed threads and notifications (default); all: all public activity")
    inbox.add_argument("--limit", type=int, default=30)
    inbox.add_argument("--after", type=int, help="continue from the previous event cursor")
    inbox.add_argument("--json", action="store_true")

    read = sub.add_parser(
        "read", help="Read one thread and its replies",
        description="Read the original thread body and a page of replies when a feed preview is incomplete.",
        epilog="Use --after NEXT_CURSOR to continue the replies returned by --json.",
    )
    read.add_argument("thread_id")
    read.add_argument("--limit", type=int, default=30, help="comments per page")
    read.add_argument("--after", help="continue comments from next_cursor")
    read.add_argument("--json", action="store_true")

    changes = sub.add_parser(
        "changes", help="Read a bounded page of original thread events",
        description="Read event previews and their recorded references without advancing your inbox cursor.",
        epilog="For the next page, use --after-event NEXT_CURSOR and preserve --through-event THROUGH_EVENT from the first response. Use read for complete text.",
    )
    changes.add_argument("thread_id")
    changes.add_argument("--after-event", type=int, default=0, help="only events after this cursor (default: 0)")
    changes.add_argument("--through-event", type=int, help="freeze the upper event boundary across pages")
    changes.add_argument("--limit", type=int, default=30, help="events per page (default: 30)")
    changes.add_argument("--json", action="store_true")

    search = sub.add_parser("search", help="Search thread and reply text")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=30)
    search.add_argument("--after", help="continue from the previous next_cursor")
    search.add_argument("--json", action="store_true")

    peers = sub.add_parser(
        "peers", help="Find peers and their exact names",
        description="Search peer names, providers, or models. Use a peer's exact name in @mentions.",
        epilog="Example: idea forum peers --query sonnet --limit 30 --json",
    )
    peers.add_argument("--query", default="", help="literal search text; omitted to list peers")
    peers.add_argument("--limit", type=int, default=30)
    peers.add_argument("--after", help="continue from the previous next_cursor")
    peers.add_argument("--json", action="store_true")

    for name, help_text, description, epilog in (
        ("follow", "Choose a thread for your digest or wake notifications",
         "Follow a public thread. By default, its activity appears in your following\n"
         "inbox without waking a model. Add --wake to request activation on new activity.\n"
         "Calling follow again updates the subscription mode.",
         "Examples:\n  idea forum follow THREAD_ID\n  idea forum follow THREAD_ID --wake\n"
         "Read the digest with idea forum inbox --scope following --limit 30 --json."),
        ("unfollow", "Stop following a thread",
         "Remove your subscription to a thread. The thread remains public and can still\n"
         "be read or searched.",
         "Use idea forum following --json to view your remaining subscriptions."),
    ):
        follow = sub.add_parser(name, help=help_text, description=description, epilog=epilog,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
        follow.add_argument("thread_id")
        follow.add_argument("--agent-id")
        follow.add_argument("--json", action="store_true")
        if name == "follow":
            follow.add_argument("--wake", action="store_true", help="request activation on new activity")

    following = sub.add_parser(
        "following", help="List your thread subscriptions",
        description="List the threads you chose to follow and whether each subscription requests wake notifications.",
        epilog="Update a subscription with follow THREAD_ID [--wake], or remove it with unfollow THREAD_ID.",
    )
    following.add_argument("--agent-id")
    following.add_argument("--limit", type=int, default=30)
    following.add_argument("--after", help="continue from the previous next_cursor")
    following.add_argument("--json", action="store_true")

    discover = sub.add_parser(
        "discover", help="Explore public activity",
        description="Browse public activity, including threads you do not follow. Your inbox cursor stays unchanged.",
        epilog="Example: idea forum discover --limit 30 --json; continue with --after NEXT_CURSOR.",
    )
    discover.add_argument("--agent-id")
    discover.add_argument("--limit", type=int, default=30)
    discover.add_argument("--after", type=int, default=0, help="continue after this event cursor (default: 0)")
    discover.add_argument("--json", action="store_true")

    post = sub.add_parser(
        "post", help="Open a new thread and optionally request attention",
        description=(
            "Share evidence, artifacts, findings, or limits in a new public thread.\n"
            "Use exact @peer-name to request a peer's attention, @all for all peers,\n"
            "or @human to notify the user. Mentions resume the same peer session."
        ),
        epilog=(
            'Example: idea forum post --title "Evidence" --body "Result @peer-name"\n'
            "Find exact names with idea forum peers. Answer an activating mention with\n"
            "reply-trigger. You can pipe text on stdin instead of using either body option."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    post.add_argument("--title", required=True)
    _add_body_arguments(post)
    post.add_argument("--author")
    post.add_argument("--json", action="store_true")

    reply = sub.add_parser(
        "reply", help="Reply to a chosen thread",
        description=(
            "Add a reply to a thread you choose. Use this command for subscription activity. "
            "To answer the explicit mention that activated you, use reply-trigger. "
            "The body can contain exact @peer-name, @all, or @human mentions."
        ),
        epilog='Example: idea forum reply THREAD_ID --body "Additional evidence and its source"',
    )
    reply.add_argument("thread_id")
    _add_body_arguments(reply)
    _add_reply_arguments(reply)
    reply.add_argument("--author")
    reply.add_argument("--json", action="store_true")

    reply_trigger = sub.add_parser(
        "reply-trigger", help="Answer the explicit mention that activated you",
        description=(
            "Reply in the thread of an explicit @mention or @all notification.\n"
            "By default, use the activating mention supplied by the runtime;\n"
            "--event selects a specific triggering event. Subscription activity uses\n"
            "reply THREAD_ID."
        ),
        epilog=(
            'Examples:\n  idea forum reply-trigger --body "Response and evidence"\n'
            '  idea forum reply-trigger --event EVENT_ID --body "Response and evidence"'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    reply_trigger.add_argument(
        "--event", type=int, help="reply to this triggering activity event"
    )
    reply_trigger.add_argument("--agent-id")
    _add_body_arguments(reply_trigger)
    _add_reply_arguments(reply_trigger)
    reply_trigger.add_argument("--author")
    reply_trigger.add_argument("--json", action="store_true")

    attach = sub.add_parser("attach", help="Attach a file to the forum")
    attach.add_argument("path")
    attach.add_argument("--thread")
    attach.add_argument("--description", default="")
    attach.add_argument("--author")
    attach.add_argument("--json", action="store_true")

    retire = sub.add_parser(
        "retire", help="Permanently stop waking this peer",
        description="Leave the run when your contribution is finished. Post useful results or limits to the forum before retiring.",
    )
    retire.add_argument("--reason", default="")
    retire.add_argument("--agent-id")
    retire.add_argument("--json", action="store_true")

    artifacts = sub.add_parser(
        "artifacts", help="List patches from older isolated runs",
        description="Find versioned contributions with their base revision, changed files, and reported validation.",
        epilog="Read a contribution with idea forum artifact ARTIFACT_ID --json.",
    )
    artifacts.add_argument("--limit", type=int, default=30)
    artifacts.add_argument("--after", help="continue from the previous next_cursor")
    artifacts.add_argument("--json", action="store_true")
    artifact = sub.add_parser(
        "artifact", help="Inspect an older artifact or save its patch to a new file",
        description=(
            "Fetch a contribution's metadata and encoded patch. Inspect its base revision, "
            "changes, and reported validation before using it in your own working copy. "
            "Use --output to save the patch to a new local file."
        ),
        epilog="Example: idea forum artifact ARTIFACT_ID --output NEW_PATCH_FILE --json",
    )
    artifact.add_argument("artifact_id")
    artifact.add_argument("--output", help="save patch bytes to this new file; existing files are preserved")
    artifact.add_argument("--json", action="store_true")
    _add_approach_commands(sub)
    for command_parser in sub.choices.values():
        command_parser.add_argument("--request-id", help="retry the same mailbox request ID after an uncertain response")
    return parser


def handle_forum(argv: Sequence[str]) -> int:
    args = forum_parser().parse_args(argv)
    command = args.forum_command
    payload = {key: value for key, value in vars(args).items()
               if key not in {"forum_command", "state_dir", "run", "json", "agent_id", "author", "body_file", "output", "request_id"}}
    if command in {"post", "reply", "reply-trigger"}:
        payload["body"] = _body(args)
    if command == "reply-trigger" and args.event is None:
        if os.environ.get("IDEA_TRIGGER_EVENT_ID"):
            payload["event"] = int(os.environ["IDEA_TRIGGER_EVENT_ID"])
        if os.environ.get("IDEA_TRIGGER_THREAD_ID"):
            payload["trigger_thread_id"] = os.environ["IDEA_TRIGGER_THREAD_ID"]
    if command == "attach":
        source = Path(payload.pop("path")).expanduser()
        with source.open("rb") as handle:
            data = handle.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            raise ValueError("attachment exceeds 4 MiB")
        payload.update(filename=source.name, data_base64=base64.b64encode(data).decode("ascii"),
                       thread_id=payload.pop("thread", None))
    bridge_dir = os.environ.get("IDEA_BRIDGE_DIR")
    if bridge_dir:
        from .bridge import BridgeClient

        if args.state_dir or args.run or getattr(args, "agent_id", None) or getattr(args, "author", None):
            raise ValueError("isolated peers cannot override their mailbox identity or forum")
        value = BridgeClient(Path(bridge_dir)).call(command, payload, request_id=args.request_id)
    else:
        if args.request_id:
            raise ValueError("--request-id applies to the isolated peer mailbox")
        forum = Forum(_state_dir(args.state_dir))
        run_id = _run_id(forum, args.run)
        agent_id = getattr(args, "agent_id", None) or os.environ.get("IDEA_AGENT_ID")
        author = getattr(args, "author", None) or os.environ.get("IDEA_AGENT_NAME")
        value = dispatch_forum(forum, run_id, agent_id, command, payload, author=author)
    if command == "artifact" and args.output:
        target = Path(args.output).expanduser()
        # Never overwrite a peer's ongoing work while fetching another patch.
        with target.open("xb") as handle:
            handle.write(base64.b64decode(value.pop("patch_base64"), validate=True))
        value["saved_to"] = str(target)
    _emit(value, args.json)
    return 0


def run_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="idea",
        description="Start diverse autonomous peers with one shared objective and forum.",
        epilog=(
            "The first non-option words are the objective. Other commands: "
            "idea resume, idea forum, idea serve, idea status, idea profiles, idea doctor"
        ),
    )
    parser.add_argument("goal", nargs="+", help="the exact objective passed to every peer")
    parser.add_argument("--workspace", default=".", help="shared project directory (default: cwd)")
    parser.add_argument(
        "--state-dir",
        help=(
            "forum/log state directory; must be inside WORKSPACE "
            "(default: WORKSPACE/.idea-swarm)"
        ),
    )
    parser.add_argument("--profile", action="append", help="launch only this named profile; repeatable")
    profile_source = parser.add_mutually_exclusive_group()
    profile_source.add_argument(
        "--config",
        help="replace the default lineup with a TOML [[agents]] configuration "
        "(default: $IDEA_CONFIG)",
    )
    profile_source.add_argument(
        "--preset",
        choices=available_presets(),
        help="replace the default agents with a packaged model preset",
    )
    parser.add_argument("--dry-run", action="store_true", help="prepare the run without starting models")
    parser.add_argument("--no-web", action="store_true", help="do not serve the live forum while running")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7331)
    _add_communication_arguments(parser)
    return parser


def _add_communication_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--notification-debounce", type=float,
                        help="nonnegative quiet seconds before grouping subscription updates by thread (default: 1; persisted on resume)")
    parser.add_argument("--notification-max-wait", type=float,
                        help="maximum subscription grouping wait in seconds, at least debounce (default: 5); 0/0 disables waiting but keeps grouping; exact mentions and @all stay immediate")


def _communication_policy(args: argparse.Namespace, previous=None):
    from dataclasses import replace
    from .communication import CommunicationPolicy

    values = {field: getattr(args, option) for option, field in (
        ("notification_debounce", "debounce_seconds"),
        ("notification_max_wait", "max_wait_seconds"),
    ) if getattr(args, option, None) is not None}
    return replace(previous or CommunicationPolicy(), **values)


def _describe_prepared(prepared: PreparedRun, state_dir: Path) -> None:
    print(f'run: {prepared.run["id"]}')
    print(f"forum state: {state_dir}")
    print("peers:")
    for peer in prepared.peers:
        print(
            f"  - {peer.profile.name}: {peer.profile.model} / "
            f"effort={peer.profile.effort.value}"
        )


def _execute_prepared(
    *,
    forum: Forum,
    prepared: PreparedRun,
    state_dir: Path,
    no_web: bool,
    host: str,
    port: int,
    run_lock: RunLock | None = None,
) -> int:
    server = None
    thread = None
    if not no_web:
        try:
            server = make_server(forum, host, port)
        except OSError:
            server = make_server(forum, host, 0)
        actual_host, actual_port = server.server_address[:2]
        thread = threading.Thread(target=server.serve_forever, name="idea-forum", daemon=True)
        thread.start()
        print(f"live forum: http://{actual_host}:{actual_port}/?run={prepared.run['id']}")
    try:
        codes = asyncio.run(run_reactor(forum=forum, prepared=prepared, run_lock=run_lock))
    except KeyboardInterrupt:
        print("launcher interrupted; stopping peer processes", file=sys.stderr)
        return 130
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join(timeout=2)
    failed = [code for code in codes if code != 0]
    print("execution stopped; the original peers and their forum history remain recorded")
    print(f"forum remains at {state_dir}")
    print(f"reopen it with: idea serve --state-dir {state_dir}")
    return 1 if failed else 0


def handle_run(argv: Sequence[str]) -> int:
    args = run_parser().parse_args(argv)
    communication_policy = _communication_policy(args)
    goal = " ".join(args.goal).strip()
    workspace = Path(args.workspace).expanduser().resolve()
    state_dir = _state_dir(args.state_dir, workspace)
    forum = Forum(state_dir)
    profiles = resolve_profiles(
        names=args.profile,
        profiles_file=(
            args.config
            or (None if args.preset else os.environ.get("IDEA_CONFIG"))
        ),
        preset=args.preset,
    )
    prepared = prepare_run(
        forum=forum,
        goal=goal,
        workspace=workspace,
        profiles=profiles,
    )
    from .communication import CommunicationStore

    CommunicationStore(forum, str(prepared.run["id"])).configure(
        debounce_seconds=communication_policy.debounce_seconds,
        max_wait_seconds=communication_policy.max_wait_seconds,
    )
    _describe_prepared(prepared, state_dir)
    if args.dry_run:
        print("dry run: no model process was started")
        return 0
    return _execute_prepared(
        forum=forum,
        prepared=prepared,
        state_dir=state_dir,
        no_web=args.no_web,
        host=args.host,
        port=args.port,
    )


def handle_resume(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="idea resume",
        description="Resume stopped peers in the same run, sessions, workspace, and forum.",
    )
    parser.add_argument("run", nargs="?", help="run id (default: latest)")
    parser.add_argument(
        "--state-dir",
        help="forum/log state directory; must be inside the run workspace",
    )
    parser.add_argument("--profile", action="append", help="resume only this peer; repeatable")
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="start fresh provider sessions while retaining the existing forum",
    )
    parser.add_argument("--dry-run", action="store_true", help="preview without starting peers")
    parser.add_argument("--no-web", action="store_true")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7331)
    _add_communication_arguments(parser)
    args = parser.parse_args(argv)
    state_dir = _state_dir(args.state_dir)
    forum = Forum(state_dir)
    run_id = _run_id(forum, args.run)
    with RunLock(forum.state_dir, run_id) as run_lock:
        from .communication import CommunicationStore

        communication = CommunicationStore(forum, run_id)
        communication_policy = _communication_policy(args, communication.policy())
        prepared = prepare_resume(
            forum=forum,
            run_id=run_id,
            profile_names=args.profile,
            fresh_sessions=args.fresh,
            reset_processes=not args.dry_run,
            run_lock=run_lock,
        )
        communication.configure(
            debounce_seconds=communication_policy.debounce_seconds,
            max_wait_seconds=communication_policy.max_wait_seconds,
        )
        _describe_prepared(prepared, state_dir)
        print("resuming the persisted run; no new objective or forum was created")
        if args.dry_run:
            print("dry run: no model process was started")
            return 0
        return _execute_prepared(
            forum=forum,
            prepared=prepared,
            state_dir=state_dir,
            no_web=args.no_web,
            host=args.host,
            port=args.port,
            run_lock=run_lock,
        )


def handle_serve(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="idea serve", description="Serve a persisted IDEA forum")
    parser.add_argument("--state-dir")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7331)
    args = parser.parse_args(argv)
    serve(Forum(_state_dir(args.state_dir)), args.host, args.port)
    return 0


def _seed_demo(forum: Forum) -> tuple[str, str]:
    from .domain import AgentProfile, Effort, ProcessState, Provider

    run = forum.create_run(
        "데모 실행: 웹 UI 미리보기용 샘플 데이터",
        workspace=forum.state_dir,
    )
    run_id = str(run["id"])
    peers = [
        ("aria", "claude-fable-5", ProcessState.RUNNING),
        ("bolt", "claude-sonnet-5", ProcessState.RUNNING),
        ("nova", "claude-opus-5", ProcessState.DORMANT),
    ]
    for name, model, process_state in peers:
        agent = forum.register_agent(
            run_id,
            AgentProfile(
                name=name, provider=Provider.ANTHROPIC, model=model, effort=Effort.HIGH
            ),
        )
        forum.set_process_state(str(agent["id"]), process_state)

    guide = forum.create_thread(
        run_id,
        "aria",
        "데모 포럼 사용법",
        "이 실행은 UI 테스트용 샘플입니다.\n\n"
        "- 왼쪽 Peers에서 이름을 클릭하면 입력창에 @태그가 들어갑니다.\n"
        "- Peers 제목 옆 @all 버튼은 모든 peer를 태그합니다.\n"
        "- 아래 실시간 활동 스레드에 주기적으로 댓글이 달리며,\n"
        "  @human 멘션이 포함되면 오른쪽 위에 알림이 뜹니다.\n"
        "  알림은 읽지 않아도 10분 뒤 자동으로 사라집니다.",
    )
    forum.add_comment(str(guide["id"]), "bolt", "@aria 정리 감사합니다. 검색과 페이지네이션도 확인해보세요.")
    forum.add_comment(str(guide["id"]), "human", "확인했습니다.")
    live = forum.create_thread(
        run_id,
        "nova",
        "실시간 활동 로그 (데모)",
        "이 스레드에는 시뮬레이션 댓글이 주기적으로 추가됩니다. @all",
    )
    return run_id, str(live["id"])


def _demo_activity(forum: Forum, thread_id: str, interval: float) -> None:
    import itertools
    import time

    messages = itertools.cycle(
        [
            ("aria", "카드 레이아웃 초안을 갱신했습니다. 다음 반복에서 그리드 간격을 조정할게요."),
            ("bolt", "모바일 브레이크포인트 검토 완료. 2열 → 1열 전환은 720px가 적당합니다."),
            ("nova", "@human 확인 부탁드립니다 — 멘션 알림 테스트용 댓글입니다."),
            ("bolt", "색상 토큰 12종을 문서화했습니다. 대비비는 모두 4.5:1 이상입니다."),
            ("aria", "@nova 접근성 체크리스트 마지막 항목 검토 부탁해요."),
        ]
    )
    while True:
        time.sleep(interval)
        author, body = next(messages)
        try:
            forum.add_comment(thread_id, author, body)
        except Exception:  # noqa: BLE001 - demo feeder should die quietly
            return


def handle_demo(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="idea demo",
        description="Serve the web UI with disposable sample data for manual testing",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7331)
    parser.add_argument("--state-dir", help="reuse this state dir instead of a fresh temp dir")
    parser.add_argument(
        "--interval",
        type=float,
        default=25.0,
        help="seconds between simulated comments; 0 disables the live feed",
    )
    args = parser.parse_args(argv)
    state_dir = (
        Path(args.state_dir).expanduser().resolve()
        if args.state_dir
        else Path(tempfile.mkdtemp(prefix="idea-demo-"))
    )
    forum = Forum(state_dir)
    live_thread = _seed_demo(forum)[1]
    print(f"demo state: {state_dir}")
    print(f"password: {os.environ.get(WEB_PASSWORD_ENV) or DEFAULT_WEB_PASSWORD}")
    if args.interval > 0:
        threading.Thread(
            target=_demo_activity,
            args=(forum, live_thread, args.interval),
            daemon=True,
        ).start()
        print(f"live feed: {args.interval:g}초 간격으로 댓글 시뮬레이션 (@human 멘션 포함)")
    serve(forum, args.host, args.port)
    return 0


def handle_status(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="idea status")
    parser.add_argument("run", nargs="?")
    parser.add_argument("--state-dir")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    forum = Forum(_state_dir(args.state_dir))
    run_id = _run_id(forum, args.run)
    snapshot = forum.snapshot(run_id)
    if args.json:
        _emit(snapshot, True)
    else:
        print(f'goal: {snapshot["run"]["goal"]}')
        print(f'workspace: {snapshot["run"]["workspace"]}')
        print(f'threads: {len(snapshot["threads"])}')
        for agent in snapshot["agents"]:
            print(
                f'  {agent["name"]}: {agent["model"]} / {agent["effort"]} / '
                f'{agent["process_state"]}'
            )
    return 0


def handle_integrate(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="idea integrate", description="Apply a published patch to its unchanged original baseline")
    parser.add_argument("artifact_id")
    parser.add_argument("--state-dir")
    parser.add_argument("--run")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    from .workspaces import WorkspaceStore

    forum = Forum(_state_dir(args.state_dir))
    value = WorkspaceStore(forum, _run_id(forum, args.run)).integrate(args.artifact_id)
    _emit(value, args.json)
    return 0


def handle_profiles(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="idea profiles",
        description="Show the exact model and reasoning profiles used to start fixed peers.",
    )
    parser.add_argument("--json", action="store_true")
    profile_source = parser.add_mutually_exclusive_group()
    profile_source.add_argument("--config")
    profile_source.add_argument("--preset", choices=available_presets())
    args = parser.parse_args(argv)
    profiles = resolve_profiles(
        profiles_file=(
            args.config
            or (None if args.preset else os.environ.get("IDEA_CONFIG"))
        ),
        preset=args.preset,
    )
    values = [profile.as_dict() for profile in profiles]
    _emit(values, args.json)
    return 0


def handle_doctor(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="idea doctor")
    parser.parse_args(argv)
    codex = shutil.which("codex")
    claude = shutil.which("claude")
    git = shutil.which("git")
    import sqlite3
    print(f"codex: {codex or 'not found'}")
    print(f"claude: {claude or 'not found'}")
    print(f"git: {git or 'not found'}")
    print(f"python: {sys.executable}")
    print(f"sqlite: {sqlite3.sqlite_version}")
    return 0 if codex and claude and git else 1


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        if os.environ.get("IDEA_AGENT_ID") and (not args or args[0] not in {"forum", "profiles", "doctor"}):
            raise ValueError("peer sessions use the forum; launcher and integration commands belong to the user")
        if args and args[0] in COMMANDS:
            command = args.pop(0)
            if command == "run":
                return handle_run(args)
            if command == "resume":
                return handle_resume(args)
            if command == "forum":
                return handle_forum(args)
            if command == "serve":
                return handle_serve(args)
            if command == "demo":
                return handle_demo(args)
            if command == "status":
                return handle_status(args)
            if command == "profiles":
                return handle_profiles(args)
            if command == "doctor":
                return handle_doctor(args)
            if command == "integrate":
                return handle_integrate(args)
        return handle_run(args)
    except (KeyError, ValueError, FileNotFoundError, NotADirectoryError, RuntimeError) as error:
        print(f"idea: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
