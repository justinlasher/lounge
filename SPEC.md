# SPEC — Lounge (self-hosted Discord-style chat platform)

Build "Lounge": a self-hosted web chat platform that replaces Discord for this user.
Servers → categories → channels → threads, roles, bot accounts, real-time messaging,
a Discord-like dark web UI, and a built-in MCP server so AI agents (Hermes) can both
operate the platform and chat in it. Python backend + static vanilla-JS frontend.
Single git repo, local only (no remote).

## Deliverables
- `pyproject.toml` — uv-managed, Python >=3.11.
  Deps: `fastapi`, `uvicorn[standard]`, `sqlalchemy>=2`, `bcrypt`, `pyjwt`, `mcp[cli]`, `httpx`.
  Dev deps: `pytest`, `pytest-asyncio`, `websocket-client` (or use httpx/ws test client).
- `src/lounge/` package:
  - `config.py` — reads/creates project-local `.env` (`LOUNGE_SECRET=` JWT secret; generate
    a new `secrets.token_hex(32)` and write it on first boot if missing). Never print the secret.
  - `db.py` — SQLAlchemy 2 models + `get_session()`. SQLite file `lounge.db` in project root.
  - `auth.py` — bcrypt password hashing, JWT issue/verify (web cookie + Bearer tokens),
    `get_current_user` dependency. Bots authenticate with `LOUNGE-BOT <token>` Bearer scheme.
  - `events.py` — WebSocket connection hub: register/deregister per (workspace_id, user_id),
    `broadcast(ws_id, event)` fan-out, online-presence tracking.
  - `api/` — FastAPI routers: `users.py`, `workspaces.py`, `channels.py`, `messages.py`,
    `threads.py`, `roles.py`, `bots.py`, `ws.py`.
  - `mcp_server.py` — FastMCP stdio server named `"lounge-ops"` (tool list below) that calls
    the REST API via httpx against a configured `LOUNGE_URL` + token from env/args.
  - `__main__.py` — CLI: `lounge serve` (first boot prints a one-time setup token to create the
    admin account), `lounge create-user NAME --admin`, `lounge selfcheck`.
- `static/` — `index.html`, `app.js`, `style.css`, served by FastAPI at `/`.
- `tests/` — pytest; FastAPI `TestClient`/httpx ASGI; no real network.
- `README.md` — what it is, quickstart (uv run lounge serve → open browser), architecture,
  REST + WS protocol summary, MCP registration snippets (Hermes + Claude Code), roadmap.
- `.gitignore` (lounge.db, .env, .venv, __pycache__, node_modules), local git repo + initial commit.

## Data model
- `users`: id, username (unique), password_hash (nullable for bots), display_name, is_bot,
  is_admin, created_at.
- `bot_tokens`: id, user_id, token_hash (sha256), created_at, revoked_at (nullable).
- `workspaces`: id, name, created_at.
- `memberships`: workspace_id, user_id, joined_at.
- `roles`: id, workspace_id, name, color, can_manage_channels, can_moderate, created_at.
- `member_roles`: membership composite (workspace_id, user_id, role_id).
  Built-in roles per workspace: `Admin` (is via users.is_admin), `Member` (default).
- `categories`: id, workspace_id, name, position.
- `channels`: id, workspace_id, category_id (nullable), name, kind (`text` only for v1;
  kind column exists for future voice), is_private (bool), allowed_role_ids (JSON, for private),
  position, created_at.
- `threads`: id, channel_id, root_message_id, title, created_by, created_at, archived (bool).
- `messages`: id, channel_id, thread_id (nullable), author_id, content, edited_at, created_at.
- `watch_hooks`: channel_id + url — when set, every message.create POSTs the message JSON to url.
  (Hermes bridge: point this at a Hermes webhook subscription.)

## REST API (all JSON under `/api`)
Auth/session:
- `POST /api/setup` — first-run only (when zero users exist): body {username, password, setup_token} → admin user.
- `POST /api/auth/login {username, password}` → sets JWT cookie, returns {token, user}.
- `GET /api/users/@me`, `GET /api/users/{id}`.
- Admin: `POST /api/users {username, password, display_name}` (create human user), `GET /api/users`.
Workspaces:
- `GET /api/workspaces`, `POST /api/workspaces {name}`.
- `GET /api/workspaces/{wid}/members`.
Channels & categories:
- `GET /api/workspaces/{wid}/channels`; `POST /api/workspaces/{wid}/categories {name}`;
  `POST /api/workspaces/{wid}/channels {name, kind, category_id, is_private, allowed_role_ids}`.
- `PATCH /api/channels/{id} {name, topic?→no topic in v1, is_private, allowed_role_ids}`;
  `DELETE /api/channels/{id}` (admin; cascades messages via FK).
Messages:
- `GET /api/channels/{id}/messages?before={id}&limit<=100` (desc, thread_id null only);
  `POST /api/channels/{id}/messages {content}` → triggers WS broadcast + watch_hook POST.
- `PATCH /api/messages/{id}` (author only), `DELETE /api/messages/{id}` (author or moderator).
Threads:
- `GET /api/channels/{id}/threads`; `POST /api/channels/{id}/threads {name, root_message_id?}`.
- `GET /api/threads/{id}/messages`; `POST /api/threads/{id}/messages {content}`
  → WS broadcast with `thread_id` set.
Roles:
- `GET/POST /api/workspaces/{wid}/roles`; `PATCH/DELETE /api/roles/{id}`;
  `PUT /api/workspaces/{wid}/members/{user_id}/roles {role_ids}` (replace semantics);
  `POST /api/workspaces/{wid}/members/{user_id}/roles/{role_id}` add; DELETE same path remove.
Bots (the “channels of bots” feature):
- `GET /api/workspaces/{wid}/bots` (members where is_bot).
- `POST /api/workspaces/{wid}/bots {name}` (admin) → creates bot user + membership +
  bot token; returns token ONCE (token shown at creation only).
- `DELETE /api/workspaces/{wid}/bots/{user_id}` → revoke + remove membership.
- `POST /api/workspaces/{wid}/bots/provision {name}` → one orchestrated call:
  create bot user+token, create role named `Agent: <name>`, create category `<name>` (private),
  create private text channel `<slug>` with allowed_role_ids=[new role], assign role to bot,
  membership, and set watch_hook optional `{watch_url}`. Returns everything incl. joining URL info.
  (This mirrors the discord-ops bot_workspace_provision orchestration.)
WS:
- `WS /api/ws?wid={workspace_id}&token={jwt-or-bot-token}`.
- Server → client events (JSON): `hello` (online user list), `message.create|edit|delete`
  (payload includes channel_id, thread_id if any, message), `typing.start {channel_id, user_id}`,
  `presence.update {user_id, online}`, `channel.create|update|delete`, `thread.create`.
- Client → server: `typing.start {channel_id}` only (all state changes go through REST).
Permissions v0 (simple, no per-channel bitfield): users.is_admin → everything;
roles.can_manage_channels → create/update channels/categories; roles.can_moderate →
delete others' messages, manage roles/members; else chat + read non-private channels.
Private channels readable only by admins + roles in allowed_role_ids.

## Web UI (static, vanilla JS — no build step, no frameworks)
Discord-like dark theme, three panes:
1. Workspace rail (left, 72px): workspace initials badges; clicking switches workspace.
2. Channel sidebar: categories with collapse toggles; channels listed; private channels show 🔒;
   click → open channel; active thread count badge; member list drawer (bottom of sidebar,
   collapsible): online users first with green dots, then offline.
3. Chat pane: channel header (name), message list (group consecutive same-author messages,
   avatar initials circle + author name + HH:MM timestamps), inline "replies in thread" link
   on threaded messages, message hover actions (reply-in-thread, edit own, delete own/mod),
   thread panel (slides in on the right for viewing/creating threads), composer with
   typing indicator broadcast, Enter to send.
- Login view when no valid cookie: centered form.
- WS client with auto-reconnect + exponential backoff; on reconnect re-fetch latest messages.
- After creating/using a bot token the UI does not need it; bots are headless clients.
- All rendering from WS events + REST fetches; no polling loops.
- Keep app.js in modular plain JS (IIFE sections or ES modules); readable > clever.
- Accessibility basics: semantic buttons, focus outlines, aria-labels on icon buttons.

## MCP server (`lounge-ops`, FastMCP stdio)
Config: env `LOUNGE_URL` (default http://127.0.0.1:8000) + `LOUNGE_TOKEN` (JWT or bot token).
Tools (all return JSON dicts; destructive ones take `confirm: bool = False` and when False
return `{"would_do": [...]}` with zero REST calls):
- `workspace_overview()` — counts + channel/category listing.
- `channel_list()`, `channel_info(channel_id)`, `channel_create(name, category_id=None)`,
  `channel_update(channel_id, name=None, is_private=None, allowed_role_ids=None)`,
  `channel_delete(channel_id, confirm)`, `category_create(name)`.
- `thread_list(channel_id)`, `thread_create(channel_id, title, root_message_id=None)`.
- `message_send(channel_or_thread_id, content)`, `message_edit(message_id, content)`,
  `message_delete(channel_or_thread_id, message_id, confirm)`.
- `member_list()`, `bots_list()`.
- `bot_create(name)`, `bot_provision(name, watch_url=None)` (the one-call workspace),
  `bot_remove(user_id, confirm)`.
- `role_list()`, `role_create(name, can_manage_channels=False, can_moderate=False)`,
  `role_assign(user_id, role_id)`, `role_remove(user_id, role_id)`, `role_delete(role_id, confirm)`,
  `bot_set_roles(user_id, role_ids)`.
Shared REST client inside mcp_server.py with error mapping (401/403/404/422 → clean messages, never leak tokens).

## Hermes integration (README section "Connecting Hermes")
1. Run `hermes webhook subscribe lounge --filter ...` (document exact command from hermes-agent
   skill docs pattern) to receive watch_hook POSTs in #general-style channels.
2. Register the MCP: `hermes mcp add lounge-ops --command uv --args run --directory <project> python -m lounge_mcp`
   → document the actual correct command from this repo (export a tiny `lounge_mcp` entrypoint:
   `lounge-mcp = "lounge.mcp_server:main"` console script, so the add command is
   `hermes mcp add lounge-ops --command <venv-uv> --args run --directory <dir> -- lounge-mcp`).
3. Flow: Lounge posts message → Hermes webhook → Hermes replies with `message_send` via MCP.

## Tests (pytest, in-process ASGI, no real network)
- Auth: setup flow with setup token, login, @me, bad creds → 401, bot-token auth works on REST.
- Channels/messages CRUD happy path; permissions: non-moderator can't delete others' messages;
  non-admin can't create channels; private channel hidden from unauthorized member.
- Threads: create, reply, listing; thread messages excluded from channel message list.
- WS: two TestClient websocket connections; A sends message via REST; B receives
  `message.create`; typing event relay; presence lists on hello.
- Bots: bot_create shows token once; bot auth works; provision creates role+category+channel+
  assignment in correct order and returns ids; watch_hook POST fires on message send (respx/mock).
- MCP: confirm-gate zero-call tests (as before), error mapping, provision orchestration shape.
- Gates (all must pass): `uv run pytest -q` green; `uv run lounge selfcheck` exits 0
  (spins app in-process: setup admin, workspace, channel, message send, WS echo, MCP
  workspace_overview via direct function call; prints compact OK summary);
  `uv run uvicorn lounge.api.app:app` boots and `curl /api/health` returns {"ok": true}
  (expose `GET /api/health` unauthenticated).

## Explicitly OUT of scope v1 (document in README roadmap)
Voice/video (WebRTC), file/image uploads, reactions/emoji, push notifications, E2E encryption,
federation, invite links (admin creates users; v2), Hermes gateway platform adapter (v2 —
makes Hermes a real "online member"), message search (v2), rate limiting beyond basic.
