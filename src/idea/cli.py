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
from .profiles import resolve_profiles
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
    group.add_argument("--body", help="message text; quote it as one argument")
    group.add_argument("--body-file", help="read message text from a UTF-8 file")


def forum_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="idea forum",
        description="Read and publish findings on the shared IDEA knowledge board.",
        epilog="Run idea forum COMMAND --help for that command's usage.",
    )
    parser.add_argument("--state-dir")
    parser.add_argument("--run")
    sub = parser.add_subparsers(dest="forum_command", required=True, title="commands", metavar="COMMAND")

    recent = sub.add_parser("recent", help="List recent posts")
    recent.add_argument("--limit", type=int, default=30)
    recent.add_argument("--after", help="continue from the previous next_cursor")
    recent.add_argument("--json", action="store_true")

    read = sub.add_parser("read", help="Read one post and its replies")
    read.add_argument("thread_id")
    read.add_argument("--limit", type=int, default=30, help="replies per page")
    read.add_argument("--after", help="continue replies from next_cursor")
    read.add_argument("--json", action="store_true")

    search = sub.add_parser("search", help="Search post and reply text")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=30)
    search.add_argument("--after", help="continue from the previous next_cursor")
    search.add_argument("--json", action="store_true")

    post = sub.add_parser("post", help="Publish a finding")
    post.add_argument("--title", required=True)
    _add_body_arguments(post)
    post.add_argument("--author")
    post.add_argument("--json", action="store_true")

    reply = sub.add_parser("reply", help="Reply to a post")
    reply.add_argument("thread_id")
    _add_body_arguments(reply)
    reply.add_argument("--author")
    reply.add_argument("--json", action="store_true")

    attach = sub.add_parser("attach", help="Attach a file to the board")
    attach.add_argument("path")
    attach.add_argument("--thread")
    attach.add_argument("--description", default="")
    attach.add_argument("--author")
    attach.add_argument("--json", action="store_true")

    return parser

def handle_forum(argv: Sequence[str]) -> int:
    args = forum_parser().parse_args(argv)
    command = args.forum_command
    payload = {key: value for key, value in vars(args).items()
               if key not in {"forum_command", "state_dir", "run", "json", "agent_id", "author", "body_file", "output"}}
    if command in {"post", "reply"}:
        payload["body"] = _body(args)
    if command == "attach":
        source = Path(payload.pop("path")).expanduser()
        with source.open("rb") as handle:
            data = handle.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            raise ValueError("attachment exceeds 4 MiB")
        payload.update(filename=source.name, data_base64=base64.b64encode(data).decode("ascii"),
                       thread_id=payload.pop("thread", None))
    forum = Forum(_state_dir(args.state_dir))
    run_id = _run_id(forum, args.run)
    agent_id = getattr(args, "agent_id", None) or os.environ.get("IDEA_AGENT_ID")
    author = getattr(args, "author", None) or os.environ.get("IDEA_AGENT_NAME")
    value = dispatch_forum(forum, run_id, agent_id, command, payload, author=author)
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
    parser.add_argument(
        "--config",
        help="replace the default lineup with a TOML [[agents]] configuration "
        "(default: $IDEA_CONFIG)",
    )
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
    goal = " ".join(args.goal).strip()
    workspace = Path(args.workspace).expanduser().resolve()
    state_dir = _state_dir(args.state_dir, workspace)
    forum = Forum(state_dir)
    profiles = resolve_profiles(
        names=args.profile,
        profiles_file=(args.config or os.environ.get("IDEA_CONFIG")),
    )
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
    args = parser.parse_args(argv)
    state_dir = _state_dir(args.state_dir)
    forum = Forum(state_dir)
    run_id = _run_id(forum, args.run)
    with RunLock(forum.state_dir, run_id) as run_lock:
        prepared = prepare_resume(
            forum=forum,
            run_id=run_id,
            profile_names=args.profile,
            fresh_sessions=args.fresh,
            reset_processes=not args.dry_run,
            run_lock=run_lock,
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
        "- 게시물 목록에서 다른 에이전트의 결과를 읽습니다.\n"
        "- 찾은 내용은 새 게시물이나 답글로 간결하게 남깁니다.\n"
        "- 이 게시판은 알림이나 작업 배정을 하지 않습니다.",
    )
    forum.add_comment(str(guide["id"]), "bolt", "검색과 페이지네이션도 확인해보세요.")
    forum.add_comment(str(guide["id"]), "human", "확인했습니다.")
    live = forum.create_thread(
        run_id,
        "nova",
        "실시간 활동 로그 (데모)",
        "이 스레드에는 시뮬레이션 댓글이 주기적으로 추가됩니다.",
    )
    return run_id, str(live["id"])


def _demo_activity(forum: Forum, thread_id: str, interval: float) -> None:
    import itertools
    import time

    messages = itertools.cycle(
        [
            ("aria", "카드 레이아웃 초안을 갱신했습니다. 다음 반복에서 그리드 간격을 조정할게요."),
            ("bolt", "모바일 브레이크포인트 검토 완료. 2열 → 1열 전환은 720px가 적당합니다."),
            ("nova", "확인한 근거를 짧게 기록했습니다."),
            ("bolt", "색상 토큰 12종을 문서화했습니다. 대비비는 모두 4.5:1 이상입니다."),
            ("aria", "접근성 체크리스트 마지막 항목을 기록했습니다."),
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
        print(f"live feed: {args.interval:g}초 간격으로 댓글 시뮬레이션")
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
    parser = argparse.ArgumentParser(
        prog="idea profiles",
        description="Show the exact model and reasoning profiles used to start fixed peers.",
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--config")
    args = parser.parse_args(argv)
    profiles = resolve_profiles(
        profiles_file=(args.config or os.environ.get("IDEA_CONFIG")),
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
            raise ValueError("peer sessions use the knowledge board; launcher commands belong to the user")
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
