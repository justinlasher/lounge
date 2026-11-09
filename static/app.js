// Lounge web client — vanilla ES module, no build step.
// Sections: api · state · ws · render · events · boot

// ----------------------------------------------------------------------------
// api
// ----------------------------------------------------------------------------
const api = (() => {
  const tokenKey = "lounge_token";
  const getToken = () => localStorage.getItem(tokenKey);
  const setToken = (t) => (t ? localStorage.setItem(tokenKey, t) : localStorage.removeItem(tokenKey));

  async function req(method, path, body) {
    const headers = { "Content-Type": "application/json" };
    const token = getToken();
    if (token) headers["Authorization"] = "Bearer " + token;
    const res = await fetch(path, {
      method,
      headers,
      credentials: "include",
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (res.status === 204) return null;
    let data = null;
    try { data = await res.json(); } catch (_) {}
    if (!res.ok) {
      const err = new Error((data && data.detail) || res.statusText);
      err.status = res.status;
      throw err;
    }
    return data;
  }

  return {
    getToken, setToken,
    get: (p) => req("GET", p),
    post: (p, b) => req("POST", p, b),
    patch: (p, b) => req("PATCH", p, b),
    del: (p, b) => req("DELETE", p, b),
  };
})();

// ----------------------------------------------------------------------------
// state
// ----------------------------------------------------------------------------
const state = {
  me: null,
  workspaces: [],
  wid: null,
  categories: [],
  channels: [],
  members: [],
  channelId: null,
  threadId: null,
  online: new Set(),
  typing: new Map(), // user_id -> timeout handle
};

const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
};
const initials = (name) =>
  (name || "?").trim().split(/\s+/).map((w) => w[0]).slice(0, 2).join("").toUpperCase();
const hhmm = (iso) => {
  const d = new Date(iso);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
};
const userName = (id) => {
  const m = state.members.find((u) => u.id === id);
  return m ? m.display_name || m.username : "user " + id;
};

// ----------------------------------------------------------------------------
// ws (auto-reconnect + exponential backoff)
// ----------------------------------------------------------------------------
const ws = (() => {
  let sock = null;
  let backoff = 1000;
  let typingTimer = null;

  function connect() {
    if (!state.wid) return;
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const url = `${proto}://${location.host}/api/ws?wid=${state.wid}&token=${encodeURIComponent(api.getToken() || "")}`;
    sock = new WebSocket(url);
    sock.onopen = () => { backoff = 1000; };
    sock.onmessage = (e) => handleEvent(JSON.parse(e.data));
    sock.onclose = () => {
      sock = null;
      setTimeout(connect, backoff);
      backoff = Math.min(backoff * 2, 15000);
    };
    sock.onerror = () => { try { sock.close(); } catch (_) {} };
  }

  function reset() {
    if (sock) { sock.onclose = null; try { sock.close(); } catch (_) {} sock = null; }
    backoff = 1000;
    connect();
  }

  function sendTyping(channelId) {
    if (sock && sock.readyState === WebSocket.OPEN) {
      if (typingTimer) return; // throttle
      sock.send(JSON.stringify({ type: "typing.start", channel_id: channelId }));
      typingTimer = setTimeout(() => (typingTimer = null), 3000);
    }
  }

  return { connect, reset, sendTyping };
})();

function handleEvent(evt) {
  switch (evt.type) {
    case "hello":
      state.online = new Set(evt.online);
      renderMembers();
      break;
    case "presence.update":
      if (evt.online) state.online.add(evt.user_id);
      else state.online.delete(evt.user_id);
      renderMembers();
      break;
    case "message.create":
      if (evt.thread_id && evt.thread_id === state.threadId) appendThreadMessage(evt.message);
      else if (!evt.thread_id && evt.channel_id === state.channelId) appendMessage(evt.message);
      break;
    case "message.edit":
      if (evt.channel_id === state.channelId) reloadMessages();
      break;
    case "message.delete":
      if (evt.channel_id === state.channelId) reloadMessages();
      break;
    case "typing.start":
      if (evt.channel_id === state.channelId && evt.user_id !== state.me.id) showTyping(evt.user_id);
      break;
    case "channel.create":
    case "channel.update":
    case "channel.delete":
    case "thread.create":
      loadChannels();
      break;
  }
}

// ----------------------------------------------------------------------------
// render
// ----------------------------------------------------------------------------
function renderWorkspaces() {
  const list = $("workspace-list");
  list.innerHTML = "";
  state.workspaces.forEach((w) => {
    const li = el("li");
    const btn = el("button", "rail-badge" + (w.id === state.wid ? " active" : ""), initials(w.name));
    btn.title = w.name;
    btn.setAttribute("aria-label", w.name);
    btn.onclick = () => selectWorkspace(w.id);
    li.appendChild(btn);
    list.appendChild(li);
  });
}

function renderChannels() {
  const tree = $("channel-tree");
  tree.innerHTML = "";
  const byCat = new Map();
  const uncategorized = [];
  state.channels.forEach((c) => {
    if (c.category_id == null) uncategorized.push(c);
    else {
      if (!byCat.has(c.category_id)) byCat.set(c.category_id, []);
      byCat.get(c.category_id).push(c);
    }
  });

  const renderChannelBtn = (c) => {
    const btn = el("button", "channel-item" + (c.id === state.channelId ? " active" : ""));
    btn.appendChild(el("span", "hash", c.is_private ? "🔒" : "#"));
    btn.appendChild(el("span", "name", c.name));
    if (c.thread_count > 0) btn.appendChild(el("span", "badge", String(c.thread_count)));
    btn.onclick = () => selectChannel(c.id);
    return btn;
  };

  uncategorized.forEach((c) => tree.appendChild(renderChannelBtn(c)));

  state.categories.forEach((cat) => {
    const wrap = el("div", "category");
    const head = el("button", "category-head");
    head.appendChild(el("span", "caret", "▾"));
    head.appendChild(el("span", null, cat.name));
    head.onclick = () => wrap.classList.toggle("collapsed");
    wrap.appendChild(head);
    (byCat.get(cat.id) || []).forEach((c) => wrap.appendChild(renderChannelBtn(c)));
    tree.appendChild(wrap);
  });
}

function renderMembers() {
  const list = $("member-list");
  list.innerHTML = "";
  const sorted = [...state.members].sort((a, b) => {
    const ao = state.online.has(a.id) ? 0 : 1;
    const bo = state.online.has(b.id) ? 0 : 1;
    if (ao !== bo) return ao - bo;
    return (a.display_name || a.username).localeCompare(b.display_name || b.username);
  });
  sorted.forEach((u) => {
    const online = state.online.has(u.id);
    const row = el("li", "member-row " + (online ? "online" : "offline"));
    row.appendChild(el("span", "dot"));
    row.appendChild(el("span", "name", u.display_name || u.username));
    if (u.is_bot) row.appendChild(el("span", "bot-tag", "bot"));
    list.appendChild(row);
  });
}

function messageGroup(msg, prev) {
  const compact = prev && prev.author_id === msg.author_id &&
    new Date(msg.created_at) - new Date(prev.created_at) < 5 * 60 * 1000 && !prev.thread_id === !msg.thread_id;
  const group = el("div", "msg-group" + (compact ? " compact" : ""));
  group.appendChild(el("div", "avatar", initials(userName(msg.author_id))));
  const bodyWrap = el("div", "msg-body");
  if (!compact) {
    const meta = el("div", "msg-meta");
    meta.appendChild(el("span", "msg-author", userName(msg.author_id)));
    meta.appendChild(el("span", "msg-time", hhmm(msg.created_at)));
    bodyWrap.appendChild(meta);
  }
  bodyWrap.appendChild(renderLine(msg));
  group.appendChild(bodyWrap);
  return group;
}

function renderLine(msg) {
  const line = el("div", "msg-line");
  const text = el("div", "msg-text" + (msg.edited_at ? " edited" : ""), msg.content);
  line.appendChild(text);

  if (!msg.thread_id) {
    const tl = el("button", "thread-link", "↳ reply in thread");
    tl.onclick = () => createOrOpenThread(msg);
    line.appendChild(tl);
  }

  const actions = el("div", "msg-actions");
  if (!msg.thread_id) {
    const reply = el("button", null, "Thread");
    reply.setAttribute("aria-label", "Reply in thread");
    reply.onclick = () => createOrOpenThread(msg);
    actions.appendChild(reply);
  }
  if (msg.author_id === state.me.id) {
    const edit = el("button", null, "Edit");
    edit.setAttribute("aria-label", "Edit message");
    edit.onclick = () => editMessage(msg);
    actions.appendChild(edit);
  }
  if (msg.author_id === state.me.id || canModerate()) {
    const del = el("button", null, "Delete");
    del.setAttribute("aria-label", "Delete message");
    del.onclick = () => deleteMessage(msg);
    actions.appendChild(del);
  }
  line.appendChild(actions);
  return line;
}

function renderMessages(messages) {
  const listEl = $("message-list");
  listEl.innerHTML = "";
  listEl._messages = messages;
  let prev = null;
  messages.forEach((m) => {
    listEl.appendChild(messageGroup(m, prev));
    prev = m;
  });
  listEl.scrollTop = listEl.scrollHeight;
}

function appendMessage(msg) {
  const listEl = $("message-list");
  const msgs = listEl._messages || [];
  const prev = msgs[msgs.length - 1];
  msgs.push(msg);
  listEl._messages = msgs;
  const nearBottom = listEl.scrollHeight - listEl.scrollTop - listEl.clientHeight < 120;
  listEl.appendChild(messageGroup(msg, prev));
  if (nearBottom) listEl.scrollTop = listEl.scrollHeight;
}

function canModerate() {
  if (state.me && state.me.is_admin) return true;
  return false; // role perms resolved server-side; client shows own-delete only
}

function showTyping(userId) {
  if (state.typing.has(userId)) clearTimeout(state.typing.get(userId));
  state.typing.set(userId, setTimeout(() => {
    state.typing.delete(userId);
    renderTyping();
  }, 4000));
  renderTyping();
}
function renderTyping() {
  const names = [...state.typing.keys()].map(userName);
  $("typing-indicator").textContent = names.length
    ? names.join(", ") + (names.length === 1 ? " is typing…" : " are typing…")
    : "";
}

// ----------------------------------------------------------------------------
// data loading
// ----------------------------------------------------------------------------
async function loadWorkspaces() {
  state.workspaces = await api.get("/api/workspaces");
  if (!state.wid && state.workspaces.length) state.wid = state.workspaces[0].id;
  renderWorkspaces();
}

async function loadChannels() {
  if (!state.wid) return;
  const data = await api.get(`/api/workspaces/${state.wid}/channels`);
  state.categories = data.categories;
  state.channels = data.channels;
  if (!state.channels.find((c) => c.id === state.channelId)) {
    state.channelId = state.channels.length ? state.channels[0].id : null;
  }
  renderChannels();
  if (state.channelId) await reloadMessages();
}

async function loadMembers() {
  if (!state.wid) return;
  state.members = await api.get(`/api/workspaces/${state.wid}/members`);
  state.online = new Set(state.members.filter((m) => m.online).map((m) => m.id));
  renderMembers();
}

async function reloadMessages() {
  if (!state.channelId) { renderMessages([]); return; }
  const ch = state.channels.find((c) => c.id === state.channelId);
  $("channel-title").textContent = (ch && ch.is_private ? "🔒 " : "# ") + (ch ? ch.name : "");
  const msgs = await api.get(`/api/channels/${state.channelId}/messages?limit=100`);
  msgs.reverse(); // server returns desc; show oldest first
  renderMessages(msgs);
}

// ----------------------------------------------------------------------------
// actions
// ----------------------------------------------------------------------------
async function selectWorkspace(wid) {
  state.wid = wid;
  state.channelId = null;
  closeThread();
  renderWorkspaces();
  await loadChannels();
  await loadMembers();
  ws.reset();
}

async function selectChannel(cid) {
  state.channelId = cid;
  closeThread();
  renderChannels();
  await reloadMessages();
}

async function sendMessage(content) {
  if (!content.trim() || !state.channelId) return;
  await api.post(`/api/channels/${state.channelId}/messages`, { content });
}

async function editMessage(msg) {
  const next = prompt("Edit message", msg.content);
  if (next != null && next !== msg.content) {
    await api.patch(`/api/messages/${msg.id}`, { content: next });
  }
}

async function deleteMessage(msg) {
  if (confirm("Delete this message?")) {
    try { await api.del(`/api/messages/${msg.id}`); }
    catch (e) { alert(e.message); }
  }
}

async function createOrOpenThread(msg) {
  // Look for an existing thread rooted at this message, else create one.
  const threads = await api.get(`/api/channels/${state.channelId}/threads`);
  let thread = threads.find((t) => t.root_message_id === msg.id);
  if (!thread) {
    const title = prompt("Thread title", msg.content.slice(0, 40)) || "Thread";
    thread = await api.post(`/api/channels/${state.channelId}/threads`, {
      name: title, root_message_id: msg.id,
    });
  }
  openThread(thread);
}

async function openThread(thread) {
  state.threadId = thread.id;
  $("thread-title").textContent = thread.title;
  $("thread-panel").hidden = false;
  const msgs = await api.get(`/api/threads/${thread.id}/messages`);
  const panel = $("thread-messages");
  panel.innerHTML = "";
  panel._messages = msgs;
  let prev = null;
  msgs.forEach((m) => { panel.appendChild(messageGroup(m, prev)); prev = m; });
  panel.scrollTop = panel.scrollHeight;
}

function appendThreadMessage(msg) {
  const panel = $("thread-messages");
  const msgs = panel._messages || [];
  const prev = msgs[msgs.length - 1];
  msgs.push(msg);
  panel._messages = msgs;
  panel.appendChild(messageGroup(msg, prev));
  panel.scrollTop = panel.scrollHeight;
}

function closeThread() {
  state.threadId = null;
  $("thread-panel").hidden = true;
}

// ----------------------------------------------------------------------------
// events / wiring
// ----------------------------------------------------------------------------
function wireEvents() {
  const composer = $("composer");
  const input = $("composer-input");
  composer.addEventListener("submit", async (e) => {
    e.preventDefault();
    const content = input.value;
    input.value = "";
    await sendMessage(content);
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); composer.requestSubmit(); }
    else if (state.channelId) ws.sendTyping(state.channelId);
  });

  $("thread-composer").addEventListener("submit", async (e) => {
    e.preventDefault();
    const ti = $("thread-input");
    const content = ti.value;
    ti.value = "";
    if (content.trim() && state.threadId) {
      await api.post(`/api/threads/${state.threadId}/messages`, { content });
    }
  });
  $("thread-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("thread-composer").requestSubmit(); }
  });
  $("thread-close").onclick = closeThread;

  $("member-toggle").onclick = (e) => {
    const drawer = e.target.closest(".member-drawer");
    const collapsed = drawer.getAttribute("data-collapsed") === "true";
    drawer.setAttribute("data-collapsed", String(!collapsed));
    e.currentTarget.setAttribute("aria-expanded", String(collapsed));
  };

  $("add-workspace").onclick = async () => {
    const name = prompt("Workspace name");
    if (name) { const w = await api.post("/api/workspaces", { name }); await loadWorkspaces(); selectWorkspace(w.id); }
  };

  $("new-channel-btn").onclick = async () => {
    const name = prompt("Channel name");
    if (name) {
      try { await api.post(`/api/workspaces/${state.wid}/channels`, { name, kind: "text" }); await loadChannels(); }
      catch (e) { alert(e.message); }
    }
  };

  $("logout-btn").onclick = async () => {
    try { await api.post("/api/auth/logout"); } catch (_) {}
    api.setToken(null);
    location.reload();
  };
}

// ----------------------------------------------------------------------------
// auth views
// ----------------------------------------------------------------------------
function showLogin(setupHint) {
  $("app-view").hidden = true;
  $("login-view").hidden = false;
  const form = $("login-form");
  let setupMode = false;

  if (!document.getElementById("setup-toggle")) {
    const toggle = el("button", "ghost-btn", "First run? Create the admin account");
    toggle.type = "button";
    toggle.id = "setup-toggle";
    toggle.onclick = () => {
      setupMode = !setupMode;
      $("setup-token-field").hidden = !setupMode;
      $("login-sub").textContent = setupMode ? "Create the first admin" : "Sign in to continue";
      form.querySelector(".primary-btn").textContent = setupMode ? "Create admin" : "Sign in";
    };
    form.appendChild(toggle);
  }

  form.onsubmit = async (e) => {
    e.preventDefault();
    $("login-error").textContent = "";
    const username = $("login-username").value;
    const password = $("login-password").value;
    try {
      let res;
      if (setupMode) {
        res = await api.post("/api/setup", {
          username, password, setup_token: $("login-setup-token").value,
        });
      } else {
        res = await api.post("/api/auth/login", { username, password });
      }
      api.setToken(res.token);
      await boot();
    } catch (err) {
      $("login-error").textContent = err.message || "Sign-in failed";
    }
  };
}

// ----------------------------------------------------------------------------
// boot
// ----------------------------------------------------------------------------
async function boot() {
  try {
    state.me = await api.get("/api/users/@me");
  } catch (_) {
    showLogin();
    return;
  }
  $("login-view").hidden = true;
  $("app-view").hidden = false;
  await loadWorkspaces();
  if (state.wid) {
    await loadChannels();
    await loadMembers();
    ws.connect();
  }
}

wireEvents();
boot();
