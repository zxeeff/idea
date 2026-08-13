from __future__ import annotations


CSS = r"""
:root {
  color-scheme: dark;
  --bg: #0a0f16;
  --bg-deep: #070b11;
  --panel: #101724;
  --panel-raised: #182130;
  --panel-soft: #0c1119;
  --line: #1f2a3a;
  --line-strong: #35455c;
  --text: #e9eef5;
  --muted: #94a3b8;
  --faint: #64748b;
  --accent: #7ee787;
  --accent-soft: #16301f;
  --blue: #79b8ff;
  --blue-soft: #10263f;
  --amber: #e3b341;
  --purple: #bd93f9;
  --red: #ff7b72;
  --shadow: 0 18px 50px #0006;
}

* { box-sizing: border-box; scrollbar-width: thin; scrollbar-color: #2c3a4e transparent; }
::-webkit-scrollbar { width: 10px; height: 10px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb {
  border: 3px solid transparent;
  border-radius: 8px;
  background: #2c3a4e;
  background-clip: content-box;
}
::-webkit-scrollbar-thumb:hover { background-color: #405270; }
[hidden] { display: none !important; }
html, body { height: 100%; }
body {
  margin: 0;
  overflow: hidden;
  background:
    radial-gradient(1100px 520px at 88% -12%, #14263f66, transparent 62%),
    radial-gradient(900px 460px at -8% 112%, #12302044, transparent 60%),
    var(--bg);
  color: var(--text);
  font: 14px/1.55 Inter, ui-sans-serif, -apple-system, BlinkMacSystemFont,
    "Segoe UI", sans-serif;
  -webkit-font-smoothing: antialiased;
}
button, input, textarea { font: inherit; }
button, a { -webkit-tap-highlight-color: transparent; }
a { color: var(--blue); text-decoration: none; }
a:hover { text-decoration: underline; }

.topbar {
  min-height: 76px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  padding: 12px 22px;
  border-bottom: 1px solid var(--line);
  background: linear-gradient(180deg, #0d1420f5, #0a0f16f2);
}
.brand-row { display: flex; align-items: baseline; gap: 11px; }
.brand { margin: 0; font-size: 18px; font-weight: 800; letter-spacing: .04em; }
.brand::before {
  content: "";
  display: inline-block;
  width: 11px;
  height: 11px;
  margin-right: 10px;
  border-radius: 3px;
  background: linear-gradient(135deg, var(--accent), var(--blue));
  transform: translateY(1px) rotate(45deg) scale(.92);
}
.run-chip {
  max-width: 230px;
  overflow: hidden;
  color: var(--faint);
  font: 11px/1.4 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.goal {
  max-width: min(950px, 74vw);
  margin-top: 3px;
  overflow: hidden;
  color: var(--muted);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.connection {
  flex: 0 0 auto;
  display: inline-flex;
  align-items: center;
  gap: 7px;
  color: var(--muted);
  font-size: 12px;
}
.connection::before {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--accent);
  box-shadow: 0 0 0 4px #7ee78718;
  content: "";
}
.connection.offline::before { background: var(--red); box-shadow: none; }
.topbar-actions { display: flex; align-items: center; gap: 12px; }
.topbar-actions form { margin: 0; }
.logout-button {
  padding: 5px 9px;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: transparent;
  color: var(--muted);
  cursor: pointer;
  font-size: 11px;
}
.logout-button:hover { border-color: var(--line-strong); color: var(--text); }

.workspace-grid {
  display: grid;
  grid-template-columns: 238px minmax(310px, 370px) minmax(0, 1fr);
  height: calc(100dvh - 76px);
  min-height: 0;
}
.sidebar, .thread-column, .reader {
  min-width: 0;
  min-height: 0;
  border-right: 1px solid var(--line);
}
.sidebar {
  overflow-y: auto;
  padding: 15px;
  background: var(--panel-soft);
  scrollbar-gutter: stable;
}
.side-section { margin-bottom: 22px; }
.section-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin: 0 0 9px;
  color: var(--faint);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: .09em;
  text-transform: uppercase;
}
.workspace-path {
  overflow-wrap: anywhere;
  color: var(--muted);
  font: 11px/1.55 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}
.stats { display: grid; grid-template-columns: repeat(3, 1fr); gap: 7px; }
.stat {
  padding: 10px 5px 8px;
  border: 1px solid var(--line);
  border-radius: 9px;
  background: linear-gradient(180deg, var(--panel-raised), var(--panel));
  text-align: center;
}
.stat strong { display: block; font-size: 17px; font-weight: 750; letter-spacing: .01em; }
.stat span { color: var(--faint); font-size: 10px; letter-spacing: .05em; }
#stat-threads { color: var(--accent); }
#stat-comments { color: var(--blue); }
#stat-files { color: var(--amber); }
.peer {
  display: grid;
  grid-template-columns: 9px minmax(0, 1fr);
  gap: 8px;
  margin: 0 -6px;
  padding: 8px 6px;
  border-bottom: 1px solid #28324188;
  border-radius: 7px;
  cursor: pointer;
  transition: background .12s;
}
.peer:last-child { border-bottom: 0; }
.peer:hover { background: var(--panel-raised); }
.peer:hover .peer-name::after {
  content: " @태그";
  color: var(--faint);
  font-size: 10px;
  font-weight: 500;
}
.state-dot { width: 7px; height: 7px; margin-top: 6px; border-radius: 50%; }
.state-running { background: var(--accent); box-shadow: 0 0 8px #7ee78766; }
.state-dormant { background: var(--amber); }
.state-blocked { background: #bd93f9; box-shadow: 0 0 8px #bd93f955; }
.state-retired, .state-exited { background: var(--faint); }
.state-failed { background: var(--red); }
.state-created { background: var(--blue); }
.peer-name { overflow: hidden; font-weight: 650; text-overflow: ellipsis; white-space: nowrap; }
.peer-meta { color: var(--faint); font-size: 11px; }
.peer-reason { margin-top: 3px; color: var(--muted); font-size: 11px; }
.run-link {
  display: block;
  margin: 2px -6px;
  padding: 6px;
  overflow: hidden;
  border-radius: 6px;
  color: var(--muted);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.run-link:hover, .run-link.current { background: var(--panel-raised); color: var(--text); text-decoration: none; }

.thread-column {
  display: flex;
  flex-direction: column;
  background: var(--panel);
}
.thread-toolbar {
  flex: 0 0 auto;
  padding: 14px;
  border-bottom: 1px solid var(--line);
  background: var(--panel);
}
.toolbar-row { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.toolbar-row h2 { margin: 0; font-size: 15px; }
.thread-total { color: var(--faint); font-size: 11px; }
.search-form { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 7px; margin-top: 10px; }
input, textarea {
  width: 100%;
  border: 1px solid var(--line);
  border-radius: 8px;
  outline: none;
  background: var(--bg-deep);
  color: var(--text);
  padding: 9px 11px;
  transition: border-color .12s, box-shadow .12s;
}
input::placeholder, textarea::placeholder { color: var(--faint); }
input:focus, textarea:focus { border-color: var(--blue); box-shadow: 0 0 0 3px #58a6ff1c; }
textarea { min-height: 92px; resize: vertical; }
.button {
  border: 1px solid var(--line-strong);
  border-radius: 8px;
  background: var(--panel-raised);
  color: var(--text);
  cursor: pointer;
  padding: 8px 12px;
  font-weight: 600;
  transition: border-color .12s, background .12s, color .12s;
}
.button:hover { border-color: var(--blue); }
.button:active { transform: translateY(1px); }
.button.primary { border-color: #2f8144; background: var(--accent-soft); color: var(--accent); }
.button.primary:hover { border-color: var(--accent); background: #1c3d27; }
.button.quiet { background: transparent; color: var(--muted); font-weight: 500; }
.new-activity {
  width: 100%;
  margin-top: 9px;
  border-color: #2b669f;
  background: var(--blue-soft);
  color: #a8d2ff;
}
.composer { margin-top: 10px; }
.composer summary { color: var(--blue); cursor: pointer; font-size: 12px; list-style-position: inside; }
.composer-form { display: grid; gap: 7px; margin-top: 9px; }
.mention-chip {
  padding: 2px 9px;
  border: 1px solid #946c1d88;
  border-radius: 999px;
  background: transparent;
  color: var(--amber);
  cursor: pointer;
  font: 11px/1.5 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-weight: 700;
  letter-spacing: 0;
  text-transform: none;
  transition: border-color .12s, background .12s;
}
.mention-chip:hover { border-color: var(--amber); background: #3b2c0c; }
.compact-row { display: grid; grid-template-columns: 100px minmax(0, 1fr); gap: 7px; }
.thread-list {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  padding: 8px;
  scrollbar-gutter: stable;
}
.thread-card {
  width: 100%;
  display: block;
  margin: 0 0 7px;
  padding: 12px 14px;
  overflow: hidden;
  border: 1px solid transparent;
  border-radius: 10px;
  background: transparent;
  color: var(--text);
  cursor: pointer;
  text-align: left;
  transition: background .12s, border-color .12s;
}
.thread-card:hover { border-color: var(--line); background: var(--panel-raised); }
.thread-card.selected {
  border-color: #2b669f;
  background: var(--blue-soft);
  box-shadow: inset 3px 0 0 0 var(--blue);
}
.thread-card-title { overflow-wrap: anywhere; font-weight: 700; line-height: 1.4; }
.thread-card-meta { margin-top: 5px; color: var(--faint); font-size: 11px; }
.card-author { font-weight: 700; }
.thread-preview {
  display: -webkit-box;
  margin-top: 6px;
  overflow: hidden;
  color: var(--muted);
  font-size: 12px;
  line-height: 1.5;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 3;
}
.load-more-wrap { flex: 0 0 auto; padding: 9px 12px 13px; border-top: 1px solid var(--line); }
.load-more-wrap .button { width: 100%; }
.empty, .loading { padding: 30px 16px; color: var(--faint); text-align: center; }

.reader {
  overflow-y: auto;
  background: var(--bg);
  scrollbar-gutter: stable;
  scroll-behavior: auto;
}
.reader-inner { width: min(900px, 100%); margin: 0 auto; padding: 30px clamp(20px, 4vw, 54px) 80px; }
.reader-empty { display: grid; min-height: 70vh; place-items: center; color: var(--faint); text-align: center; }
.thread-heading {
  margin: 0;
  overflow-wrap: anywhere;
  font-size: clamp(22px, 3vw, 32px);
  font-weight: 800;
  letter-spacing: -.015em;
  line-height: 1.24;
}
.post-meta { margin-top: 10px; color: var(--faint); font-size: 12px; }
.post-meta .card-author { font-size: 12.5px; }
.post-body, .comment-body {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font: 14px/1.72 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}
.mention {
  padding: .08em .36em;
  border: 1px solid #3178b8;
  border-radius: .42em;
  background: #163653;
  color: #a8d2ff;
  font-weight: 750;
  box-decoration-break: clone;
  -webkit-box-decoration-break: clone;
}
.mention-all {
  border-color: #946c1d;
  background: #3b2c0c;
  color: #ffd479;
  box-shadow: 0 0 0 2px #e3b34116;
}
.mention-human {
  border-color: #9a63d5;
  background: #34204d;
  color: #dec1ff;
  box-shadow: 0 0 0 2px #bd93f918;
}
.mention-unknown {
  border-color: #596779;
  border-style: dashed;
  background: #1a2029;
  color: #8795a8;
}
.post-body { margin-top: 26px; padding-bottom: 28px; border-bottom: 1px solid var(--line); }
.attachment-block { margin-top: 22px; padding: 14px; border: 1px solid var(--line); border-radius: 10px; background: var(--panel); }
.attachment { padding: 5px 0; }
.attachment-meta { display: block; color: var(--faint); font-size: 11px; }
.comments-heading { margin: 32px 0 14px; font-size: 15px; }
.comment {
  display: grid;
  grid-template-columns: 32px minmax(0, 1fr);
  column-gap: 12px;
  margin: 0 0 12px;
  padding: 14px 16px;
  border: 1px solid var(--line);
  border-radius: 12px;
  background: var(--panel);
}
.avatar {
  width: 30px;
  height: 30px;
  display: grid;
  place-items: center;
  border: 1px solid hsl(var(--avatar-hue, 210) 45% 34% / .8);
  border-radius: 9px;
  background: hsl(var(--avatar-hue, 210) 45% 19%);
  color: hsl(var(--avatar-hue, 210) 85% 78%);
  font-size: 13px;
  font-weight: 800;
  text-transform: uppercase;
  user-select: none;
}
.comment > .avatar { grid-row: 1; margin-top: 1px; }
.comment-heading { align-self: center; min-width: 0; }
.comment-author { font-weight: 700; color: hsl(var(--avatar-hue, 210) 75% 76%); }
.tag-author {
  padding: 0;
  border: 0;
  background: none;
  font: inherit;
  font-weight: 700;
  cursor: pointer;
}
.tag-author:hover { text-decoration: underline; }
.comment-time { margin-left: 7px; color: var(--faint); font-size: 11px; }
.comment-body { grid-column: 2; margin-top: 8px; }
.mention-focus {
  outline: 2px solid #bd93f9;
  outline-offset: 4px;
  animation: mention-focus-pulse 1.5s ease-out;
}
@keyframes mention-focus-pulse {
  0% { outline-color: #fff; box-shadow: 0 0 0 8px #bd93f944; }
  100% { outline-color: #bd93f9; box-shadow: 0 0 0 0 #bd93f900; }
}
.reply-form { display: grid; gap: 8px; margin-top: 22px; padding-top: 20px; border-top: 1px solid var(--line); }
.reply-actions { display: flex; justify-content: flex-end; }
.human-mentions {
  position: fixed;
  top: 88px;
  right: 20px;
  z-index: 30;
  width: min(410px, calc(100vw - 40px));
  max-height: calc(100dvh - 112px);
  display: grid;
  gap: 9px;
  overflow-y: auto;
  padding: 2px;
  scrollbar-gutter: stable;
}
.human-mention-alert {
  position: relative;
  overflow: hidden;
  border: 1px solid #7650a3;
  border-radius: 10px;
  background: #181221f2;
  box-shadow: 0 16px 45px #0009, 0 0 0 1px #bd93f91c inset;
}
.human-mention-open {
  width: 100%;
  display: block;
  padding: 13px 42px 13px 14px;
  border: 0;
  background: transparent;
  color: var(--text);
  cursor: pointer;
  text-align: left;
}
.human-mention-open:hover { background: #bd93f90d; }
.human-mention-kicker {
  color: #cfadf7;
  font-size: 11px;
  font-weight: 800;
  letter-spacing: .06em;
  text-transform: uppercase;
}
.human-mention-title {
  margin-top: 4px;
  overflow: hidden;
  font-weight: 750;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.human-mention-preview {
  display: -webkit-box;
  margin-top: 5px;
  overflow: hidden;
  color: var(--muted);
  font: 12px/1.45 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}
.human-mention-hint { margin-top: 7px; color: #a884d4; font-size: 10px; }
.human-mention-close {
  position: absolute;
  top: 8px;
  right: 8px;
  width: 28px;
  height: 28px;
  padding: 0;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--muted);
  cursor: pointer;
  font-size: 18px;
}
.human-mention-close:hover { background: #ffffff10; color: var(--text); }
.toast {
  position: fixed;
  right: 20px;
  bottom: 20px;
  z-index: 20;
  max-width: min(420px, calc(100vw - 40px));
  padding: 10px 14px;
  border: 1px solid var(--line-strong);
  border-radius: 8px;
  background: var(--panel-raised);
  box-shadow: var(--shadow);
  color: var(--text);
}

@media (max-width: 1120px) {
  .workspace-grid { grid-template-columns: 205px 320px minmax(0, 1fr); }
}
@media (max-width: 850px) {
  body { height: auto; overflow: auto; }
  .topbar { position: sticky; top: 0; z-index: 5; min-height: 66px; }
  .goal { max-width: 70vw; }
  .workspace-grid { display: block; height: auto; }
  .sidebar, .thread-column, .reader { border-right: 0; border-bottom: 1px solid var(--line); }
  .sidebar { max-height: 340px; }
  .thread-column { height: min(620px, 72vh); }
  .reader { min-height: 70vh; overflow: visible; }
  .reader-inner { padding-top: 24px; }
  .human-mentions { top: 76px; right: 10px; width: calc(100vw - 20px); }
}
"""


JAVASCRIPT = r"""
(() => {
  "use strict";

  const app = document.querySelector("[data-idea-app]");
  if (!app) return;

  const runId = app.dataset.runId;
  const encodedRun = encodeURIComponent(runId);
  const threadList = document.getElementById("thread-list");
  const reader = document.getElementById("reader");
  const loadMore = document.getElementById("load-more");
  const newActivity = document.getElementById("new-activity");
  const connection = document.getElementById("connection");
  const searchInput = document.getElementById("search-input");
  const threadTotal = document.getElementById("thread-total");
  const humanMentions = document.getElementById("human-mentions");
  const toast = document.getElementById("toast");

  const urlState = new URL(window.location.href);
  const baseTitle = document.title;
  const initialHighWater = Number(app.dataset.highWater || 0);
  const mentionStorageKey = `idea:${runId}:human-mention-cursor`;
  const readMentionCursor = () => {
    try {
      const value = window.localStorage.getItem(mentionStorageKey);
      if (value === null) return null;
      const parsed = Number(value);
      return Number.isSafeInteger(parsed) && parsed >= 0 ? parsed : null;
    } catch (_error) {
      return null;
    }
  };
  const writeMentionCursor = (value) => {
    try {
      window.localStorage.setItem(mentionStorageKey, String(value));
    } catch (_error) {
      // The in-memory notification queue still works when storage is disabled.
    }
  };
  const storedMentionCursor = readMentionCursor();
  const initialMentionCursor = storedMentionCursor === null
    ? initialHighWater
    : Math.min(storedMentionCursor, initialHighWater);
  if (storedMentionCursor === null) writeMentionCursor(initialMentionCursor);
  const state = {
    cursor: null,
    query: "",
    selected: urlState.searchParams.get("thread"),
    highWater: initialHighWater,
    pending: 0,
    loadingList: false,
    listRequest: 0,
    threadRequest: 0,
    peerFingerprint: "",
    peerNames: new Set(
      [...document.querySelectorAll(".peer-name")]
        .map((node) => node.textContent.trim().toLocaleLowerCase("en-US"))
    ),
    mentionCursor: initialMentionCursor,
    mentionSeenCursor: initialMentionCursor,
    mentionItems: [],
    mentionIds: new Set(),
    polling: false,
    toastTimer: null,
    activeComposer: null,
  };

  const make = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };

  // Keep the visual grammar aligned with the forum's exact mention syntax.
  // Text nodes are appended directly so forum content never becomes HTML.
  const mentionPattern = /(^|[^\p{L}\p{N}_-])@([A-Za-z0-9][A-Za-z0-9_-]*)(?![\p{L}\p{N}_-])/gu;

  const appendMentionText = (node, rawText) => {
    const value = String(rawText || "");
    let cursor = 0;
    for (const match of value.matchAll(mentionPattern)) {
      const start = match.index + match[1].length;
      if (start > cursor) node.append(document.createTextNode(value.slice(cursor, start)));
      const token = `@${match[2]}`;
      const name = match[2].toLocaleLowerCase("en-US");
      let kind = "unknown";
      let title = "등록된 peer와 일치하지 않는 멘션";
      if (name === "all") {
        kind = "all";
        title = "모든 비활성 peer를 깨우는 @all 멘션";
      } else if (name === "human" || name === "user") {
        kind = "human";
        title = "웹 포럼 사용자를 향한 멘션";
      } else if (state.peerNames.has(name)) {
        kind = "peer";
        title = `${token} peer를 깨우는 정확한 멘션`;
      }
      const badge = make(
        "span",
        `mention mention-${kind}`,
        token
      );
      badge.dataset.mention = match[2];
      badge.dataset.mentionKind = kind;
      badge.title = title;
      node.append(badge);
      cursor = start + token.length;
    }
    if (cursor < value.length) node.append(document.createTextNode(value.slice(cursor)));
    return node;
  };

  const richText = (tag, className, text) =>
    appendMentionText(make(tag, className), text);

  // Deterministic per-author hue so each agent keeps one color everywhere.
  const authorHue = (author) => {
    const name = String(author || "").trim().toLocaleLowerCase("en-US");
    if (name === "human" || name === "user") return 270;
    let hash = 0;
    for (let index = 0; index < name.length; index += 1) {
      hash = (hash * 31 + name.charCodeAt(index)) >>> 0;
    }
    return hash % 360;
  };

  const avatarNode = (author) => {
    const initial = (String(author || "?").trim()[0] || "?");
    const badge = make("span", "avatar", initial);
    badge.setAttribute("aria-hidden", "true");
    return badge;
  };

  const authorChip = (author) => {
    const chip = make("span", "card-author", author);
    chip.style.color = `hsl(${authorHue(author)} 65% 72%)`;
    return chip;
  };

  // Tagging happens from the sidebar: clicking a peer (or @all) drops the
  // mention into whichever composer the user touched last.
  const insertMention = (textarea, token) => {
    const start = textarea.selectionStart ?? textarea.value.length;
    const end = textarea.selectionEnd ?? start;
    const before = textarea.value.slice(0, start);
    const insertion = `${before && !/\s$/.test(before) ? " " : ""}${token} `;
    textarea.value = before + insertion + textarea.value.slice(end);
    const caret = before.length + insertion.length;
    textarea.focus();
    textarea.setSelectionRange(caret, caret);
  };

  const composerTarget = () => {
    if (state.activeComposer && state.activeComposer.isConnected) {
      return state.activeComposer;
    }
    const replyBody = document.querySelector("#reply-form textarea");
    if (replyBody) return replyBody;
    const form = document.getElementById("new-thread-form");
    form.closest("details").open = true;
    return form.elements.body;
  };

  const tagIntoComposer = (token) => {
    insertMention(composerTarget(), token);
    showToast(`${token} 태그를 입력창에 추가했습니다.`);
  };

  const authorTagButton = (author, className) => {
    const chip = make("button", `${className} tag-author`, author);
    chip.type = "button";
    chip.style.color = `hsl(${authorHue(author)} 65% 72%)`;
    chip.title = `클릭하면 @${author} 태그`;
    chip.addEventListener("click", () => tagIntoComposer(`@${author}`));
    return chip;
  };

  const timeText = (value) => {
    if (!value) return "";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    return new Intl.DateTimeFormat("ko-KR", {
      month: "short", day: "numeric", hour: "2-digit", minute: "2-digit"
    }).format(date);
  };

  const request = async (path, options = {}) => {
    const response = await fetch(path, {
      cache: "no-store",
      credentials: "same-origin",
      ...options,
      headers: { "Accept": "application/json", ...(options.headers || {}) },
    });
    if (response.status === 401) {
      const next = `${window.location.pathname}${window.location.search}${window.location.hash}`;
      window.location.assign(`/login?next=${encodeURIComponent(next)}`);
      throw new Error("로그인 세션이 만료되었습니다.");
    }
    const payload = await response.json().catch(() => ({ error: `HTTP ${response.status}` }));
    if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
    return payload;
  };

  const showToast = (message) => {
    toast.textContent = message;
    toast.hidden = false;
    window.clearTimeout(state.toastTimer);
    state.toastTimer = window.setTimeout(() => { toast.hidden = true; }, 3200);
  };

  const renderHumanMentions = () => {
    humanMentions.replaceChildren();
    humanMentions.hidden = state.mentionItems.length === 0;
    document.title = state.mentionItems.length
      ? `(${state.mentionItems.length}) ${baseTitle}`
      : baseTitle;
    const kindText = { thread: "새 글", comment: "댓글", attachment: "첨부파일" };
    for (const item of state.mentionItems) {
      const alert = make("article", "human-mention-alert");
      alert.dataset.testid = "human-mention-alert";
      alert.dataset.eventId = String(item.id);
      const open = make("button", "human-mention-open");
      open.type = "button";
      open.dataset.testid = "human-mention-open";
      open.setAttribute(
        "aria-label",
        `${item.author}의 멘션: ${item.thread_title || "제목 없는 글"} 열기`
      );
      open.append(make(
        "div",
        "human-mention-kicker",
        `${item.mention} · ${item.author} · ${kindText[item.kind] || item.kind}`
      ));
      open.append(make(
        "div", "human-mention-title", item.thread_title || "제목 없는 글"
      ));
      open.append(make("div", "human-mention-preview", item.preview));
      open.append(make("div", "human-mention-hint", "클릭해서 멘션 위치로 이동"));
      open.addEventListener("click", async () => {
        const opened = await selectThread(item.thread_id, {
          updateHistory: true,
          focusSubjectId: item.subject_id,
        });
        if (opened) markHumanMentionRead(item.id);
      });
      const close = make("button", "human-mention-close", "×");
      close.type = "button";
      close.setAttribute("aria-label", "이 멘션 알림 읽음 처리");
      close.addEventListener("click", () => markHumanMentionRead(item.id));
      alert.append(open, close);
      humanMentions.append(alert);
    }
  };

  const markHumanMentionRead = (eventId) => {
    state.mentionItems = state.mentionItems.filter((item) => item.id > eventId);
    state.mentionIds = new Set(state.mentionItems.map((item) => item.id));
    state.mentionSeenCursor = state.mentionItems.length
      ? Math.max(state.mentionSeenCursor, eventId)
      : Math.max(state.mentionCursor, eventId);
    writeMentionCursor(state.mentionSeenCursor);
    renderHumanMentions();
  };

  // Alerts left unread for too long dismiss themselves.
  const MENTION_ALERT_TTL_MS = 10 * 60 * 1000;

  const enqueueHumanMentions = (items) => {
    for (const item of items || []) {
      const eventId = Number(item.id);
      if (
        !Number.isSafeInteger(eventId)
        || eventId <= state.mentionSeenCursor
        || state.mentionIds.has(eventId)
      ) continue;
      state.mentionIds.add(eventId);
      state.mentionItems.push({
        ...item,
        id: eventId,
        expiresAt: Date.now() + MENTION_ALERT_TTL_MS,
      });
    }
    state.mentionItems.sort((left, right) => left.id - right.id);
    renderHumanMentions();
  };

  const expireHumanMentions = () => {
    const now = Date.now();
    const expired = state.mentionItems.filter((item) => item.expiresAt <= now);
    if (!expired.length) return;
    // Items expire in id order (same TTL, enqueued in order), so marking the
    // newest expired id read clears exactly the expired prefix.
    markHumanMentionRead(Math.max(...expired.map((item) => item.id)));
  };

  const advanceMentionCursor = (cursor) => {
    const value = Number(cursor);
    if (Number.isSafeInteger(value)) state.mentionCursor = Math.max(state.mentionCursor, value);
    if (!state.mentionItems.length) {
      state.mentionSeenCursor = Math.max(state.mentionSeenCursor, state.mentionCursor);
      writeMentionCursor(state.mentionSeenCursor);
    }
  };

  const setConnection = (online) => {
    connection.classList.toggle("offline", !online);
    connection.textContent = online ? "실시간 확인 중" : "연결 재시도 중";
  };

  const markSelected = () => {
    for (const card of threadList.querySelectorAll("[data-thread-id]")) {
      card.classList.toggle("selected", card.dataset.threadId === state.selected);
    }
  };

  const renderThreadCards = (items, append) => {
    if (!append) threadList.replaceChildren();
    if (!append && items.length === 0) {
      threadList.append(make("div", "empty", state.query ? "검색 결과가 없습니다." : "아직 게시물이 없습니다."));
      return;
    }
    for (const item of items) {
      const card = make("button", "thread-card");
      card.type = "button";
      card.dataset.threadId = item.id;
      card.setAttribute("aria-label", `${item.title} 열기`);
      card.append(richText("div", "thread-card-title", item.title));
      const counts = `${item.comment_count} 댓글 · ${item.attachment_count} 파일`;
      const meta = make("div", "thread-card-meta");
      meta.append(authorChip(item.author));
      meta.append(document.createTextNode(` · ${timeText(item.updated_at)} · ${counts}`));
      card.append(meta);
      const preview = String(item.preview || "").replace(/\s+/g, " ").trim();
      if (preview) {
        card.append(richText("div", "thread-preview", preview + (item.body_length > 240 ? "…" : "")));
      }
      card.addEventListener("click", () => selectThread(item.id, { updateHistory: true }));
      threadList.append(card);
    }
    markSelected();
  };

  const loadThreads = async ({ reset = false } = {}) => {
    if (state.loadingList && !reset) return;
    const requestNumber = ++state.listRequest;
    state.loadingList = true;
    if (reset) {
      state.cursor = null;
      threadList.replaceChildren(make("div", "loading", "게시물 목록 불러오는 중…"));
    }
    loadMore.disabled = true;
    const params = new URLSearchParams({ limit: "30" });
    if (state.cursor && !reset) params.set("before", state.cursor);
    if (state.query) params.set("q", state.query);
    try {
      const data = await request(`/api/runs/${encodedRun}/threads?${params}`);
      if (requestNumber !== state.listRequest) return;
      renderThreadCards(data.items, !reset);
      state.cursor = data.next_cursor;
      loadMore.hidden = !data.next_cursor;
      threadTotal.textContent = state.query ? `“${state.query}” 검색` : `${data.total_count}개`;
      document.getElementById("search-clear").hidden = !state.query;
      if (!state.selected && data.items.length) {
        await selectThread(data.items[0].id, { updateHistory: true });
      } else if (state.selected) {
        markSelected();
      }
      setConnection(true);
    } catch (error) {
      if (requestNumber === state.listRequest) {
        if (reset) threadList.replaceChildren(make("div", "empty", `목록 오류: ${error.message}`));
        showToast(`게시물 목록을 가져오지 못했습니다: ${error.message}`);
      }
      setConnection(false);
    } finally {
      if (requestNumber === state.listRequest) {
        state.loadingList = false;
        loadMore.disabled = false;
      }
    }
  };

  const attachmentNode = (item) => {
    const row = make("div", "attachment");
    row.dataset.subjectId = item.id;
    const link = richText("a", "", `📎 ${item.original_name}`);
    link.href = `/attachment?id=${encodeURIComponent(item.id)}`;
    row.append(link);
    const size = Number(item.size || 0).toLocaleString("ko-KR");
    const metadata = make("span", "attachment-meta", `${item.author} · ${size} bytes`);
    if (item.description) {
      metadata.append(document.createTextNode(" · "));
      appendMentionText(metadata, item.description);
    }
    row.append(metadata);
    return row;
  };

  const renderThread = (thread, { preserveScroll = false, scrollToBottom = false } = {}) => {
    const previousScroll = reader.scrollTop;
    const inner = make("article", "reader-inner");
    inner.dataset.testid = "thread-reader";
    inner.append(richText("h1", "thread-heading", thread.title));
    const postMeta = make("div", "post-meta");
    postMeta.append(authorTagButton(thread.author, "card-author"));
    postMeta.append(document.createTextNode(` · ${timeText(thread.created_at)} · ${thread.id}`));
    inner.append(postMeta);
    const postBody = richText("div", "post-body", thread.body);
    postBody.dataset.subjectId = thread.id;
    inner.append(postBody);

    if (thread.attachments.length) {
      const block = make("section", "attachment-block");
      block.append(make("div", "section-title", `첨부파일 ${thread.attachments.length}`));
      for (const item of thread.attachments) block.append(attachmentNode(item));
      inner.append(block);
    }

    inner.append(make("h2", "comments-heading", `댓글 ${thread.comments.length}`));
    if (!thread.comments.length) inner.append(make("div", "empty", "아직 댓글이 없습니다."));
    for (const comment of thread.comments) {
      const node = make("article", "comment");
      node.dataset.subjectId = comment.id;
      node.style.setProperty("--avatar-hue", String(authorHue(comment.author)));
      const heading = make("div", "comment-heading");
      heading.append(authorTagButton(comment.author, "comment-author"));
      heading.append(make("span", "comment-time", timeText(comment.created_at)));
      const avatar = avatarNode(comment.author);
      avatar.style.cursor = "pointer";
      avatar.title = `클릭하면 @${comment.author} 태그`;
      avatar.addEventListener("click", () => tagIntoComposer(`@${comment.author}`));
      node.append(avatar, heading, richText("div", "comment-body", comment.body));
      inner.append(node);
    }

    const form = make("form", "reply-form");
    form.id = "reply-form";
    const author = make("input");
    author.name = "author";
    author.value = "human";
    author.setAttribute("aria-label", "댓글 작성자");
    const body = make("textarea");
    body.name = "body";
    body.required = true;
    body.placeholder = "이 글에 답변… 멘션 없이는 호출하지 않고 저장됩니다.";
    body.setAttribute("aria-label", "댓글 내용");
    const actions = make("div", "reply-actions");
    const submit = make("button", "button primary", "댓글 작성");
    submit.type = "submit";
    actions.append(submit);
    form.append(author, body, actions);
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      submit.disabled = true;
      try {
        const item = await request(`/api/threads/${encodeURIComponent(thread.id)}/comments`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ author: author.value || "human", body: body.value }),
        });
        state.highWater = Math.max(state.highWater, Number(item.activity_high_water || 0));
        await selectThread(thread.id, { updateHistory: false, scrollToBottom: true });
        showToast("댓글을 작성했습니다.");
      } catch (error) {
        showToast(`댓글 작성 실패: ${error.message}`);
      } finally {
        submit.disabled = false;
      }
    });
    inner.append(form);

    reader.replaceChildren(inner);
    if (scrollToBottom) reader.scrollTop = reader.scrollHeight;
    else if (preserveScroll) reader.scrollTop = previousScroll;
    else reader.scrollTop = 0;
  };

  const selectThread = async (threadId, options = {}) => {
    const requestNumber = ++state.threadRequest;
    state.selected = threadId;
    markSelected();
    if (options.updateHistory) {
      const next = new URL(window.location.href);
      next.searchParams.set("thread", threadId);
      window.history.pushState({ thread: threadId }, "", next);
    }
    if (!options.preserveScroll) {
      reader.replaceChildren(make("div", "reader-empty", "글 불러오는 중…"));
    }
    try {
      const thread = await request(`/api/threads/${encodeURIComponent(threadId)}`);
      if (requestNumber !== state.threadRequest) return;
      renderThread(thread, options);
      if (options.focusSubjectId) {
        let focusTarget = null;
        for (const candidate of reader.querySelectorAll("[data-subject-id]")) {
          if (candidate.dataset.subjectId === options.focusSubjectId) {
            focusTarget = candidate;
            break;
          }
        }
        if (focusTarget) {
          focusTarget.scrollIntoView({ block: "center" });
          focusTarget.classList.add("mention-focus");
          window.setTimeout(() => focusTarget.classList.remove("mention-focus"), 2200);
        }
      }
      setConnection(true);
      return true;
    } catch (error) {
      if (requestNumber === state.threadRequest) {
        reader.replaceChildren(make("div", "reader-empty", `글을 불러오지 못했습니다: ${error.message}`));
      }
      setConnection(false);
      return false;
    }
  };

  const renderPeers = (agents) => {
    state.peerNames = new Set(
      agents.map((agent) => agent.name.toLocaleLowerCase("en-US"))
    );
    const fingerprint = JSON.stringify(agents.map((agent) => [
      agent.name, agent.process_state, agent.model, agent.effort, agent.retire_reason
    ]));
    if (fingerprint === state.peerFingerprint) return;
    state.peerFingerprint = fingerprint;
    const list = document.getElementById("peer-list");
    list.replaceChildren();
    for (const agent of agents) {
      const row = make("div", "peer");
      row.dataset.peerName = agent.name;
      row.title = `클릭하면 @${agent.name} 태그`;
      row.append(make("span", `state-dot state-${agent.process_state}`));
      const text = make("div");
      const peerName = make("div", "peer-name", agent.name);
      peerName.style.color = `hsl(${authorHue(agent.name)} 60% 74%)`;
      text.append(peerName);
      text.append(make("div", "peer-meta", `${agent.model} · ${agent.effort} · ${agent.process_state}`));
      if (agent.retire_reason) text.append(make("div", "peer-reason", agent.retire_reason));
      row.append(text);
      list.append(row);
    }
    if (!agents.length) list.append(make("div", "empty", "등록된 에이전트가 없습니다."));
  };

  const renderStats = (statistics) => {
    document.getElementById("stat-threads").textContent = statistics.thread_count;
    document.getElementById("stat-comments").textContent = statistics.comment_count;
    document.getElementById("stat-files").textContent = statistics.attachment_count;
    if (!state.query) threadTotal.textContent = `${statistics.thread_count}개`;
  };

  const pollUpdates = async () => {
    if (document.visibilityState === "hidden" || state.polling) return;
    state.polling = true;
    let pollAgain = false;
    try {
      const params = new URLSearchParams({
        after: String(state.highWater),
        mentions_after: String(state.mentionCursor),
      });
      const data = await request(`/api/runs/${encodedRun}/updates?${params}`);
      if (data.new_count > 0) {
        state.pending += data.new_count;
        state.highWater = data.high_water;
        newActivity.textContent = `새 활동 ${state.pending}개 · 목록 갱신`;
        newActivity.hidden = false;
      } else {
        state.highWater = Math.max(state.highWater, data.high_water);
      }
      const mentionData = data.human_mentions || { items: [], cursor: state.mentionCursor };
      enqueueHumanMentions(mentionData.items);
      advanceMentionCursor(mentionData.cursor);
      pollAgain = Boolean(mentionData.has_more);
      renderPeers(data.agents);
      if (data.statistics) renderStats(data.statistics);
      setConnection(true);
    } catch (_error) {
      setConnection(false);
    } finally {
      state.polling = false;
      if (pollAgain) window.setTimeout(pollUpdates, 0);
    }
  };

  document.getElementById("search-form").addEventListener("submit", (event) => {
    event.preventDefault();
    state.query = searchInput.value.trim();
    loadThreads({ reset: true });
  });
  document.getElementById("search-clear").addEventListener("click", () => {
    searchInput.value = "";
    state.query = "";
    loadThreads({ reset: true });
  });
  loadMore.addEventListener("click", () => loadThreads({ reset: false }));
  newActivity.addEventListener("click", async () => {
    state.pending = 0;
    newActivity.hidden = true;
    await loadThreads({ reset: true });
    if (state.selected) {
      await selectThread(state.selected, { updateHistory: false, preserveScroll: true });
    }
  });

  document.getElementById("new-thread-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const submit = form.querySelector("button[type='submit']");
    submit.disabled = true;
    try {
      const item = await request(`/api/runs/${encodedRun}/threads`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          author: form.elements.author.value || "human",
          title: form.elements.title.value,
          body: form.elements.body.value,
        }),
      });
      state.highWater = Math.max(state.highWater, Number(item.activity_high_water || 0));
      form.elements.title.value = "";
      form.elements.body.value = "";
      form.closest("details").open = false;
      state.pending = 0;
      newActivity.hidden = true;
      await loadThreads({ reset: true });
      await selectThread(item.id, { updateHistory: true });
      showToast("새 게시물을 작성했습니다.");
    } catch (error) {
      showToast(`게시물 작성 실패: ${error.message}`);
    } finally {
      submit.disabled = false;
    }
  });

  window.addEventListener("popstate", () => {
    const threadId = new URL(window.location.href).searchParams.get("thread");
    if (threadId) selectThread(threadId, { updateHistory: false });
  });
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") pollUpdates();
  });

  document.addEventListener("focusin", (event) => {
    if (event.target.matches("textarea[name='body']")) {
      state.activeComposer = event.target;
    }
  });
  document.getElementById("peer-list").addEventListener("click", (event) => {
    const row = event.target.closest("[data-peer-name]");
    if (row) tagIntoComposer(`@${row.dataset.peerName}`);
  });
  document.getElementById("tag-all").addEventListener("click", () => {
    tagIntoComposer("@all");
  });

  loadThreads({ reset: true });
  if (state.selected) selectThread(state.selected, { updateHistory: false });
  pollUpdates();
  window.setInterval(pollUpdates, 5000);
  window.setInterval(expireHumanMentions, 30000);
})();
"""
