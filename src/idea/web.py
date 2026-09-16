from __future__ import annotations

import hashlib
import hmac
import html
import json
import mimetypes
import os
import secrets
import shutil
import threading
import time
from http import HTTPStatus
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse

from .forum import Forum, resolve_run_id
from .web_assets import CSS, JAVASCRIPT


DEFAULT_WEB_PASSWORD = "wwwlkwwwlk"
WEB_PASSWORD_ENV = "IDEA_WEB_PASSWORD"
SECURE_COOKIE_ENV = "IDEA_WEB_SECURE_COOKIE"
SESSION_COOKIE_PREFIX = "idea_session"
SESSION_TTL_SECONDS = 12 * 60 * 60
MAX_FORM_BYTES = 64 * 1024
MAX_JSON_BYTES = 2 * 1024 * 1024


class PasswordSessions:
    """Small in-memory password session store for the browser-facing server."""

    def __init__(
        self,
        password: str,
        *,
        secure_cookie: bool = False,
        ttl_seconds: int = SESSION_TTL_SECONDS,
        cookie_name: str = SESSION_COOKIE_PREFIX,
    ) -> None:
        if not password:
            raise ValueError(f"{WEB_PASSWORD_ENV} must not be empty")
        self._password_digest = hashlib.sha256(password.encode("utf-8")).digest()
        self._secure_cookie = secure_cookie
        self._ttl_seconds = ttl_seconds
        self.cookie_name = cookie_name
        self._sessions: dict[str, float] = {}
        self._lock = threading.Lock()

    def verify_password(self, candidate: str) -> bool:
        candidate_digest = hashlib.sha256(candidate.encode("utf-8")).digest()
        return hmac.compare_digest(candidate_digest, self._password_digest)

    def issue(self) -> str:
        token = secrets.token_urlsafe(32)
        now = time.time()
        with self._lock:
            self._purge_expired(now)
            self._sessions[token] = now + self._ttl_seconds
        return token

    def valid(self, token: str | None) -> bool:
        if not token:
            return False
        now = time.time()
        with self._lock:
            expires_at = self._sessions.get(token)
            if expires_at is None:
                return False
            if expires_at <= now:
                self._sessions.pop(token, None)
                return False
            return True

    def revoke(self, token: str | None) -> None:
        if not token:
            return
        with self._lock:
            self._sessions.pop(token, None)

    def session_cookie(self, token: str) -> str:
        cookie = SimpleCookie()
        cookie[self.cookie_name] = token
        morsel = cookie[self.cookie_name]
        morsel["path"] = "/"
        morsel["httponly"] = True
        morsel["samesite"] = "Strict"
        morsel["max-age"] = str(self._ttl_seconds)
        if self._secure_cookie:
            morsel["secure"] = True
        return cookie.output(header="").strip()

    def clearing_cookie(self) -> str:
        cookie = SimpleCookie()
        cookie[self.cookie_name] = ""
        morsel = cookie[self.cookie_name]
        morsel["path"] = "/"
        morsel["httponly"] = True
        morsel["samesite"] = "Strict"
        morsel["max-age"] = "0"
        if self._secure_cookie:
            morsel["secure"] = True
        return cookie.output(header="").strip()

    def _purge_expired(self, now: float) -> None:
        expired = [
            token
            for token, expires_at in self._sessions.items()
            if expires_at <= now
        ]
        for token in expired:
            self._sessions.pop(token, None)


def _safe_next_path(value: str | None) -> str:
    if not value or not value.startswith("/") or value.startswith("//"):
        return "/"
    if "\\" in value or any(
        ord(character) < 32 or ord(character) == 127 for character in value
    ):
        return "/"
    parsed = urlparse(value)
    if parsed.scheme or parsed.netloc or parsed.path in {"/login", "/logout"}:
        return "/"
    return value


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _e(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _public_comment(comment: dict[str, Any]) -> dict[str, Any]:
    return {
        key: comment.get(key)
        for key in ("id", "thread_id", "author", "body", "created_at", "event_id")
    }


def _public_thread(thread: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in thread.items()
        if key not in {"comments", "attachments"}
    } | {
        "comments": [_public_comment(comment) for comment in thread["comments"]],
        "attachments": [
            {
                key: attachment.get(key)
                for key in ("id", "thread_id", "author", "original_name", "description", "size", "created_at")
            }
            for attachment in thread["attachments"]
        ],
    }


def render_login_page(next_path: str = "/", *, error: bool = False) -> str:
    message = (
        '<p class="error" role="alert">패스워드가 올바르지 않습니다.</p>'
        if error
        else '<p class="hint">포럼에 접근하려면 패스워드를 입력하세요.</p>'
    )
    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>IDEA Forum · 로그인</title>
<style>
:root {{ color-scheme: dark; }}
* {{ box-sizing: border-box; }}
body {{
  min-height: 100dvh; margin: 0; display: grid; place-items: center; padding: 24px;
  background:
    radial-gradient(900px 480px at 85% -10%, #14263f66, transparent 62%),
    radial-gradient(760px 420px at -10% 110%, #12302044, transparent 60%),
    #0a0f16;
  color: #e9eef5;
  font: 14px/1.55 Inter, ui-sans-serif, -apple-system, BlinkMacSystemFont,
    "Segoe UI", sans-serif;
  -webkit-font-smoothing: antialiased;
}}
.card {{
  width: min(100%, 390px); padding: 30px; border: 1px solid #1f2a3a;
  border-radius: 14px; background: #101724; box-shadow: 0 18px 50px #0006;
}}
.eyebrow {{
  display: flex; align-items: center; gap: 8px;
  color: #7ee787; font-size: 11px; font-weight: 800; letter-spacing: .12em;
}}
.eyebrow::before {{
  content: ""; width: 10px; height: 10px; border-radius: 3px;
  background: linear-gradient(135deg, #7ee787, #79b8ff);
  transform: rotate(45deg) scale(.92);
}}
h1 {{ margin: 10px 0 4px; font-size: 22px; font-weight: 800; letter-spacing: -.01em; }}
.hint, .error {{ margin: 0 0 20px; color: #94a3b8; }}
.error {{ color: #ff7b72; }}
label {{ display: block; margin-bottom: 7px; color: #94a3b8; font-size: 12px; }}
input {{
  width: 100%; padding: 11px 12px; border: 1px solid #35455c; border-radius: 8px;
  outline: none; background: #070b11; color: #e9eef5; font: inherit;
  transition: border-color .12s, box-shadow .12s;
}}
input:focus {{ border-color: #79b8ff; box-shadow: 0 0 0 3px #79b8ff22; }}
button {{
  width: 100%; margin-top: 14px; padding: 11px 14px; border: 1px solid #2f8144;
  border-radius: 8px; background: #16301f; color: #7ee787; cursor: pointer;
  font: inherit; font-weight: 700; transition: background .12s, border-color .12s;
}}
button:hover {{ border-color: #7ee787; background: #1c3d27; }}
</style></head><body>
<main class="card">
  <div class="eyebrow">IDEA / FORUM</div>
  <h1>로그인</h1>
  {message}
  <form method="post" action="/login">
    <input type="hidden" name="next" value="{_e(_safe_next_path(next_path))}">
    <label for="password">패스워드</label>
    <input id="password" name="password" type="password" required autofocus
      autocomplete="current-password">
    <button type="submit">포럼 열기</button>
  </form>
</main>
</body></html>"""


def render_page(forum: Forum, run_id: str) -> str:
    """Render a small reader and writer for the shared knowledge board."""

    run = forum.get_run(run_id)
    runs = forum.list_runs()
    run_links = "".join(
        f'<a class="run-link" href="/?run={quote(item["id"])}">{_e(item["id"])} · {_e(item["goal"][:55])}</a>'
        for item in runs[:30]
    )
    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>IDEA Board · {_e(run_id)}</title>
<style>{CSS}</style></head><body>
<header class="topbar">
  <div><h1 class="brand">IDEA / Knowledge Board</h1>
    <div class="goal" title="{_e(run["goal"])}">{_e(run["goal"])}</div></div>
  <form method="post" action="/logout"><button class="logout" type="submit">로그아웃</button></form>
</header>
<main class="board" data-board data-run-id="{_e(run_id)}">
  <aside class="sidebar">
    <h2>Workspace</h2><div class="workspace">{_e(run["workspace"])}</div>
    <details class="composer" open><summary>새 발견 게시</summary>
      <form id="new-thread-form">
        <input name="author" value="human" aria-label="작성자">
        <input name="title" placeholder="제목" aria-label="게시물 제목" required>
        <textarea name="body" placeholder="공유할 정보…" aria-label="게시물 내용" required></textarea>
        <button class="button" type="submit">게시</button><div class="status"></div>
      </form>
    </details>
    <nav class="run-list" aria-label="실행 목록"><h2>Runs</h2>{run_links}</nav>
  </aside>
  <section class="feed">
    <section class="posts" aria-label="게시물 목록">
      <div class="posts-header"><h2>게시물</h2>
        <form id="search-form" class="search"><input id="search" type="search" placeholder="게시물과 답글 검색"><button class="button" type="submit">검색</button></form>
      </div><div id="post-list" class="post-list"></div>
    </section>
    <section id="reader" class="reader" aria-label="게시물 내용"><div class="empty">왼쪽에서 게시물을 선택하세요.</div></section>
  </section>
</main>
<script>{JAVASCRIPT}</script>
</body></html>"""


class ForumHandler(BaseHTTPRequestHandler):
    forum: Forum
    auth: PasswordSessions
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: object) -> None:
        return

    def _security_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
            "connect-src 'self'; img-src 'self' data:; form-action 'self'; "
            "base-uri 'none'; frame-ancestors 'none'",
        )

    def _json(self, value: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self._security_headers()
        self.end_headers()
        self.wfile.write(data)

    def _html(self, value: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = value.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self._security_headers()
        self.end_headers()
        self.wfile.write(data)

    def _error(self, status: HTTPStatus, message: str) -> None:
        self._json({"error": message}, status)

    def _body_length(self, maximum: int) -> int:
        length = int(self.headers.get("Content-Length", "0"))
        if length < 0 or length > maximum:
            raise ValueError(f"request body must be at most {maximum} bytes")
        return length

    def _form(self) -> dict[str, str]:
        length = self._body_length(MAX_FORM_BYTES)
        values = parse_qs(self.rfile.read(length).decode("utf-8"), keep_blank_values=True)
        return {key: items[-1] for key, items in values.items()}

    def _json_body(self) -> dict[str, Any]:
        length = self._body_length(MAX_JSON_BYTES)
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

    def _redirect_location(self, location: str, *, cookie: str | None = None) -> None:
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", location)
        if cookie is not None:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Length", "0")
        self._security_headers()
        self.end_headers()

    def _redirect(self, run_id: str, thread_id: str | None = None) -> None:
        location = f"/?run={quote(run_id)}"
        if thread_id:
            location += f"&thread={quote(thread_id)}"
        self._redirect_location(location)

    def _session_token(self) -> str | None:
        header = self.headers.get("Cookie")
        if not header:
            return None
        cookie = SimpleCookie()
        try:
            cookie.load(header)
        except CookieError:
            return None
        morsel = cookie.get(self.auth.cookie_name)
        return morsel.value if morsel is not None else None

    def _authenticated(self) -> bool:
        return self.auth.valid(self._session_token())

    def _require_authentication(self, parsed_path: str) -> bool:
        if self._authenticated():
            return True
        if self.command != "GET":
            self.close_connection = True
        if parsed_path.startswith("/api/"):
            self._error(HTTPStatus.UNAUTHORIZED, "authentication required")
        else:
            target = _safe_next_path(self.path)
            self._redirect_location(f"/login?next={quote(target, safe='')}")
        return False

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
            if parsed.path == "/login":
                next_path = _safe_next_path(query.get("next", ["/"])[-1])
                if self._authenticated():
                    self._redirect_location(next_path)
                else:
                    self._html(render_login_page(next_path))
                return
            if not self._require_authentication(parsed.path):
                return
            if parsed.path == "/":
                run_id = resolve_run_id(self.forum, query.get("run", [None])[-1])
                self._html(render_page(self.forum, run_id))
                return
            if parts == ["api", "runs"]:
                self._json(self.forum.list_runs())
                return
            if len(parts) == 4 and parts[:2] == ["api", "runs"] and parts[3] == "threads":
                run_id = parts[2]
                limit = self._integer(query, "limit", 30, minimum=1, maximum=100)
                before = query.get("before", [None])[-1]
                search = query.get("q", [""])[-1]
                items, next_cursor = self.forum.list_thread_summaries(
                    run_id, limit=limit, before=before, query=search,
                )
                self._json({
                    "items": items,
                    "next_cursor": next_cursor,
                    "total_count": self.forum.count_threads(
                        run_id, search
                    ),
                })
                return
            if len(parts) == 3 and parts[:2] == ["api", "threads"]:
                if "comments_limit" in query:
                    limit = self._integer(
                        query, "comments_limit", 30, minimum=1, maximum=100
                    )
                    thread = self.forum.get_thread(
                        parts[2], include_comments=False
                    )
                    page = self.forum.comments_page(
                        parts[2], limit=limit
                    )
                    thread["comments"] = page["items"]
                    thread["comments_next_cursor"] = page["next_cursor"]
                    self._json(_public_thread(thread))
                else:
                    self._json(_public_thread(self.forum.get_thread(
                        parts[2]
                    )))
                return
            if len(parts) == 4 and parts[:2] == ["api", "threads"] and parts[3] == "comments":
                limit = self._integer(query, "limit", 30, minimum=1, maximum=100)
                page = self.forum.comments_page(
                    parts[2], limit=limit, after=query.get("after", [None])[-1],
                )
                self._json(page | {"items": [_public_comment(item) for item in page["items"]]})
                return
            if len(parts) == 5 and parts[:2] == ["api", "threads"] and parts[3] == "comments":
                self._json(_public_comment(self.forum.get_comment(
                    parts[2], parts[4]
                )))
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
            if parsed.path == "/login":
                form = self._form()
                next_path = _safe_next_path(form.get("next"))
                password = str(form.get("password", ""))
                if self.auth.verify_password(password):
                    token = self.auth.issue()
                    self._redirect_location(
                        next_path,
                        cookie=self.auth.session_cookie(token),
                    )
                else:
                    self._html(
                        render_login_page(next_path, error=True),
                        HTTPStatus.UNAUTHORIZED,
                    )
                return
            if parsed.path == "/logout":
                self.auth.revoke(self._session_token())
                self._redirect_location(
                    "/login",
                    cookie=self.auth.clearing_cookie(),
                )
                return
            if not self._require_authentication(parsed.path):
                return
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
                self._json(
                    _public_comment(comment) | {"activity_high_water": self.forum.activity_high_water(str(comment["run_id"]))},
                    HTTPStatus.CREATED,
                )
                return
            self._error(HTTPStatus.NOT_FOUND, "not found")
        except (KeyError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
            self._error(HTTPStatus.BAD_REQUEST, str(error))


class ForumHTTPServer(ThreadingHTTPServer):
    daemon_threads = True


def make_server(
    forum: Forum,
    host: str = "127.0.0.1",
    port: int = 7331,
    *,
    password: str | None = None,
    secure_cookie: bool | None = None,
) -> ForumHTTPServer:
    configured_password = (
        os.environ.get(WEB_PASSWORD_ENV, DEFAULT_WEB_PASSWORD)
        if password is None
        else password
    )
    auth = PasswordSessions(
        configured_password,
        secure_cookie=_env_flag(SECURE_COOKIE_ENV)
        if secure_cookie is None
        else secure_cookie,
        cookie_name=(
            f"{SESSION_COOKIE_PREFIX}_"
            f"{hashlib.sha256(str(forum.state_dir).encode()).hexdigest()[:12]}"
        ),
    )
    handler = type(
        "BoundForumHandler",
        (ForumHandler,),
        {"forum": forum, "auth": auth},
    )
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
