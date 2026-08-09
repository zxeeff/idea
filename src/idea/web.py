from __future__ import annotations

import html
import json
import mimetypes
import shutil
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse

from .forum import Forum, resolve_run_id
from .web_assets import CSS, JAVASCRIPT


def _e(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _public_agent(agent: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "id",
        "name",
        "provider",
        "model",
        "effort",
        "process_state",
        "created_at",
        "started_at",
        "exited_at",
        "retired_at",
        "retire_reason",
    )
    return {key: agent.get(key) for key in keys}


def _public_thread(thread: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in thread.items()
        if key not in {"comments", "attachments"}
    } | {
        "comments": [
            {
                key: comment.get(key)
                for key in ("id", "thread_id", "author", "body", "created_at")
            }
            for comment in thread["comments"]
        ],
        "attachments": [
            {
                key: item.get(key)
                for key in (
                    "id",
                    "run_id",
                    "thread_id",
                    "author",
                    "original_name",
                    "description",
                    "size",
                    "created_at",
                )
            }
            for item in thread["attachments"]
        ],
    }


def _peer_html(agent: dict[str, Any]) -> str:
    reason = (
        f'<div class="peer-reason">{_e(agent["retire_reason"])}</div>'
        if agent.get("retire_reason")
        else ""
    )
    return (
        '<div class="peer">'
        f'<span class="state-dot state-{_e(agent["process_state"])}"></span>'
        '<div>'
        f'<div class="peer-name">{_e(agent["name"])}</div>'
        f'<div class="peer-meta">{_e(agent["model"])} · {_e(agent["effort"])} · '
        f'{_e(agent["process_state"])}</div>{reason}</div></div>'
    )


def render_page(forum: Forum, run_id: str) -> str:
    """Render a lightweight application shell; thread data is fetched on demand."""

    run = forum.get_run(run_id)
    agents = forum.list_agents(run_id)
    statistics = forum.run_statistics(run_id)
    high_water = forum.activity_high_water(run_id)
    runs = forum.list_runs()

    peer_html = "".join(_peer_html(agent) for agent in agents)
    if not peer_html:
        peer_html = '<div class="empty">등록된 에이전트가 없습니다.</div>'
    run_links = "".join(
        (
            f'<a class="run-link{" current" if item["id"] == run_id else ""}" '
            f'href="/?run={quote(item["id"])}" title="{_e(item["goal"])}">'
            f'{_e(item["id"])} · {_e(item["goal"][:55])}</a>'
        )
        for item in runs[:30]
    )

    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>IDEA Forum · {_e(run_id)}</title>
<style>{CSS}</style></head><body>
<header class="topbar">
  <div>
    <div class="brand-row"><h1 class="brand">IDEA / Forum</h1>
      <span class="run-chip" title="{_e(run_id)}">{_e(run_id)}</span></div>
    <div class="goal" title="{_e(run["goal"])}">{_e(run["goal"])}</div>
  </div>
  <div id="connection" class="connection" aria-live="polite">실시간 확인 중</div>
</header>
<main class="workspace-grid" data-idea-app data-run-id="{_e(run_id)}"
  data-high-water="{high_water}">
  <aside class="sidebar" aria-label="실행 정보">
    <section class="side-section">
      <h2 class="section-title">Workspace</h2>
      <div class="workspace-path">{_e(run["workspace"])}</div>
    </section>
    <section class="side-section">
      <h2 class="section-title">Activity</h2>
      <div class="stats">
        <div class="stat"><strong id="stat-threads">{statistics["thread_count"]}</strong><span>글</span></div>
        <div class="stat"><strong id="stat-comments">{statistics["comment_count"]}</strong><span>댓글</span></div>
        <div class="stat"><strong id="stat-files">{statistics["attachment_count"]}</strong><span>파일</span></div>
      </div>
    </section>
    <section class="side-section">
      <h2 class="section-title">Peers <span>{len(agents)}</span></h2>
      <div id="peer-list">{peer_html}</div>
    </section>
    <section class="side-section">
      <h2 class="section-title">Runs</h2>
      <nav aria-label="실행 목록">{run_links}</nav>
    </section>
  </aside>

  <section class="thread-column" aria-label="게시물 목록">
    <div class="thread-toolbar">
      <div class="toolbar-row"><h2>게시물</h2>
        <span id="thread-total" class="thread-total">{statistics["thread_count"]}개</span></div>
      <form id="search-form" class="search-form" role="search">
        <input id="search-input" type="search" placeholder="제목·본문·댓글 검색"
          aria-label="포럼 검색">
        <button class="button" type="submit">검색</button>
      </form>
      <button id="search-clear" class="button quiet" type="button" hidden>검색 지우기</button>
      <button id="new-activity" class="button new-activity" type="button" hidden
        aria-live="polite"></button>
      <details class="composer">
        <summary>새 게시물 작성</summary>
        <form id="new-thread-form" class="composer-form">
          <div class="compact-row">
            <input name="author" value="human" aria-label="게시물 작성자">
            <input name="title" placeholder="제목" aria-label="게시물 제목" required>
          </div>
          <textarea name="body" placeholder="공유할 내용… 즉시 알림은 @정확한-이름 또는 @all"
            aria-label="게시물 내용" required></textarea>
          <button class="button primary" type="submit">게시</button>
        </form>
      </details>
    </div>
    <div id="thread-list" class="thread-list" role="list" aria-live="polite">
      <div class="loading">게시물 목록 불러오는 중…</div>
    </div>
    <div class="load-more-wrap">
      <button id="load-more" class="button quiet" type="button" hidden>이전 게시물 더 보기</button>
    </div>
  </section>

  <section id="reader" class="reader" aria-label="게시물 내용">
    <div class="reader-empty">왼쪽에서 게시물을 선택하세요.</div>
  </section>
</main>
<section id="human-mentions" class="human-mentions" aria-label="나를 멘션한 새 메시지"
  aria-live="polite" hidden></section>
<div id="toast" class="toast" role="status" hidden></div>
<script>{JAVASCRIPT}</script>
</body></html>"""


class ForumHandler(BaseHTTPRequestHandler):
    forum: Forum
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: object) -> None:
        return

    def _security_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
            "connect-src 'self'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'",
        )

    def _json(self, value: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self._security_headers()
        self.end_headers()
        self.wfile.write(data)

    def _html(self, value: str) -> None:
        data = value.encode()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self._security_headers()
        self.end_headers()
        self.wfile.write(data)

    def _error(self, status: HTTPStatus, message: str) -> None:
        self._json({"error": message}, status)

    def _form(self) -> dict[str, str]:
        length = int(self.headers.get("Content-Length", "0"))
        values = parse_qs(self.rfile.read(length).decode("utf-8"), keep_blank_values=True)
        return {key: items[-1] for key, items in values.items()}

    def _json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        value = json.loads(self.rfile.read(length))
        if not isinstance(value, dict):
            raise ValueError("JSON body must be an object")
        return value

    @staticmethod
    def _required_text(data: dict[str, Any], key: str) -> str:
        value = data.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{key} must be a non-empty string")
        return value

    @staticmethod
    def _integer(
        query: dict[str, list[str]], key: str, default: int, *, minimum: int, maximum: int
    ) -> int:
        value = int(query.get(key, [str(default)])[-1])
        if value < minimum or value > maximum:
            raise ValueError(f"{key} must be between {minimum} and {maximum}")
        return value

    def _redirect(self, run_id: str, thread_id: str | None = None) -> None:
        location = f"/?run={quote(run_id)}"
        if thread_id:
            location += f"&thread={quote(thread_id)}"
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self._security_headers()
        self.end_headers()

    def _overview(self, run_id: str) -> dict[str, Any]:
        return {
            "run": self.forum.get_run(run_id),
            "agents": [_public_agent(agent) for agent in self.forum.list_agents(run_id)],
            "statistics": self.forum.run_statistics(run_id),
            "high_water": self.forum.activity_high_water(run_id),
        }

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        parts = [unquote(part) for part in parsed.path.strip("/").split("/") if part]
        try:
            if parsed.path == "/favicon.ico":
                self.send_response(HTTPStatus.NO_CONTENT)
                self.send_header("Content-Length", "0")
                self._security_headers()
                self.end_headers()
                return
            if parsed.path == "/":
                run_id = resolve_run_id(self.forum, query.get("run", [None])[-1])
                self._html(render_page(self.forum, run_id))
                return
            if parts == ["api", "runs"]:
                self._json(self.forum.list_runs())
                return
            if len(parts) == 4 and parts[:2] == ["api", "runs"]:
                run_id, resource = parts[2], parts[3]
                if resource == "overview":
                    self._json(self._overview(run_id))
                    return
                if resource == "threads":
                    limit = self._integer(query, "limit", 30, minimum=1, maximum=100)
                    before = query.get("before", [None])[-1]
                    search = query.get("q", [""])[-1]
                    items, next_cursor = self.forum.list_thread_summaries(
                        run_id, limit=limit, before=before, query=search
                    )
                    self._json(
                        {
                            "items": items,
                            "next_cursor": next_cursor,
                            "total_count": self.forum.count_threads(run_id, search),
                        }
                    )
                    return
                if resource == "updates":
                    self.forum.get_run(run_id)
                    after = self._integer(
                        query, "after", 0, minimum=0, maximum=9_223_372_036_854_775_807
                    )
                    mentions_after = self._integer(
                        query,
                        "mentions_after",
                        after,
                        minimum=0,
                        maximum=9_223_372_036_854_775_807,
                    )
                    summary = self.forum.activity_summary(run_id, after)
                    payload: dict[str, Any] = summary | {
                        "agents": [
                            _public_agent(agent) for agent in self.forum.list_agents(run_id)
                        ],
                        "human_mentions": self.forum.human_mentions(
                            run_id, mentions_after
                        ),
                    }
                    if summary["new_count"]:
                        payload["statistics"] = self.forum.run_statistics(run_id)
                    self._json(
                        payload
                    )
                    return
            if len(parts) == 3 and parts[:2] == ["api", "runs"]:
                # Backwards-compatible full export. The browser UI intentionally
                # uses the paginated endpoints above instead.
                self._json(self.forum.snapshot(parts[2]))
                return
            if len(parts) == 3 and parts[:2] == ["api", "threads"]:
                self._json(_public_thread(self.forum.get_thread(parts[2])))
                return
            if parsed.path == "/attachment":
                attachment_id = query.get("id", [""])[-1]
                item = self.forum.get_attachment(attachment_id)
                path = Path(item["stored_path"])
                size = path.stat().st_size
                mime = mimetypes.guess_type(item["original_name"])[0] or "application/octet-stream"
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", mime)
                self.send_header(
                    "Content-Disposition",
                    f"attachment; filename*=UTF-8''{quote(item['original_name'])}",
                )
                self.send_header("Content-Length", str(size))
                self._security_headers()
                self.end_headers()
                with path.open("rb") as handle:
                    shutil.copyfileobj(handle, self.wfile, length=1024 * 1024)
                return
            self._error(HTTPStatus.NOT_FOUND, "not found")
        except (KeyError, RuntimeError, FileNotFoundError) as error:
            self._error(HTTPStatus.NOT_FOUND, str(error))
        except (ValueError, OverflowError) as error:
            self._error(HTTPStatus.BAD_REQUEST, str(error))

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        parts = [unquote(part) for part in parsed.path.strip("/").split("/") if part]
        try:
            if parsed.path == "/post":
                form = self._form()
                run_id = self._required_text(form, "run_id")
                item = self.forum.create_thread(
                    run_id,
                    str(form.get("author", "human")),
                    self._required_text(form, "title"),
                    self._required_text(form, "body"),
                )
                self._redirect(run_id, item["id"])
                return
            if parsed.path == "/reply":
                form = self._form()
                run_id = self._required_text(form, "run_id")
                thread_id = self._required_text(form, "thread_id")
                self.forum.add_comment(
                    thread_id,
                    str(form.get("author", "human")),
                    self._required_text(form, "body"),
                )
                self._redirect(run_id, thread_id)
                return
            if (
                len(parts) == 4
                and parts[:2] == ["api", "runs"]
                and parts[3] == "threads"
            ):
                data = self._json_body()
                item = self.forum.create_thread(
                    parts[2],
                    str(data.get("author", "anonymous")),
                    self._required_text(data, "title"),
                    self._required_text(data, "body"),
                )
                item["activity_high_water"] = self.forum.activity_high_water(parts[2])
                self._json(
                    item,
                    HTTPStatus.CREATED,
                )
                return
            if (
                len(parts) == 4
                and parts[:2] == ["api", "threads"]
                and parts[3] == "comments"
            ):
                data = self._json_body()
                comment = self.forum.add_comment(
                    parts[2],
                    str(data.get("author", "anonymous")),
                    self._required_text(data, "body"),
                )
                comment["activity_high_water"] = self.forum.activity_high_water(
                    str(comment.pop("run_id"))
                )
                self._json(
                    comment,
                    HTTPStatus.CREATED,
                )
                return
            self._error(HTTPStatus.NOT_FOUND, "not found")
        except (KeyError, ValueError, json.JSONDecodeError) as error:
            self._error(HTTPStatus.BAD_REQUEST, str(error))


class ForumHTTPServer(ThreadingHTTPServer):
    daemon_threads = True


def make_server(forum: Forum, host: str = "127.0.0.1", port: int = 7331) -> ThreadingHTTPServer:
    handler = type("BoundForumHandler", (ForumHandler,), {"forum": forum})
    return ForumHTTPServer((host, port), handler)


def serve(forum: Forum, host: str = "127.0.0.1", port: int = 7331) -> None:
    server = make_server(forum, host, port)
    actual_host, actual_port = server.server_address[:2]
    print(f"IDEA forum: http://{actual_host}:{actual_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
