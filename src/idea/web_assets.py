from __future__ import annotations


CSS = r"""
:root { color-scheme: dark; --bg:#0b1118; --panel:#111a24; --line:#2a3747; --text:#e7edf5; --muted:#9aa9bb; --accent:#86efac; --blue:#93c5fd; }
* { box-sizing:border-box; }
body { margin:0; min-height:100vh; background:var(--bg); color:var(--text); font:14px/1.55 Inter,ui-sans-serif,system-ui,sans-serif; }
button,input,textarea { font:inherit; }
button { cursor:pointer; }
.topbar { display:flex; justify-content:space-between; align-items:center; gap:20px; padding:16px 24px; border-bottom:1px solid var(--line); background:#0e1620; }
.brand { margin:0; font-size:18px; letter-spacing:.04em; }
.goal { max-width:70vw; overflow:hidden; color:var(--muted); text-overflow:ellipsis; white-space:nowrap; }
.logout { padding:6px 10px; border:1px solid var(--line); border-radius:6px; color:var(--muted); background:transparent; }
.board { display:grid; grid-template-columns:300px minmax(0,1fr); min-height:calc(100vh - 74px); }
.sidebar { padding:18px; border-right:1px solid var(--line); background:#0e1620; }
.sidebar h2,.feed h2 { margin:0; font-size:15px; }
.workspace { margin:6px 0 18px; overflow-wrap:anywhere; color:var(--muted); font:11px/1.5 ui-monospace,monospace; }
.search { display:flex; gap:8px; margin:14px 0; }
input,textarea { width:100%; padding:9px 10px; border:1px solid var(--line); border-radius:7px; background:#090e14; color:var(--text); }
textarea { min-height:110px; resize:vertical; }
.button { padding:8px 10px; border:1px solid #37615a; border-radius:7px; background:#143229; color:var(--accent); }
.button.secondary { border-color:#365a7d; background:#102840; color:var(--blue); }
.composer { display:grid; gap:9px; margin-top:22px; }
.composer summary { cursor:pointer; color:var(--blue); }
.composer form { display:grid; gap:9px; margin-top:10px; }
.run-list { margin-top:24px; }
.run-link { display:block; margin:5px 0; overflow:hidden; color:var(--muted); font-size:12px; text-overflow:ellipsis; white-space:nowrap; }
.feed { min-width:0; display:grid; grid-template-columns:minmax(270px,360px) minmax(0,1fr); }
.posts { border-right:1px solid var(--line); overflow:auto; }
.posts-header { padding:18px; border-bottom:1px solid var(--line); }
.post-list { padding:8px; }
.post { width:100%; margin:0 0 7px; padding:12px; border:1px solid transparent; border-radius:8px; background:transparent; color:var(--text); text-align:left; }
.post:hover,.post.active { border-color:var(--line); background:var(--panel); }
.post-title { overflow:hidden; font-weight:700; text-overflow:ellipsis; white-space:nowrap; }
.post-meta,.preview,.empty { margin-top:4px; color:var(--muted); font-size:12px; }
.preview { display:-webkit-box; overflow:hidden; -webkit-box-orient:vertical; -webkit-line-clamp:2; }
.reader { min-width:0; overflow:auto; padding:28px; }
.reader h1 { margin:0 0 6px; font-size:22px; }
.reader-meta { margin-bottom:22px; color:var(--muted); font-size:12px; }
.body { white-space:pre-wrap; overflow-wrap:anywhere; }
.reply { margin-top:28px; padding-top:22px; border-top:1px solid var(--line); }
.comment { margin:18px 0; padding:14px; border-left:2px solid #365a7d; background:#0e1620; }
.comment-meta { margin-bottom:7px; color:var(--muted); font-size:12px; }
.status { min-height:1.5em; margin-top:8px; color:var(--muted); font-size:12px; }
@media (max-width:800px) { .board,.feed { display:block; } .sidebar,.posts { border-right:0; border-bottom:1px solid var(--line); } .posts { max-height:42vh; } .goal { max-width:52vw; } }
"""


JAVASCRIPT = r"""
(() => {
  const board = document.querySelector('[data-board]');
  if (!board) return;
  const runId = board.dataset.runId;
  const encodedRun = encodeURIComponent(runId);
  const list = document.querySelector('#post-list');
  const reader = document.querySelector('#reader');
  const search = document.querySelector('#search');
  let selected = null;

  const text = (tag, value, className = '') => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    node.textContent = value || '';
    return node;
  };
  const request = async (path, options = {}) => {
    const response = await fetch(path, { headers: {'Content-Type':'application/json'}, ...options });
    if (!response.ok) throw new Error((await response.json().catch(() => ({}))).error || '요청에 실패했습니다.');
    return response.json();
  };
  const loadPosts = async (query = '') => {
    list.replaceChildren(text('div', '게시물을 불러오는 중…', 'empty'));
    const params = new URLSearchParams({limit:'50'});
    if (query) params.set('q', query);
    try {
      const page = await request(`/api/runs/${encodedRun}/threads?${params}`);
      list.replaceChildren();
      if (!page.items.length) list.append(text('div', '게시물이 없습니다.', 'empty'));
      for (const item of page.items) {
        const button = document.createElement('button');
        button.type = 'button'; button.className = `post${item.id === selected ? ' active' : ''}`;
        button.append(text('div', item.title, 'post-title'));
        button.append(text('div', `${item.author} · 답글 ${item.comment_count}`, 'post-meta'));
        button.append(text('div', item.preview, 'preview'));
        button.addEventListener('click', () => selectPost(item.id));
        list.append(button);
      }
    } catch (error) { list.replaceChildren(text('div', error.message, 'empty')); }
  };
  const selectPost = async (threadId) => {
    selected = threadId;
    reader.replaceChildren(text('div', '게시물을 불러오는 중…', 'empty'));
    try {
      const thread = await request(`/api/threads/${encodeURIComponent(threadId)}`);
      const content = document.createElement('article');
      content.append(text('h1', thread.title));
      content.append(text('div', `${thread.author} · ${thread.created_at}`, 'reader-meta'));
      content.append(text('div', thread.body, 'body'));
      const comments = document.createElement('section');
      comments.append(text('h2', `답글 ${thread.comments.length}`));
      for (const comment of thread.comments) {
        const node = document.createElement('article'); node.className = 'comment';
        node.append(text('div', `${comment.author} · ${comment.created_at}`, 'comment-meta'));
        node.append(text('div', comment.body, 'body'));
        comments.append(node);
      }
      const reply = document.createElement('form'); reply.className = 'reply';
      reply.append(text('h2', '답글 작성'));
      const author = document.createElement('input'); author.name = 'author'; author.value = 'human'; author.placeholder = '작성자';
      const body = document.createElement('textarea'); body.name = 'body'; body.placeholder = '추가할 정보…'; body.required = true;
      const submit = text('button', '답글 게시', 'button secondary'); submit.type = 'submit';
      const status = text('div', '', 'status');
      reply.append(author, body, submit, status);
      reply.addEventListener('submit', async (event) => {
        event.preventDefault(); submit.disabled = true; status.textContent = '';
        try {
          await request(`/api/threads/${encodeURIComponent(thread.id)}/comments`, {method:'POST', body:JSON.stringify({author:author.value || 'human', body:body.value})});
          await selectPost(thread.id); await loadPosts(search.value.trim());
        } catch (error) { status.textContent = error.message; submit.disabled = false; }
      });
      content.append(comments, reply);
      reader.replaceChildren(content);
      await loadPosts(search.value.trim());
    } catch (error) { reader.replaceChildren(text('div', error.message, 'empty')); }
  };
  document.querySelector('#search-form').addEventListener('submit', (event) => { event.preventDefault(); loadPosts(search.value.trim()); });
  document.querySelector('#new-thread-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const form = event.currentTarget; const submit = form.querySelector('button'); const status = form.querySelector('.status');
    submit.disabled = true; status.textContent = '';
    try {
      const item = await request(`/api/runs/${encodedRun}/threads`, {method:'POST', body:JSON.stringify({author:form.author.value || 'human', title:form.title.value, body:form.body.value})});
      form.reset(); form.author.value = 'human'; await selectPost(item.id);
    } catch (error) { status.textContent = error.message; submit.disabled = false; }
  });
  loadPosts();
})();
"""
