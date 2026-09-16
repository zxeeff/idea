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
  display: flex;
  flex-direction: column;
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
  flex: 0 0 auto;
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
  flex: 1;
  display: grid;
  grid-template-columns: 238px minmax(310px, 370px) minmax(0, 1fr);
  min-height: 0;
}
.coordination-list { display: grid; gap: 8px; }
.coordination-card {
  display: block;
  width: 100%;
  padding: 8px;
  border: 1px solid var(--line);
  border-radius: 7px;
  background: var(--panel);
  color: var(--text);
  text-align: left;
  cursor: pointer;
  overflow-wrap: anywhere;
  font-size: 12px;
}
.coordination-card:hover { border-color: var(--line-strong); background: var(--panel-raised); }
.coordination-card .peer-meta { display: block; margin-top: 5px; }
.artifact-heading { margin-top: 20px; }
.artifact-files { padding-left: 20px; overflow-wrap: anywhere; }
.artifact-files li { margin: 5px 0; }
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
.peer-summary { margin: 5px 0; color: var(--muted); font-size: 11px; }
.peer-help { color: var(--faint); }
.peer-search { margin: 12px 0 7px; }
.peer-search input { min-width: 0; width: 100%; }
.peer-refresh, .peer-more { width: 100%; margin: 8px 0; }
.comment-page-actions { display: flex; gap: 8px; margin: 14px 0; }
.comment-context { color: var(--muted); font-size: 12px; }
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
  overflow-wrap: anywhere;
  font-size: 14px;
  line-height: 1.72;
}
.markdown-body > :first-child { margin-top: 0; }
.markdown-body > :last-child { margin-bottom: 0; }
.markdown-body p { margin: 0 0 1em; }
.markdown-body h1, .markdown-body h2, .markdown-body h3,
.markdown-body h4, .markdown-body h5, .markdown-body h6 {
  margin: 1.35em 0 .55em;
  color: #f3f6fa;
  font-weight: 760;
  line-height: 1.3;
}
.markdown-body h1 { font-size: 1.65em; }
.markdown-body h2 { padding-bottom: .25em; border-bottom: 1px solid var(--line); font-size: 1.4em; }
.markdown-body h3 { font-size: 1.2em; }
.markdown-body h4, .markdown-body h5, .markdown-body h6 { font-size: 1em; }
.markdown-body ul, .markdown-body ol { margin: .5em 0 1em; padding-left: 1.7em; }
.markdown-body li { margin: .2em 0; padding-left: .15em; }
.markdown-body li > p:first-child { margin-top: 0; }
.markdown-body li > p:last-child { margin-bottom: 0; }
.markdown-body li > ul, .markdown-body li > ol { margin-top: .25em; margin-bottom: .35em; }
.markdown-body li::marker { color: var(--muted); }
.markdown-body blockquote {
  margin: .8em 0 1em;
  padding: .15em 1em;
  border-left: 3px solid #42688e;
  color: var(--muted);
}
.markdown-body code {
  padding: .12em .34em;
  border: 1px solid #2b384b;
  border-radius: 5px;
  background: #151d29;
  color: #dce8f5;
  font: .92em/1.55 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}
.markdown-body pre {
  margin: .8em 0 1.1em;
  overflow-x: auto;
  padding: 13px 15px;
  border: 1px solid #293649;
  border-radius: 9px;
  background: #080d14;
  scrollbar-gutter: stable;
}
.markdown-body pre code {
  padding: 0;
  border: 0;
  background: transparent;
  white-space: pre;
  overflow-wrap: normal;
  color: #d7e1ec;
}
.markdown-body hr { margin: 1.25em 0; border: 0; border-top: 1px solid var(--line-strong); }
.markdown-body .markdown-table-wrap {
  width: 100%;
  margin: .8em 0 1.1em;
  overflow-x: auto;
  border: 1px solid var(--line-strong);
  border-radius: 7px;
  scrollbar-gutter: stable;
}
.markdown-body table {
  width: 100%;
  min-width: max-content;
  margin: 0;
  border-collapse: collapse;
  font-size: .95em;
}
.markdown-body th, .markdown-body td {
  padding: 7px 10px;
  border-right: 1px solid var(--line-strong);
  border-bottom: 1px solid var(--line-strong);
  text-align: left;
  vertical-align: top;
}
.markdown-body th:last-child, .markdown-body td:last-child { border-right: 0; }
.markdown-body tbody tr:last-child td { border-bottom: 0; }
.markdown-body th { background: var(--panel-raised); font-weight: 720; }
.markdown-body tbody tr:nth-child(even) { background: #ffffff05; }
.markdown-body del { color: var(--faint); }
.markdown-body .task-checkbox { margin: 0 .45em 0 0; accent-color: var(--accent); }
.markdown-body a { overflow-wrap: anywhere; }
.markdown-body img {
  display: inline-block;
  max-width: 100%;
  max-height: 70vh;
  margin: .35em 0;
  border: 1px solid var(--line);
  border-radius: 8px;
  object-fit: contain;
  vertical-align: middle;
}
.markdown-body .markdown-raw-link { color: var(--muted); }
.markdown-body .markdown-language {
  display: block;
  margin-bottom: 6px;
  color: var(--faint);
  font: 10px/1.3 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  letter-spacing: .04em;
  text-transform: uppercase;
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
.comment-heading {
  display: flex;
  align-self: center;
  align-items: center;
  flex-wrap: wrap;
  min-width: 0;
  gap: 4px 8px;
}
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
.comment-time { color: var(--faint); font-size: 11px; }
.comment-body { grid-column: 2; margin-top: 8px; }
.event-reference { margin-left: 8px; color: var(--muted); font-size: 11px; }
.comment-heading > .event-reference { margin-left: 0; }
.comment-heading > .reply-action {
  margin-left: auto;
  padding: 4px 10px;
}
.comment-references { grid-column: 2; min-width: 0; margin-top: 10px; color: var(--muted); font-size: 12px; overflow-wrap: anywhere; }
.reference-links { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; }
.reference-links .event-reference { margin-left: 0; }
.relation-label { color: var(--text); }
.validation-report { margin: 8px 0 0; padding-left: 10px; border-left: 2px solid var(--line); white-space: pre-wrap; }
.reply-target { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; color: var(--muted); font-size: 12px; }
.reply-details { min-width: 0; padding: 8px 0; color: var(--muted); font-size: 12px; }
.reply-details summary { cursor: pointer; }
.reply-fields { display: grid; gap: 10px; margin-top: 12px; }
.reply-fields label { display: grid; gap: 5px; }
.reply-fields select { width: 100%; padding: 9px; border: 1px solid var(--line); border-radius: 7px; color: var(--text); background: var(--panel); }
.reply-fields textarea { min-height: 80px; }
.reply-fields p { margin: 0; }
.investigation-section { margin: 20px 0; }
.investigation-card { margin: 10px 0; padding: 14px; border: 1px solid var(--line); border-radius: 10px; background: var(--panel); overflow-wrap: anywhere; }
.investigation-card h3 { margin: 0 0 8px; font-size: 14px; }
.investigation-card p { margin: 7px 0; white-space: pre-wrap; }
.investigation-note { color: var(--muted); font-size: 12px; white-space: pre-wrap; overflow-wrap: anywhere; }
.investigation-links { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.contribution-form { display: grid; gap: 10px; margin-top: 14px; }
.contribution-form .reply-fields { margin-top: 0; }
.member-row { padding: 9px 0; border-bottom: 1px solid var(--line); overflow-wrap: anywhere; }
.approach-choice { display: grid; gap: 8px; }
.approach-choice .search-form { grid-template-columns: minmax(0, 1fr) auto; }
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
  body { display: block; height: auto; overflow: auto; }
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
@media (max-width: 600px) {
  .topbar { flex-wrap: wrap; gap: 8px; padding: 12px 15px; }
  .topbar > div:first-child { width: 100%; min-width: 0; }
  .brand { flex-shrink: 0; }
  .run-chip { min-width: 0; }
  .goal { max-width: 100%; }
  .topbar-actions { width: 100%; justify-content: space-between; }
  .logout-button { white-space: nowrap; }
  .human-mentions { top: 126px; }
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
    selectedArtifact: urlState.searchParams.get("artifact"),
    selectedApproach: urlState.searchParams.get("approach"),
    selectedReport: urlState.searchParams.get("report"),
    highWater: initialHighWater,
    pending: 0,
    loadingList: false,
    listRequest: 0,
    threadRequest: 0,
    peerCursor: app.dataset.peerCursor || null,
    peerVersion: app.dataset.peerVersion || "",
    peerQuery: "",
    peerRequest: 0,
    loadingPeers: false,
    currentThread: null,
    approachCursor: null,
    approachQuery: "",
    approachRequest: 0,
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
      let title = "이 목록에 없는 peer 멘션 · 이름을 검색해 확인하세요.";
      if (name === "all") {
        kind = "all";
        title = "은퇴하지 않은 모든 peer에게 알림을 남깁니다.";
      } else if (name === "human" || name === "user") {
        kind = "human";
        title = "웹 포럼 사용자를 향한 멘션";
      } else if (state.peerNames.has(name)) {
        kind = "peer";
        title = `${token} peer에게 알림을 남기는 정확한 멘션`;
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

  const safeMarkdownLink = (rawValue) => {
    const value = String(rawValue || "").trim();
    if (/^(https?:|mailto:)/i.test(value)) return value;
    if (/^(?:#|\?|\/(?!\/)|\.\.?\/)/.test(value)) return value;
    return null;
  };

  const safeMarkdownImage = (rawValue) => {
    const value = safeMarkdownLink(rawValue);
    return value && !/^(?:mailto:|#|\?)/i.test(value) ? value : null;
  };

  const isEscapedMarkdown = (value, index) => {
    let backslashes = 0;
    for (let cursor = index - 1; cursor >= 0 && value[cursor] === "\\"; cursor -= 1) {
      backslashes += 1;
    }
    return backslashes % 2 === 1;
  };

  const unescapeMarkdown = (value) => String(value || "").replace(
    /\\([\\`*_[\]{}()#+\-.!|>~])/gu,
    "$1"
  );

  const appendMarkdownText = (node, value) =>
    appendMentionText(node, unescapeMarkdown(value));

  // Regex-only link parsing truncates destinations such as `guide_(draft).md`.
  // Scan balanced brackets and parentheses so ordinary CommonMark links survive.
  const findMarkdownLink = (value) => {
    for (let labelStart = 0; labelStart < value.length; labelStart += 1) {
      if (value[labelStart] !== "[" || isEscapedMarkdown(value, labelStart)) continue;
      const image = labelStart > 0
        && value[labelStart - 1] === "!"
        && !isEscapedMarkdown(value, labelStart - 1);
      let labelEnd = labelStart + 1;
      let bracketDepth = 1;
      for (; labelEnd < value.length; labelEnd += 1) {
        if (isEscapedMarkdown(value, labelEnd)) continue;
        if (value[labelEnd] === "[") bracketDepth += 1;
        if (value[labelEnd] === "]") bracketDepth -= 1;
        if (bracketDepth === 0) break;
      }
      if (bracketDepth || value[labelEnd + 1] !== "(") continue;

      let destinationEnd = labelEnd + 2;
      let parenthesisDepth = 1;
      let angleDestination = false;
      for (; destinationEnd < value.length; destinationEnd += 1) {
        const character = value[destinationEnd];
        if (isEscapedMarkdown(value, destinationEnd)) continue;
        if (character === "<" && parenthesisDepth === 1) angleDestination = true;
        if (character === ">" && angleDestination) angleDestination = false;
        if (!angleDestination && character === "(") parenthesisDepth += 1;
        if (!angleDestination && character === ")") parenthesisDepth -= 1;
        if (parenthesisDepth === 0) break;
      }
      if (parenthesisDepth) continue;

      const inside = value.slice(labelEnd + 2, destinationEnd).trim();
      const destination = /^(<[^<>]+>|\S+?)(?:\s+(?:"([^"]*)"|'([^']*)'|\(([^()]*)\)))?$/u.exec(inside);
      if (!destination) continue;
      const rawDestination = destination[1].startsWith("<")
        ? destination[1].slice(1, -1)
        : destination[1];
      const start = image ? labelStart - 1 : labelStart;
      const full = value.slice(start, destinationEnd + 1);
      return {
        index: start,
        0: full,
        label: value.slice(labelStart + 1, labelEnd),
        destination: unescapeMarkdown(rawDestination),
        title: destination[2] ?? destination[3] ?? destination[4] ?? "",
        image,
      };
    }
    return null;
  };

  const appendInlineMarkdown = (node, rawText, depth = 0) => {
    let value = String(rawText || "");
    if (depth > 8) return appendMarkdownText(node, value);
    const rules = [
      {
        find: findMarkdownLink,
        render: (match) => {
          if (match.image) {
            const src = safeMarkdownImage(match.destination);
            if (!src) return make("span", "markdown-raw-link", match[0]);
            const picture = make("img", "");
            picture.src = src;
            picture.alt = unescapeMarkdown(match.label);
            if (match.title) picture.title = match.title;
            picture.loading = "lazy";
            picture.decoding = "async";
            picture.referrerPolicy = "no-referrer";
            return picture;
          }
          const href = safeMarkdownLink(match.destination);
          if (!href) return make("span", "markdown-raw-link", match[0]);
          const link = make("a", "");
          link.href = href;
          if (match.title) link.title = match.title;
          if (/^https?:/i.test(href)) {
            link.target = "_blank";
            link.rel = "noopener noreferrer";
          }
          appendInlineMarkdown(link, match.label, depth + 1);
          return link;
        },
      },
      {
        find: (text) => /(?<!\\)(`+)(?!`)([^\n]*?)\1(?!`)/u.exec(text),
        render: (match) => make("code", "", match[2].replace(/^ | $/gu, "")),
      },
      {
        find: (text) => /<((?:https?:\/\/|mailto:)[^<>\s]+|[^<>\s@]+@[^<>\s@]+\.[^<>\s@]+)>/iu.exec(text),
        render: (match) => {
          const href = safeMarkdownLink(
            match[1].includes(":") ? match[1] : `mailto:${match[1]}`
          );
          if (!href) return make("span", "markdown-raw-link", match[0]);
          const link = make("a", "", match[1]);
          link.href = href;
          if (/^https?:/i.test(href)) {
            link.target = "_blank";
            link.rel = "noopener noreferrer";
          }
          return link;
        },
      },
      {
        find: (text) => /(?<!\\)\*\*(?![\s*])(.+?)(?<!\s)\*\*(?!\*)/u.exec(text),
        render: (match) => {
          const strong = make("strong", "");
          appendInlineMarkdown(strong, match[1], depth + 1);
          return strong;
        },
      },
      {
        find: (text) => /(?<![\\\p{L}\p{N}])__(?![\s_])(.+?)(?<!\s)__(?![\p{L}\p{N}_])/u.exec(text),
        render: (match) => {
          const strong = make("strong", "");
          appendInlineMarkdown(strong, match[1], depth + 1);
          return strong;
        },
      },
      {
        find: (text) => /(?<!\\)~~(?!\s)(.+?)(?<!\s)~~/u.exec(text),
        render: (match) => {
          const deleted = make("del", "");
          appendInlineMarkdown(deleted, match[1], depth + 1);
          return deleted;
        },
      },
      {
        find: (text) => /(?<![\\*])\*(?![\s*])(.+?)(?<![\s*])\*(?!\*)/u.exec(text),
        render: (match) => {
          const emphasis = make("em", "");
          appendInlineMarkdown(emphasis, match[1], depth + 1);
          return emphasis;
        },
      },
      {
        find: (text) => /(?<![\\\p{L}\p{N}_])_(?![\s_])(.+?)(?<![\s_])_(?![\p{L}\p{N}_])/u.exec(text),
        render: (match) => {
          const emphasis = make("em", "");
          appendInlineMarkdown(emphasis, match[1], depth + 1);
          return emphasis;
        },
      },
    ];

    while (value) {
      let selected = null;
      for (const rule of rules) {
        const match = rule.find(value);
        if (match && (!selected || match.index < selected.match.index)) {
          selected = { rule, match };
        }
      }
      if (!selected) {
        appendMarkdownText(node, value);
        break;
      }
      if (selected.match.index) {
        appendMarkdownText(node, value.slice(0, selected.match.index));
      }
      node.append(selected.rule.render(selected.match));
      value = value.slice(selected.match.index + selected.match[0].length);
    }
    return node;
  };

  const splitMarkdownRow = (line) => {
    const cells = [];
    let cell = "";
    let codeFenceLength = 0;
    const value = String(line).trim();
    for (let index = 0; index < value.length; index += 1) {
      const character = value[index];
      if (character === "\\" && ["\\", "|"].includes(value[index + 1])) {
        cell += value[index + 1];
        index += 1;
      } else if (character === "`") {
        let runLength = 1;
        while (value[index + runLength] === "`") runLength += 1;
        if (!codeFenceLength) codeFenceLength = runLength;
        else if (codeFenceLength === runLength) codeFenceLength = 0;
        cell += "`".repeat(runLength);
        index += runLength - 1;
      } else if (character === "|" && !codeFenceLength) {
        cells.push(cell.trim());
        cell = "";
      } else {
        cell += character;
      }
    }
    cells.push(cell.trim());
    if (cells[0] === "") cells.shift();
    if (cells.at(-1) === "") cells.pop();
    return cells;
  };

  const tableSeparator = (line) => {
    const cells = splitMarkdownRow(line);
    return cells.length > 0 && cells.every((cell) => /^:?-{3,}:?$/.test(cell));
  };

  const markdownTableAt = (lines, index) => {
    if (index + 1 >= lines.length || !lines[index].includes("|")) return null;
    const headers = splitMarkdownRow(lines[index]);
    const separators = splitMarkdownRow(lines[index + 1]);
    if (
      headers.length < 2
      || headers.length !== separators.length
      || !tableSeparator(lines[index + 1])
    ) return null;
    return { headers, separators };
  };

  const markdownIndent = (line) => {
    let columns = 0;
    for (const character of String(line || "")) {
      if (character === " ") columns += 1;
      else if (character === "\t") columns += 4 - (columns % 4);
      else break;
    }
    return columns;
  };

  const stripMarkdownIndent = (line, columns) => {
    let consumed = 0;
    let index = 0;
    while (index < line.length && consumed < columns) {
      if (line[index] === " ") consumed += 1;
      else if (line[index] === "\t") consumed += 4 - (consumed % 4);
      else break;
      index += 1;
    }
    return line.slice(index);
  };

  const markdownListItem = (line) => {
    const match = /^(\s*)([-+*]|\d+[.)])([ \t]+)(.*)$/u.exec(line);
    if (!match) return null;
    const indent = markdownIndent(match[1]);
    return {
      indent,
      contentIndent: indent + match[2].length + markdownIndent(match[3]),
      ordered: /^\d/u.test(match[2]),
      start: Number.parseInt(match[2], 10) || 1,
      content: match[4],
    };
  };

  const markdownBlockStart = (lines, index) => {
    const line = lines[index] || "";
    if (!line.trim()) return true;
    if (/^ {0,3}(`{3,}|~{3,})/.test(line)) return true;
    if (/^(?: {4}|\t)/.test(line)) return true;
    if (/^ {0,3}#{1,6}\s+/.test(line)) return true;
    if (/^ {0,3}>\s?/.test(line)) return true;
    if (markdownListItem(line)) return true;
    if (/^ {0,3}(?:-{3,}|\*{3,}|_{3,})\s*$/.test(line)) return true;
    return Boolean(markdownTableAt(lines, index));
  };

  const appendMarkdownBlocks = (node, rawText) => {
    const lines = String(rawText || "").replace(/\r\n?/g, "\n").split("\n");
    let index = 0;
    while (index < lines.length) {
      const line = lines[index];
      if (!line.trim()) {
        index += 1;
        continue;
      }

      const fence = /^ {0,3}(`{3,}|~{3,})\s*([^\s`]*)\s*$/.exec(line);
      if (fence) {
        const marker = fence[1];
        const language = fence[2];
        const content = [];
        index += 1;
        while (index < lines.length && !new RegExp(`^ {0,3}${marker[0]}{${marker.length},}\\s*$`).test(lines[index])) {
          content.push(lines[index]);
          index += 1;
        }
        if (index < lines.length) index += 1;
        const pre = make("pre", "");
        if (language) pre.append(make("span", "markdown-language", language));
        const code = make("code", language ? `language-${language.replace(/[^a-z0-9_-]/gi, "")}` : "", content.join("\n"));
        pre.append(code);
        node.append(pre);
        continue;
      }

      if (/^(?: {4}|\t)/.test(line)) {
        const content = [];
        while (index < lines.length && (!lines[index].trim() || /^(?: {4}|\t)/.test(lines[index]))) {
          content.push(lines[index].trim() ? lines[index].replace(/^(?: {4}|\t)/, "") : "");
          index += 1;
        }
        while (content.at(-1) === "") content.pop();
        const pre = make("pre", "");
        pre.append(make("code", "", content.join("\n")));
        node.append(pre);
        continue;
      }

      const setext = index + 1 < lines.length
        ? /^ {0,3}(=+|-+)\s*$/.exec(lines[index + 1])
        : null;
      if (setext && line.trim()) {
        const headingNode = make(setext[1][0] === "=" ? "h1" : "h2", "");
        appendInlineMarkdown(headingNode, line.trim());
        node.append(headingNode);
        index += 2;
        continue;
      }

      const heading = /^ {0,3}(#{1,6})\s+(.+?)\s*#*\s*$/.exec(line);
      if (heading) {
        const headingNode = make(`h${heading[1].length}`, "");
        appendInlineMarkdown(headingNode, heading[2]);
        node.append(headingNode);
        index += 1;
        continue;
      }

      if (/^ {0,3}(?:-{3,}|\*{3,}|_{3,})\s*$/.test(line)) {
        node.append(make("hr", ""));
        index += 1;
        continue;
      }

      const tableMatch = markdownTableAt(lines, index);
      if (tableMatch) {
        const { headers, separators } = tableMatch;
        const table = make("table", "");
        const head = make("thead", "");
        const headRow = make("tr", "");
        headers.forEach((header, column) => {
          const cell = make("th", "");
          const separator = separators[column] || "";
          cell.style.textAlign = separator.startsWith(":") && separator.endsWith(":")
            ? "center" : (separator.endsWith(":") ? "right" : "left");
          appendInlineMarkdown(cell, header);
          headRow.append(cell);
        });
        head.append(headRow);
        table.append(head);
        const body = make("tbody", "");
        index += 2;
        while (index < lines.length && lines[index].trim() && lines[index].includes("|")) {
          const values = splitMarkdownRow(lines[index]);
          if (values.length < 2) break;
          const row = make("tr", "");
          headers.forEach((_header, column) => {
            const cell = make("td", "");
            cell.style.textAlign = headRow.children[column].style.textAlign;
            appendInlineMarkdown(cell, values[column] || "");
            row.append(cell);
          });
          body.append(row);
          index += 1;
        }
        table.append(body);
        const tableWrap = make("div", "markdown-table-wrap");
        tableWrap.append(table);
        node.append(tableWrap);
        continue;
      }

      if (/^ {0,3}>\s?/.test(line)) {
        const quoteLines = [];
        while (index < lines.length && (/^ {0,3}>\s?/.test(lines[index]) || !lines[index].trim())) {
          quoteLines.push(lines[index].replace(/^ {0,3}>\s?/, ""));
          index += 1;
        }
        const quote = make("blockquote", "");
        appendMarkdownBlocks(quote, quoteLines.join("\n"));
        node.append(quote);
        continue;
      }

      const listMatch = markdownListItem(line);
      if (listMatch) {
        const ordered = listMatch.ordered;
        const baseIndent = listMatch.indent;
        const list = make(ordered ? "ol" : "ul", "");
        if (ordered) list.start = listMatch.start;
        while (index < lines.length) {
          const itemMatch = markdownListItem(lines[index]);
          if (!itemMatch || itemMatch.indent !== baseIndent || itemMatch.ordered !== ordered) break;
          const itemLines = [itemMatch.content];
          index += 1;
          while (index < lines.length) {
            const nextItem = markdownListItem(lines[index]);
            if (nextItem && nextItem.indent === baseIndent) break;
            if (lines[index].trim() && markdownIndent(lines[index]) <= baseIndent) break;
            itemLines.push(
              lines[index].trim()
                ? stripMarkdownIndent(lines[index], itemMatch.contentIndent)
                : ""
            );
            index += 1;
          }
          const item = make("li", "");
          const task = /^\[([ xX])\]\s+(.*)$/u.exec(itemLines[0]);
          if (task) {
            itemLines[0] = task[2];
          }
          appendMarkdownBlocks(item, itemLines.join("\n"));
          if (task) {
            const checkbox = make("input", "task-checkbox");
            checkbox.type = "checkbox";
            checkbox.checked = task[1].toLocaleLowerCase("en-US") === "x";
            checkbox.disabled = true;
            const firstParagraph = item.firstElementChild?.tagName === "P"
              ? item.firstElementChild
              : item;
            firstParagraph.prepend(checkbox);
          }
          list.append(item);
        }
        node.append(list);
        continue;
      }

      const paragraphLines = [line];
      index += 1;
      while (index < lines.length && !markdownBlockStart(lines, index)) {
        paragraphLines.push(lines[index]);
        index += 1;
      }
      const paragraph = make("p", "");
      paragraphLines.forEach((paragraphLine, lineIndex) => {
        if (lineIndex) paragraph.append(make("br", ""));
        appendInlineMarkdown(paragraph, paragraphLine);
      });
      node.append(paragraph);
    }
    return node;
  };

  const markdownText = (tag, className, text) =>
    appendMarkdownBlocks(make(tag, `${className} markdown-body`), text);

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
      if (!state.selected && !state.selectedArtifact && !state.selectedApproach && !state.selectedReport && data.items.length) {
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

  const relationLabels = {
    reply: "일반 답글", supports: "근거 보강", challenges: "반박",
    verifies: "검증 보고", retracts: "본인 글 철회", supersedes: "본인 글 교체",
  };

  const eventReference = (reference, label) => {
    if (!reference?.thread_id || !reference?.subject_id) return make("span", "event-reference", label);
    const link = make("a", "event-reference", label);
    const params = new URLSearchParams({ run: runId, thread: reference.thread_id, focus: reference.subject_id });
    link.href = `/?${params}`;
    link.addEventListener("click", (event) => {
      event.preventDefault();
      selectThread(reference.thread_id, { updateHistory: true, focusSubjectId: reference.subject_id });
    });
    return link;
  };

  const chooseReplyTarget = (record) => {
    const form = document.getElementById("reply-form");
    if (!form || !record.event_id) return;
    form.dataset.replyToEventId = String(record.event_id);
    form.querySelector(".reply-target-label").textContent = record.id === state.currentThread?.id
      ? `원글에 답글 · 이벤트 #${record.event_id}`
      : `${record.author}의 댓글에 답글 · 이벤트 #${record.event_id}`;
    form.querySelector("textarea[name=body]").focus();
    form.scrollIntoView({ block: "nearest" });
  };

  const replyTargetButton = (record) => {
    const button = make("button", "button quiet reply-action", "답글");
    button.type = "button";
    button.disabled = !record.event_id;
    button.setAttribute("aria-label", `${record.author}의 ${record.thread_id ? "댓글" : "원글"}에 답글`);
    button.addEventListener("click", () => chooseReplyTarget(record));
    return button;
  };

  const commentReferences = (comment) => {
    const metadata = comment.provenance;
    if (!metadata) return null;
    const block = make("div", "comment-references");
    const links = make("div", "reference-links");
    links.append(make("span", "relation-label", relationLabels[metadata.relation] || "일반 답글"));
    if (metadata.reply_to_event_id) {
      links.append(eventReference(metadata.reply_to_event, `대상 #${metadata.reply_to_event_id}`));
    }
    for (const reference of metadata.evidence_events || []) {
      links.append(eventReference(reference, `근거 #${reference.event_id}`));
    }
    if (metadata.artifact_id) {
      const artifact = make("a", "event-reference", "참조 결과물");
      artifact.href = `/?run=${encodedRun}&artifact=${encodeURIComponent(metadata.artifact_id)}`;
      artifact.addEventListener("click", (event) => {
        event.preventDefault();
        selectArtifact(metadata.artifact_id);
      });
      links.append(artifact);
    }
    block.append(links);
    if (metadata.validation) {
      block.append(make("div", "validation-report", `${comment.author}의 검증 보고\n${metadata.validation}`));
    }
    return block;
  };

  const commentNode = (comment) => {
    const node = make("article", "comment");
    node.dataset.subjectId = comment.id;
    node.style.setProperty("--avatar-hue", String(authorHue(comment.author)));
    const heading = make("div", "comment-heading");
    heading.append(authorTagButton(comment.author, "comment-author"));
    heading.append(make("span", "comment-time", timeText(comment.created_at)));
    if (comment.event_id) heading.append(eventReference(
      { thread_id: comment.thread_id, subject_id: comment.id }, `#${comment.event_id}`
    ));
    heading.append(replyTargetButton(comment));
    const avatar = avatarNode(comment.author);
    avatar.style.cursor = "pointer";
    avatar.title = `클릭하면 @${comment.author} 태그`;
    avatar.addEventListener("click", () => tagIntoComposer(`@${comment.author}`));
    node.append(avatar, heading);
    const reported = comment.report || comment.exchange?.source_report;
    if (reported) {
      const contribution = make("div", "comment-body");
      if (comment.exchange) {
        contribution.append(make("p", "investigation-note", "다른 접근법에서 가져온 중간 성과"));
        contribution.append(make("p", "investigation-note", `적용 메모: ${comment.exchange.application}`));
      }
      contribution.append(reportCard(reported));
      const original = make("details", "reply-details");
      original.append(make("summary", "", "원문 전체"), markdownText("div", "", comment.body));
      contribution.append(original);
      node.append(contribution);
    } else node.append(markdownText("div", "comment-body", comment.body));
    const references = commentReferences(comment);
    if (references) node.append(references);
    return node;
  };

  const loadComments = async (thread, button, container) => {
    if (button.disabled || !thread.comments_next_cursor) return;
    button.disabled = true;
    const requestNumber = state.threadRequest;
    try {
      const params = new URLSearchParams({ limit: "30", after: thread.comments_next_cursor });
      const page = await request(`/api/threads/${encodeURIComponent(thread.id)}/comments?${params}`);
      if (requestNumber !== state.threadRequest) return;
      for (const comment of page.items) {
        if (thread.comments.some((existing) => existing.id === comment.id)) continue;
        thread.comments.push(comment);
        container.append(commentNode(comment));
        if (thread.focus_comment?.id === comment.id) {
          reader.querySelector(".focused-comment")?.remove();
          thread.focus_comment = null;
        }
      }
      thread.comments_next_cursor = page.next_cursor;
      button.hidden = !page.next_cursor;
      button.textContent = `다음 댓글 더 보기 · ${thread.comments.length}개 읽음`;
    } catch (error) {
      showToast(`댓글을 가져오지 못했습니다: ${error.message}`);
    } finally {
      button.disabled = false;
    }
  };

  const renderThread = (thread, { preserveScroll = false, scrollToBottom = false } = {}) => {
    const previousScroll = reader.scrollTop;
    const inner = make("article", "reader-inner");
    inner.dataset.testid = "thread-reader";
    inner.append(richText("h1", "thread-heading", thread.title));
    const postMeta = make("div", "post-meta");
    postMeta.append(authorTagButton(thread.author, "card-author"));
    postMeta.append(document.createTextNode(` · ${timeText(thread.created_at)} · ${thread.id}`));
    if (thread.event_id) postMeta.append(eventReference(
      { thread_id: thread.id, subject_id: thread.id }, `#${thread.event_id}`
    ));
    postMeta.append(replyTargetButton(thread));
    inner.append(postMeta);
    const postBody = markdownText("div", "post-body", thread.body);
    postBody.dataset.subjectId = thread.id;
    inner.append(postBody);

    if (thread.attachments.length) {
      const block = make("section", "attachment-block");
      block.append(make("div", "section-title", `첨부파일 ${thread.attachments.length}`));
      for (const item of thread.attachments) block.append(attachmentNode(item));
      inner.append(block);
    }

    const investigation = make("section", "investigation-section");
    investigation.setAttribute("aria-label", "이 논의의 접근법과 중간 성과");
    inner.append(investigation);
    loadThreadInvestigations(thread, investigation);

    inner.append(make("h2", "comments-heading", `댓글 ${thread.comment_count ?? thread.comments.length}`));
    if (!thread.comments.length) inner.append(make("div", "empty", "아직 댓글이 없습니다."));
    const comments = make("section", "comment-list");
    for (const comment of thread.comments) comments.append(commentNode(comment));
    inner.append(comments);
    const commentActions = make("div", "comment-page-actions");
    const moreComments = make("button", "button quiet", "다음 댓글 더 보기");
    moreComments.type = "button";
    moreComments.hidden = !thread.comments_next_cursor;
    moreComments.addEventListener("click", () => loadComments(thread, moreComments, comments));
    commentActions.append(moreComments);
    inner.append(commentActions);
    if (thread.focus_comment) {
      const focus = make("section", "focused-comment");
      focus.append(make("p", "comment-context", "선택한 댓글 · 앞선 댓글은 더 보기로 읽을 수 있습니다."));
      focus.append(commentNode(thread.focus_comment));
      inner.append(focus);
    }

    const form = make("form", "reply-form");
    form.id = "reply-form";
    form.dataset.replyToEventId = thread.event_id ? String(thread.event_id) : "";
    const target = make("div", "reply-target");
    target.append(make("span", "reply-target-label", `원글에 답글${thread.event_id ? ` · 이벤트 #${thread.event_id}` : ""}`));
    const resetTarget = make("button", "button quiet", "원글로 변경");
    resetTarget.type = "button";
    resetTarget.addEventListener("click", () => chooseReplyTarget(thread));
    target.append(resetTarget);
    const author = make("input");
    author.name = "author";
    author.value = "human";
    author.setAttribute("aria-label", "댓글 작성자");
    const body = make("textarea");
    body.name = "body";
    body.required = true;
    body.placeholder = "이 글에 답변… 구독자에게 알림이 전달됩니다. 특정 동료는 @이름";
    body.setAttribute("aria-label", "댓글 내용");
    const details = make("details", "reply-details");
    details.append(make("summary", "", "관계·근거 추가 (선택)"));
    const fields = make("div", "reply-fields");
    const field = (label, input) => {
      const wrapper = make("label", "", label);
      wrapper.append(input);
      fields.append(wrapper);
      return input;
    };
    const relation = make("select");
    relation.name = "relation";
    for (const [value, label] of Object.entries(relationLabels)) {
      const option = make("option", "", label);
      option.value = value;
      relation.append(option);
    }
    field("대상과의 관계", relation);
    const artifact = field("참조 결과물 ID", make("input"));
    artifact.name = "artifact_id";
    artifact.placeholder = "artifact_…";
    const evidence = field("근거 이벤트 번호 (쉼표로 구분, 최대 16개)", make("input"));
    evidence.name = "evidence_event_ids";
    evidence.placeholder = "12, 18";
    evidence.pattern = "[0-9]+(?:\\s*,\\s*[0-9]+)*";
    const validation = field("작성자가 보고하는 검증 내용", make("textarea"));
    validation.name = "validation";
    validation.placeholder = "실행한 확인 방법과 결과, 남은 한계";
    relation.addEventListener("change", () => {
      validation.required = relation.value === "verifies";
      validation.setCustomValidity("");
    });
    validation.addEventListener("input", () => validation.setCustomValidity(""));
    fields.append(make("p", "", "관계와 검증은 작성자의 보고입니다. 철회·교체는 원작성자만 할 수 있습니다."));
    details.append(fields);
    form.addEventListener("invalid", (event) => {
      if (details.contains(event.target)) details.open = true;
    }, true);
    const actions = make("div", "reply-actions");
    const submit = make("button", "button primary", "댓글 작성");
    submit.type = "submit";
    actions.append(submit);
    form.append(target, author, body, details, actions);
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (relation.value === "verifies" && !validation.value.trim()) {
        details.open = true;
        validation.setCustomValidity("검증 방법과 결과를 입력하세요.");
        validation.reportValidity();
        return;
      }
      const evidenceIds = evidence.value.trim() ? evidence.value.split(",").map((value) => Number(value.trim())) : [];
      if (evidenceIds.length > 16 || evidenceIds.some((value) => !Number.isSafeInteger(value) || value < 1)) {
        details.open = true;
        showToast("근거 이벤트는 양의 정수로 최대 16개 입력하세요.");
        return;
      }
      submit.disabled = true;
      try {
        const item = await request(`/api/threads/${encodeURIComponent(thread.id)}/comments`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            author: author.value || "human", body: body.value,
            reply_to_event_id: form.dataset.replyToEventId ? Number(form.dataset.replyToEventId) : null,
            relation: relation.value, artifact_id: artifact.value.trim() || null,
            validation: validation.value, evidence_event_ids: evidenceIds,
          }),
        });
        state.highWater = Math.max(state.highWater, Number(item.activity_high_water || 0));
        await selectThread(thread.id, {
          updateHistory: false, scrollToBottom: true, focusSubjectId: item.id,
        });
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
    state.selectedArtifact = null;
    state.selectedApproach = null;
    state.selectedReport = null;
    markSelected();
    if (options.updateHistory) {
      const next = new URL(window.location.href);
      next.searchParams.set("thread", threadId);
      next.searchParams.delete("artifact");
      next.searchParams.delete("approach");
      next.searchParams.delete("report");
      if (options.focusSubjectId) next.searchParams.set("focus", options.focusSubjectId);
      else next.searchParams.delete("focus");
      window.history.pushState({ thread: threadId }, "", next);
    }
    if (!options.preserveScroll) {
      reader.replaceChildren(make("div", "reader-empty", "글 불러오는 중…"));
    }
    try {
      const thread = await request(`/api/threads/${encodeURIComponent(threadId)}?comments_limit=30`);
      if (options.focusSubjectId && options.focusSubjectId.startsWith("comment_")
          && !thread.comments.some((comment) => comment.id === options.focusSubjectId)) {
        thread.focus_comment = await request(
          `/api/threads/${encodeURIComponent(threadId)}/comments/${encodeURIComponent(options.focusSubjectId)}`
        );
      }
      if (requestNumber !== state.threadRequest) return;
      state.currentThread = thread;
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

  const renderPeers = (agents, append = false) => {
    const list = document.getElementById("peer-list");
    if (!append) list.replaceChildren();
    for (const agent of agents) {
      state.peerNames.add(agent.name.toLocaleLowerCase("en-US"));
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
    if (!append && !agents.length) {
      list.append(make("div", "empty", state.peerQuery ? "일치하는 동료가 없습니다." : "등록된 에이전트가 없습니다."));
    }
  };

  const renderPeerSummary = (summary) => {
    document.getElementById("peer-total").textContent = `Peers ${summary.total_count}`;
    document.getElementById("peer-states").textContent =
      `Running ${summary.states.running || 0} · Dormant ${summary.states.dormant || 0}`;
    document.getElementById("peer-refresh").hidden = summary.version === state.peerVersion;
  };

  const loadPeers = async ({ reset = false } = {}) => {
    if (state.loadingPeers && !reset) return;
    const requestNumber = ++state.peerRequest;
    state.loadingPeers = true;
    const more = document.getElementById("peer-load-more");
    more.disabled = true;
    const params = new URLSearchParams({ limit: "30", q: state.peerQuery });
    if (!reset && state.peerCursor) params.set("after", state.peerCursor);
    try {
      const data = await request(`/api/runs/${encodedRun}/agents?${params}`);
      if (requestNumber !== state.peerRequest) return;
      renderPeers(data.items, !reset);
      state.peerCursor = data.next_cursor;
      // Only a full refresh makes all currently visible peer states current.
      if (reset) state.peerVersion = data.summary.version;
      more.hidden = !data.next_cursor;
      renderPeerSummary(data.summary);
    } catch (error) {
      showToast(`동료 목록을 가져오지 못했습니다: ${error.message}`);
    } finally {
      if (requestNumber === state.peerRequest) {
        state.loadingPeers = false;
        more.disabled = false;
      }
    }
  };

  const discussionLink = (kind, id, label) => {
    const link = make("a", "", label);
    link.href = `/?run=${encodedRun}&${kind}=${encodeURIComponent(id)}`;
    link.addEventListener("click", (event) => {
      event.preventDefault();
      if (kind === "thread") selectThread(id, { updateHistory: true });
      else selectInvestigation(kind, id);
    });
    return link;
  };

  const originalLinks = (events, label) => {
    const links = make("div", "investigation-links");
    for (const event of events || []) links.append(eventReference(event, `${label} #${event.event_id}`));
    return links;
  };

  const reportCard = (item) => {
    const card = make("article", "investigation-card");
    const heading = make("h3");
    heading.append(discussionLink("report", item.id, item.summary));
    card.append(heading, make("div", "investigation-note", `${item.author} · ${timeText(item.created_at)}${item.is_superseded ? " · 후속 보고 있음" : ""}`));
    card.append(make("p", "investigation-note", `적용 조건: ${item.conditions}`));
    if (item.open_questions) card.append(make("p", "investigation-note", `남은 질문: ${item.open_questions}`));
    if (item.content_truncated) card.append(make("p", "investigation-note", "일부 내용 미리보기 · 제목을 눌러 전체 보기"));
    if (item.source_changes?.total_count) {
      const changed = make("p", "investigation-note");
      changed.append(discussionLink("report", item.id, `근거에 반박·철회·대체 ${item.source_changes.total_count}건 · 상세에서 확인`));
      card.append(changed);
    }
    card.append(originalLinks(item.source_events, "원본"));
    return card;
  };

  const approachCard = (item) => {
    const card = make("article", "investigation-card");
    const heading = make("h3");
    heading.append(discussionLink("approach", item.id, item.hypothesis));
    card.append(heading, make("p", "investigation-note", `다음 확인: ${item.next_check}`));
    card.append(make("div", "investigation-note", `참여 ${item.member_count || 0}명 · Running ${item.running_members || 0}`));
    if (item.hypothesis_truncated || item.next_check_truncated) card.append(make("p", "investigation-note", "일부 내용 미리보기 · 가설을 눌러 전체 보기"));
    return card;
  };

  const pagedSection = (container, path, parameters, render, emptyText, moreText) => {
    const list = make("div");
    const more = make("button", "button quiet", moreText);
    more.type = "button";
    more.hidden = true;
    container.append(list, more);
    let cursor = null;
    let loading = false;
    const load = async () => {
      if (loading) return;
      loading = true;
      more.disabled = true;
      try {
        const query = new URLSearchParams(parameters);
        if (cursor) query.set("after", cursor);
        const page = await request(`${path}?${query}`);
        if (!container.isConnected) return;
        if (!cursor && !page.items.length) list.append(make("p", "investigation-note", emptyText));
        for (const item of page.items) list.append(render(item));
        cursor = page.next_cursor;
        more.hidden = !cursor;
      } catch (error) {
        showToast(error.message);
      } finally {
        loading = false;
        more.disabled = false;
      }
    };
    more.addEventListener("click", load);
    return load;
  };

  const eventIds = (value) => {
    if (!value.trim()) return [];
    const ids = value.split(",").map((item) => Number(item.trim()));
    if (ids.length > 16 || ids.some((id) => !Number.isSafeInteger(id) || id < 1)) {
      throw new Error("이벤트 번호는 양의 정수로 최대 16개 입력하세요.");
    }
    return ids;
  };

  const contributionForm = (title, definitions, submitText, onSubmit) => {
    const details = make("details", "reply-details");
    details.append(make("summary", "", title));
    const form = make("form", "contribution-form");
    const fields = make("div", "reply-fields");
    const inputs = {};
    for (const [name, label, required, multiline] of definitions) {
      const wrapper = make("label", "", `${label}${required ? " *" : ""}`);
      const input = make(multiline ? "textarea" : "input");
      input.name = name;
      input.required = Boolean(required);
      wrapper.append(input);
      fields.append(wrapper);
      inputs[name] = input;
    }
    const submit = make("button", "button primary", submitText);
    submit.type = "submit";
    form.append(fields, make("p", "investigation-note", "human으로 기록합니다. * 필수 입력"), submit);
    form.addEventListener("invalid", () => { details.open = true; }, true);
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      submit.disabled = true;
      try {
        const values = Object.fromEntries(Object.entries(inputs).map(([name, input]) => [name, input.value.trim()]));
        await onSubmit(values);
      } catch (error) {
        showToast(`기록하지 못했습니다: ${error.message}`);
      } finally {
        submit.disabled = false;
      }
    });
    details.append(form);
    return { details, fields, form, inputs, submit };
  };

  const writeContribution = async (path, values) => {
    const item = await request(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(values) });
    state.highWater = Math.max(state.highWater, Number(item.activity_high_water || 0));
    loadApproaches({ reset: true });
    loadThreads({ reset: true });
    return item;
  };

  const loadThreadInvestigations = async (thread, container) => {
    try {
      const page = await request(`/api/runs/${encodedRun}/approaches?${new URLSearchParams({ thread: thread.id, limit: "5" })}`);
      if (!container.isConnected) return;
      if (page.items.length) {
        container.append(make("h2", "section-title", "접근법"));
        for (const item of page.items) container.append(approachCard(item));
        container.append(make("h2", "section-title", "중간 성과"));
        const reports = make("div");
        container.append(reports);
        pagedSection(reports, `/api/runs/${encodedRun}/reports`, { thread: thread.id, limit: "3" }, reportCard,
          "아직 중간 성과가 없습니다. 접근법에서 근거를 포함해 기록할 수 있습니다.", "중간 성과 더 보기")();
      } else {
        const creation = contributionForm("접근법 기록 (선택)", [
          ["hypothesis", "검토할 가설", true, true], ["next_check", "다음 확인", true, true], ["parent_id", "관련 상위 접근법 ID", false, false],
        ], "접근법 기록", async (values) => {
          const item = await writeContribution(`/api/runs/${encodedRun}/approaches`, { ...values, parent_id: values.parent_id || null, thread_id: thread.id });
          await selectInvestigation("approach", item.id);
          showToast("접근법을 기록했습니다.");
        });
        container.append(creation.details);
      }
    } catch (error) {
      if (container.isConnected) container.append(make("p", "investigation-note", `접근법을 불러오지 못했습니다: ${error.message}`));
    }
  };

  const loadApproaches = async ({ reset = false } = {}) => {
    const requestNumber = ++state.approachRequest;
    const more = document.getElementById("approach-more");
    const list = document.getElementById("approach-list");
    more.disabled = true;
    try {
      const query = new URLSearchParams({ q: state.approachQuery, limit: "5" });
      if (!reset && state.approachCursor) query.set("after", state.approachCursor);
      const page = await request(`/api/runs/${encodedRun}/approaches?${query}`);
      if (requestNumber !== state.approachRequest) return;
      if (reset) list.replaceChildren();
      if (!page.items.length && reset) list.append(make("p", "investigation-note", "일치하는 접근법이 없습니다."));
      for (const item of page.items) {
        const link = discussionLink("approach", item.id, item.hypothesis);
        link.className = "coordination-card";
        link.append(make("span", "peer-meta", `참여 ${item.member_count || 0}명 · ${item.thread_title || "논의"}`));
        list.append(link);
      }
      state.approachCursor = page.next_cursor;
      more.hidden = !page.next_cursor;
    } catch (error) {
      showToast(`접근법 목록 오류: ${error.message}`);
    } finally {
      if (requestNumber === state.approachRequest) more.disabled = false;
    }
  };

  const addReportForm = (inner, approach) => {
    const contribution = contributionForm("중간 성과 기록 (선택)", [
      ["summary", "중간 결론", true, true], ["conditions", "적용 조건과 한계", true, true],
      ["open_questions", "남은 질문", false, true], ["source_event_ids", "원본 근거 이벤트 번호 (쉼표로 구분)", true, false],
      ["artifact_id", "참조 결과물 ID", false, false], ["validation_event_ids", "검증 보고 이벤트 번호 (쉼표로 구분)", false, false],
      ["supersedes_report_id", "교체할 본인 보고서 ID", false, false],
    ], "중간 성과 기록", async (values) => {
      const item = await writeContribution(`/api/runs/${encodedRun}/approaches/${encodeURIComponent(approach.id)}/reports`, {
        ...values, source_event_ids: eventIds(values.source_event_ids), validation_event_ids: eventIds(values.validation_event_ids),
        artifact_id: values.artifact_id || null, supersedes_report_id: values.supersedes_report_id || null,
      });
      await selectInvestigation("report", item.id);
      showToast("근거와 함께 중간 성과를 기록했습니다.");
    });
    contribution.fields.append(make("p", "investigation-note", "이벤트 번호는 각각 최대 16개입니다. 원본 근거 중 하나는 이 논의의 이벤트여야 합니다. 검증 보고는 기존 ‘검증 보고’ 답글을 참조하며, 결과물을 지정하면 같은 결과물의 검증이어야 합니다."));
    inner.append(contribution.details);
  };

  const addAdoptionForm = (inner, report) => {
    const contribution = contributionForm("다른 접근법에 가져가기 (선택)", [["application", "해당 접근법에 적용할 방법과 조건", true, true]], "근거와 함께 가져가기", async (values) => {
      const exchange = await writeContribution(`/api/runs/${encodedRun}/reports/${encodeURIComponent(report.id)}/adoptions`, {
        application: values.application, target_approach_id: target.value,
      });
      await selectThread(exchange.thread_id, { updateHistory: true, focusSubjectId: exchange.comment_id });
      showToast("원본 보고서와 적용 이유를 대상 논의에 남겼습니다.");
    });
    const choices = make("div", "approach-choice");
    const search = make("div", "search-form");
    const query = make("input");
    query.type = "search";
    query.placeholder = "대상 접근법 검색";
    query.setAttribute("aria-label", "가져갈 접근법 검색");
    const searchButton = make("button", "button", "찾기");
    searchButton.type = "button";
    search.append(query, searchButton);
    const label = make("label", "", "대상 접근법 *");
    const target = make("select");
    target.required = true;
    target.setAttribute("aria-label", "대상 접근법");
    const placeholder = () => { const option = make("option", "", "대상 접근법 선택"); option.value = ""; return option; };
    target.append(placeholder());
    label.append(target);
    const more = make("button", "button quiet", "대상 더 보기");
    more.type = "button";
    more.hidden = true;
    choices.append(search, label, more);
    contribution.fields.prepend(choices);
    let cursor = null;
    let searchVersion = 0;
    let loaded = false;
    const load = async (reset = false) => {
      const version = ++searchVersion;
      searchButton.disabled = true;
      more.disabled = true;
      try {
        const params = new URLSearchParams({ q: query.value.trim(), limit: "10" });
        if (!reset && cursor) params.set("after", cursor);
        const page = await request(`/api/runs/${encodedRun}/approaches?${params}`);
        if (version !== searchVersion || !contribution.details.isConnected) return;
        if (reset) target.replaceChildren(placeholder());
        for (const item of page.items) {
          if (item.id === report.approach_id) continue;
          const option = make("option", "", item.hypothesis);
          option.value = item.id;
          target.append(option);
        }
        cursor = page.next_cursor;
        more.hidden = !cursor;
        loaded = true;
      } catch (error) {
        showToast(error.message);
      } finally {
        if (version === searchVersion) { searchButton.disabled = false; more.disabled = false; }
      }
    };
    contribution.details.addEventListener("toggle", () => { if (contribution.details.open && !loaded) load(true); });
    searchButton.addEventListener("click", () => load(true));
    more.addEventListener("click", () => load());
    query.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); load(true); } });
    inner.append(contribution.details);
  };

  const selectInvestigation = async (kind, id, { updateHistory = true } = {}) => {
    const requestNumber = ++state.threadRequest;
    state.selected = null;
    state.selectedArtifact = null;
    state.selectedApproach = kind === "approach" ? id : null;
    state.selectedReport = kind === "report" ? id : null;
    state.currentThread = null;
    markSelected();
    if (updateHistory) {
      const next = new URL(window.location.href);
      for (const key of ["thread", "focus", "artifact", "approach", "report"]) next.searchParams.delete(key);
      next.searchParams.set(kind, id);
      window.history.pushState({ [kind]: id }, "", next);
    }
    reader.replaceChildren(make("div", "reader-empty", "논의 정보 불러오는 중…"));
    try {
      const item = await request(`/api/runs/${encodedRun}/${kind === "approach" ? "approaches" : "reports"}/${encodeURIComponent(id)}`);
      if (requestNumber !== state.threadRequest) return;
      const inner = make("article", "reader-inner");
      inner.dataset.testid = `${kind}-reader`;
      inner.append(make("h1", "thread-heading", kind === "approach" ? item.hypothesis : item.summary));
      inner.append(make("p", "investigation-note", `${item.author} · ${timeText(item.created_at)} · ${item.id}`));
      inner.append(discussionLink("thread", item.thread_id, "원래 논의 열기"));
      if (kind === "approach") {
        inner.append(make("h2", "comments-heading", "다음 확인"), make("p", "investigation-note", item.next_check));
        if (item.parent_id) inner.append(discussionLink("approach", item.parent_id, "관련 상위 접근법"));
        inner.append(make("h2", "comments-heading", `자율 참여 ${item.member_count || 0}명`));
        inner.append(make("p", "investigation-note", "참여자는 자신의 초점을 정해 합류합니다."));
        const members = make("div");
        inner.append(members);
        const loadMembers = pagedSection(members, `/api/runs/${encodedRun}/approaches/${encodeURIComponent(id)}/members`, { limit: "10" }, (member) => {
          const row = make("div", "member-row");
          row.append(make("strong", "", member.name || member.agent_name || member.agent_id));
          row.append(make("p", "investigation-note", member.focus || "참여 중"));
          if (member.process_state) row.append(make("span", "investigation-note", member.process_state));
          return row;
        }, "아직 합류한 참여자가 없습니다.", "참여자 더 보기");
        inner.append(make("h2", "comments-heading", "중간 성과"));
        const reports = make("div");
        inner.append(reports);
        const loadReports = pagedSection(reports, `/api/runs/${encodedRun}/reports`, { approach: id, limit: "5" }, reportCard,
          "아직 기록된 중간 성과가 없습니다.", "중간 성과 더 보기");
        addReportForm(inner, item);
        reader.replaceChildren(inner);
        loadMembers();
        loadReports();
      } else {
        inner.append(make("p", "investigation-note", "작성자가 보고한 중간 성과입니다. 원본과 적용 조건을 함께 확인하세요."));
        inner.append(discussionLink("approach", item.approach_id, "이 성과의 접근법"));
        for (const [label, value] of [["적용 조건과 한계", item.conditions], ["남은 질문", item.open_questions || "기록된 질문 없음"]]) {
          inner.append(make("h2", "comments-heading", label), make("p", "investigation-note", value));
        }
        inner.append(make("h2", "comments-heading", "원본 근거"), originalLinks(item.source_events, "원본"));
        if (item.validation_events?.length) inner.append(make("h2", "comments-heading", "작성자의 검증 보고"), originalLinks(item.validation_events, "검증 보고"));
        if (item.artifact_id) {
          const artifact = make("a", "", "참조 결과물 열기");
          artifact.href = `/?run=${encodedRun}&artifact=${encodeURIComponent(item.artifact_id)}`;
          artifact.addEventListener("click", (event) => { event.preventDefault(); selectArtifact(item.artifact_id); });
          inner.append(make("p", ""));
          inner.append(artifact);
        }
        if (item.source_changes?.total_count) {
          inner.append(make("h2", "comments-heading", `근거의 후속 관계 ${item.source_changes.total_count}건`));
          inner.append(make("p", "investigation-note", "원본에 후속 의견이 있습니다. 보고서의 적용 가능성을 다시 확인하세요."));
          inner.append(originalLinks(item.source_changes.items, "후속"));
        }
        if (item.supersedes_report_id) inner.append(discussionLink("report", item.supersedes_report_id, "이전 보고서"));
        if (item.is_superseded) {
          inner.append(make("p", "investigation-note", "후속 보고서가 있습니다."));
          for (const next of item.superseded_by?.items || []) inner.append(discussionLink("report", next.id, next.summary || "후속 보고서 열기"));
        }
        addAdoptionForm(inner, item);
        reader.replaceChildren(inner);
      }
      reader.scrollTop = 0;
    } catch (error) {
      if (requestNumber === state.threadRequest) reader.replaceChildren(make("div", "reader-empty", `논의 정보를 불러오지 못했습니다: ${error.message}`));
    }
  };

  const selectArtifact = async (artifactId, { updateHistory = true } = {}) => {
    const requestNumber = ++state.threadRequest;
    state.selected = null;
    state.selectedArtifact = artifactId;
    state.selectedApproach = null;
    state.selectedReport = null;
    state.currentThread = null;
    markSelected();
    if (updateHistory) {
      const next = new URL(window.location.href);
      next.searchParams.delete("thread");
      next.searchParams.delete("focus");
      next.searchParams.delete("approach");
      next.searchParams.delete("report");
      next.searchParams.set("artifact", artifactId);
      window.history.pushState({ artifact: artifactId }, "", next);
    }
    reader.replaceChildren(make("div", "reader-empty", "결과물 정보 불러오는 중…"));
    try {
      const item = await request(`/api/runs/${encodedRun}/artifacts/${encodeURIComponent(artifactId)}`);
      if (requestNumber !== state.threadRequest) return;
      const inner = make("article", "reader-inner");
      inner.dataset.testid = "artifact-reader";
      const title = item.note ? String(item.note).split("\n")[0].slice(0, 120) : "공유된 변경 사항";
      inner.append(make("h1", "thread-heading", title));
      const metadata = make("div", "post-meta");
      metadata.append(authorChip(item.author));
      metadata.append(document.createTextNode(` · ${timeText(item.created_at)} · ${item.integrated_at ? "적용됨" : "게시됨"}`));
      inner.append(metadata);
      inner.append(make("p", "workspace-path", `${item.id} · 기준 ${item.base_revision}`));
      if (item.note && item.note !== title) inner.append(markdownText("div", "post-body", item.note));
      inner.append(make("h2", "comments-heading", `변경 파일 ${item.file_count}개`));
      const files = make("ul", "artifact-files");
      for (const path of item.files) files.append(make("li", "", path));
      inner.append(files);
      inner.append(make("h2", "comments-heading", "작성자가 보고한 검증"));
      inner.append(markdownText("div", "post-body", item.validation || "별도 검증 결과가 기록되지 않았습니다."));
      reader.replaceChildren(inner);
      reader.scrollTop = 0;
    } catch (error) {
      if (requestNumber === state.threadRequest) {
        reader.replaceChildren(make("div", "reader-empty", `결과물을 불러오지 못했습니다: ${error.message}`));
      }
    }
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
        peers: "none",
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
      renderPeerSummary(data.agent_summary);
      document.getElementById("notification-counts").textContent =
        `알림 대기 ${data.notifications.pending_agents}명 · ${data.notifications.pending_events}건`;
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
    const params = new URL(window.location.href).searchParams;
    if (params.get("artifact")) selectArtifact(params.get("artifact"), { updateHistory: false });
    else if (params.get("approach")) selectInvestigation("approach", params.get("approach"), { updateHistory: false });
    else if (params.get("report")) selectInvestigation("report", params.get("report"), { updateHistory: false });
    else if (params.get("thread")) selectThread(params.get("thread"), { updateHistory: false, focusSubjectId: params.get("focus") });
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
  document.getElementById("peer-search-form").addEventListener("submit", (event) => {
    event.preventDefault();
    state.peerQuery = document.getElementById("peer-search-input").value.trim();
    loadPeers({ reset: true });
  });
  document.getElementById("peer-search-input").addEventListener("search", (event) => {
    if (!event.target.value) {
      state.peerQuery = "";
      loadPeers({ reset: true });
    }
  });
  document.getElementById("peer-load-more").addEventListener("click", () => loadPeers());
  document.getElementById("approach-search-form").addEventListener("submit", (event) => {
    event.preventDefault();
    state.approachQuery = document.getElementById("approach-search-input").value.trim();
    loadApproaches({ reset: true });
  });
  document.getElementById("approach-more").addEventListener("click", () => loadApproaches());
  document.getElementById("peer-refresh").addEventListener("click", () => loadPeers({ reset: true }));
  document.getElementById("tag-all").addEventListener("click", () => {
    tagIntoComposer("@all");
  });

  loadThreads({ reset: true });
  loadApproaches({ reset: true });
  if (state.selected) selectThread(state.selected, { updateHistory: false, focusSubjectId: urlState.searchParams.get("focus") });
  else if (state.selectedArtifact) selectArtifact(state.selectedArtifact, { updateHistory: false });
  else if (state.selectedApproach) selectInvestigation("approach", state.selectedApproach, { updateHistory: false });
  else if (state.selectedReport) selectInvestigation("report", state.selectedReport, { updateHistory: false });
  pollUpdates();
  window.setInterval(pollUpdates, 5000);
  window.setInterval(expireHumanMentions, 30000);
})();
"""
