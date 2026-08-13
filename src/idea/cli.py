from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any, Sequence

from .forum import Forum, resolve_run_id
from .launcher import PreparedRun, prepare_resume, prepare_run, run_reactor
from .profiles import default_profiles, select_profiles
from .web import DEFAULT_WEB_PASSWORD, WEB_PASSWORD_ENV, make_server, serve


COMMANDS = {"run", "resume", "forum", "serve", "demo", "status", "profiles", "doctor"}


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
    group.add_argument("--body")
    group.add_argument("--body-file")


def forum_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="idea forum", description="Use the shared free-form forum")
    parser.add_argument("--state-dir")
    parser.add_argument("--run")
    sub = parser.add_subparsers(dest="forum_command", required=True)

    recent = sub.add_parser("recent", help="List recent threads")
    recent.add_argument("--limit", type=int, default=100)
    recent.add_argument("--json", action="store_true")

    inbox = sub.add_parser("inbox", help="Read and acknowledge new activity for this peer")
    inbox.add_argument("--agent-id")
    inbox.add_argument("--json", action="store_true")

    read = sub.add_parser("read", help="Read one thread and its replies")
    read.add_argument("thread_id")
    read.add_argument("--json", action="store_true")

    search = sub.add_parser("search", help="Search thread and reply text")
    search.add_argument("query")
    search.add_argument("--json", action="store_true")

    post = sub.add_parser("post", help="Open a new thread")
    post.add_argument("--title", required=True)
    _add_body_arguments(post)
    post.add_argument("--author")
    post.add_argument("--json", action="store_true")

    reply = sub.add_parser("reply", help="Reply to a thread")
    reply.add_argument("thread_id")
    _add_body_arguments(reply)
    reply.add_argument("--author")
    reply.add_argument("--json", action="store_true")

    reply_trigger = sub.add_parser(
        "reply-trigger", help="Reply to the explicit mention that activated this peer"
    )
    reply_trigger.add_argument(
        "--event", type=int, help="reply to this triggering activity event"
    )
    reply_trigger.add_argument("--agent-id")
    _add_body_arguments(reply_trigger)
    reply_trigger.add_argument("--author")
    reply_trigger.add_argument("--json", action="store_true")

    attach = sub.add_parser("attach", help="Attach a file to the forum")
    attach.add_argument("path")
    attach.add_argument("--thread")
    attach.add_argument("--description", default="")
    attach.add_argument("--author")
    attach.add_argument("--json", action="store_true")

    retire = sub.add_parser("retire", help="Permanently stop waking this peer")
    retire.add_argument("--reason", default="")
    retire.add_argument("--agent-id")
    retire.add_argument("--json", action="store_true")
    return parser


def handle_forum(argv: Sequence[str]) -> int:
    args = forum_parser().parse_args(argv)
    forum = Forum(_state_dir(args.state_dir))
    run_id = _run_id(forum, args.run)
    command = args.forum_command
    if command == "recent":
        value = forum.list_threads(run_id, args.limit)
    elif command == "inbox":
        agent_id = _agent_id(args.agent_id)
        value, high_water = forum.unseen_activity(agent_id)
        forum.mark_activity_seen(agent_id, high_water)
    elif command == "read":
        value = forum.get_thread(args.thread_id)
    elif command == "search":
        value = forum.search(run_id, args.query)
    elif command == "post":
        value = forum.create_thread(run_id, _author(args.author), args.title, _body(args))
    elif command == "reply":
        value = forum.add_comment(args.thread_id, _author(args.author), _body(args))
    elif command == "reply-trigger":
        event_id = args.event
        using_environment_trigger = event_id is None
        if event_id is None and os.environ.get("IDEA_TRIGGER_EVENT_ID"):
            event_id = int(os.environ["IDEA_TRIGGER_EVENT_ID"])
        trigger = forum.resolve_reply_trigger(_agent_id(args.agent_id), event_id)
        hinted_thread = os.environ.get("IDEA_TRIGGER_THREAD_ID")
        if (
            using_environment_trigger
            and event_id is not None
            and hinted_thread
            and hinted_thread != trigger["thread_id"]
        ):
            raise RuntimeError("trigger routing hints disagree; refusing to misroute the reply")
        value = forum.add_comment(
            str(trigger["thread_id"]), _author(args.author), _body(args)
        )
        value["trigger_event_id"] = int(trigger["id"])
        value["trigger_thread_id"] = str(trigger["thread_id"])
    elif command == "attach":
        value = forum.add_attachment(
            run_id,
            _author(args.author),
            args.path,
            thread_id=args.thread,
            description=args.description,
        )
    elif command == "retire":
        value = forum.retire_agent(_agent_id(args.agent_id), args.reason)
    else:  # pragma: no cover
        raise AssertionError(command)
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
    parser.add_argument("--workspace", default=".", help="shared working directory (default: cwd)")
    parser.add_argument("--state-dir", help="forum/log state directory (default: WORKSPACE/.idea-swarm)")
    parser.add_argument("--profile", action="append", help="launch only this named profile; repeatable")
    parser.add_argument("--dry-run", action="store_true", help="prepare the run without starting models")
    parser.add_argument("--no-web", action="store_true", help="do not serve the live forum while running")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7331)
    return parser


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
        codes = asyncio.run(run_reactor(forum=forum, prepared=prepared))
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
    print(f"all peers retired; forum remains at {state_dir}")
    print(f"reopen it with: idea serve --state-dir {state_dir}")
    return 1 if failed else 0


def handle_run(argv: Sequence[str]) -> int:
    args = run_parser().parse_args(argv)
    goal = " ".join(args.goal).strip()
    workspace = Path(args.workspace).expanduser().resolve()
    state_dir = _state_dir(args.state_dir, workspace)
    forum = Forum(state_dir)
    profiles = select_profiles(args.profile)
    prepared = prepare_run(
        forum=forum,
        goal=goal,
        workspace=workspace,
        profiles=profiles,
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
    parser.add_argument("--state-dir")
    parser.add_argument("--profile", action="append", help="resume only this peer; repeatable")
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="start fresh provider sessions while retaining the existing forum",
    )
    parser.add_argument(
        "--expand-defaults",
        action="store_true",
        help="add any newly introduced default profiles to this existing run",
    )
    parser.add_argument("--dry-run", action="store_true", help="preview without starting peers")
    parser.add_argument("--no-web", action="store_true")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7331)
    args = parser.parse_args(argv)
    state_dir = _state_dir(args.state_dir)
    forum = Forum(state_dir)
    run_id = _run_id(forum, args.run)
    if args.dry_run and args.expand_defaults:
        raise ValueError("--expand-defaults cannot be combined with --dry-run")
    prepared = prepare_resume(
        forum=forum,
        run_id=run_id,
        profile_names=args.profile,
        fresh_sessions=args.fresh,
        reset_processes=not args.dry_run,
        additional_profiles=default_profiles() if args.expand_defaults else (),
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


def handle_profiles(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="idea profiles")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    values = [profile.as_dict() for profile in default_profiles()]
    _emit(values, args.json)
    return 0


def handle_doctor(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="idea doctor")
    parser.parse_args(argv)
    codex = shutil.which("codex")
    claude = shutil.which("claude")
    print(f"codex: {codex or 'not found'}")
    print(f"claude: {claude or 'not found'}")
    print(f"python: {sys.executable}")
    return 0 if codex and claude else 1


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    try:
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
        return handle_run(args)
    except (KeyError, ValueError, FileNotFoundError, NotADirectoryError, RuntimeError) as error:
        print(f"idea: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
