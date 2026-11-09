# Lounge

A **self-hosted, Discord-style chat platform** you can run on your own box.
Servers → categories → channels → threads, roles, bot accounts, real-time
messaging over WebSockets, a dark web UI, and a **built-in MCP server** so AI
agents (e.g. *Hermes*) can both operate the platform and chat in it.

Python backend (FastAPI + SQLAlchemy 2 + SQLite) · static vanilla-JS frontend ·
no build step · single local git repo.

---

## Quickstart

```bash
uv sync                      # install deps (incl. dev tools)
uv run lounge serve          # first boot prints a one-time SETUP TOKEN
# → open http://127.0.0.1:8000/  and click "First run? Create the admin account"
#   enter a username/password + the setup token to create the admin.
```

Everything after that happens in the UI: create a workspace (the `+` on the left
rail), add channels, invite bots, chat.

Other commands:

```bash
uv run lounge create-user alice --admin        # create a user from the CLI
uv run lounge selfcheck                         # in-process end-to-end smoke test
uv run uvicorn lounge.api.app:app               # raw ASGI boot (exposes /api/health)
```

On first boot a project-local `.env` is created holding `LOUNGE_SECRET` (the JWT
signing key, a fresh `secrets.token_hex(32)`). It is **never printed**. The
SQLite database lives at `./lounge.db`.

---

## Architecture

```
src/lounge/
  config.py        reads/creates .env (LOUNGE_SECRET); DB path
  db.py            SQLAlchemy 2 models + engine/session factory (SQLite)
  auth.py          bcrypt hashing, JWT issue/verify, bot-token auth, permissions
  events.py        WebSocket hub (presence + fan-out) + watch-hook delivery
  api/
    app.py         FastAPI app: routers + static mount + /api/health
    users.py       setup / login / users
    workspaces.py  workspaces + membership
    channels.py    categories + channels
    messages.py    channel messages (+ watch-hook trigger)
    threads.py     threads + threaded messages
    roles.py       roles + member-role assignment
    bots.py        bot accounts + one-call provisioning
    ws.py          the /api/ws WebSocket endpoint
  mcp_server.py    FastMCP stdio server "lounge-ops" (REST client inside)
  __main__.py      CLI: serve / create-user / selfcheck
static/            index.html, app.js, style.css (the web UI)
tests/             pytest, in-process ASGI, no real network
```

**Data model** (SQLite): `users`, `bot_tokens`, `workspaces`, `memberships`,
`roles`, `member_roles`, `categories`, `channels`, `threads`, `messages`,
`watch_hooks`. Foreign keys cascade (deleting a channel removes its messages,
threads, and watch hook).

**Permissions (v0, no per-channel bitfield):**
- `users.is_admin` → everything.
- `roles.can_manage_channels` → create/update channels & categories.
- `roles.can_moderate` → delete others' messages, manage roles/members.
- otherwise → chat + read non-private channels.
- Private channels are readable only by admins and members holding a role in the
  channel's `allowed_role_ids`.

---

## REST API (JSON under `/api`)

Auth / session
- `POST /api/setup {username, password, setup_token}` — first run only.
- `POST /api/auth/login {username, password}` → `{token, user}` (+ cookie).
- `POST /api/auth/logout`
- `GET /api/users/@me`, `GET /api/users/{id}`
- `POST /api/users {username, password, display_name}` (admin), `GET /api/users` (admin)

Workspaces / members
- `GET /api/workspaces`, `POST /api/workspaces {name}`
- `GET /api/workspaces/{wid}/members`

Channels / categories
- `GET /api/workspaces/{wid}/channels`
- `POST /api/workspaces/{wid}/categories {name}`
- `POST /api/workspaces/{wid}/channels {name, kind, category_id, is_private, allowed_role_ids}`
- `PATCH /api/channels/{id}`, `DELETE /api/channels/{id}` (admin; cascades)

Messages
- `GET /api/channels/{id}/messages?before={id}&limit<=100` (desc; top-level only)
- `POST /api/channels/{id}/messages {content}` (WS broadcast + watch-hook)
- `PATCH /api/messages/{id}` (author), `DELETE /api/messages/{id}` (author or moderator)

Threads
- `GET /api/channels/{id}/threads`, `POST /api/channels/{id}/threads {name, root_message_id?}`
- `GET /api/threads/{id}/messages`, `POST /api/threads/{id}/messages {content}`

Roles / members
- `GET|POST /api/workspaces/{wid}/roles`, `PATCH|DELETE /api/roles/{id}`
- `PUT /api/workspaces/{wid}/members/{uid}/roles {role_ids}` (replace)
- `POST|DELETE /api/workspaces/{wid}/members/{uid}/roles/{rid}` (add / remove)

Bots
- `GET /api/workspaces/{wid}/bots`
- `POST /api/workspaces/{wid}/bots {name}` (admin) → returns **token once**
- `DELETE /api/workspaces/{wid}/bots/{uid}` (revoke + remove membership)
- `POST /api/workspaces/{wid}/bots/provision {name, watch_url?}` → one call that
  creates bot+token, role `Agent: <name>`, category `<name>`, private channel
  `<slug>` scoped to the role, assigns the role, and (optionally) sets a watch hook.

Health
- `GET /api/health` → `{"ok": true}` (unauthenticated).

### WebSocket protocol

Connect: `WS /api/ws?wid={workspace_id}&token={jwt-or-bot-token}`

Server → client events (JSON `{"type": ...}`):
- `hello` — `{online: [user_id...], user_id, workspace_id}`
- `message.create` / `message.edit` / `message.delete` — include `channel_id`,
  `thread_id` (nullable), and the `message` (or `message_id` for delete)
- `typing.start` — `{channel_id, user_id}`
- `presence.update` — `{user_id, online}`
- `channel.create` / `channel.update` / `channel.delete`
- `thread.create`

Client → server: `typing.start {channel_id}` only. **All state changes go
through REST.**

Authentication: `Authorization: Bearer <jwt>` for humans (also a `lounge_token`
cookie), `Authorization: LOUNGE-BOT <token>` for bots.

---

## Web UI

Static, framework-free (`static/`). Three panes — workspace rail, channel
sidebar (categories with collapse toggles, 🔒 private channels, thread-count
badges, a collapsible member drawer with online-first green dots), and the chat
pane (grouped consecutive messages, avatar initials, `HH:MM` timestamps, hover
actions for reply-in-thread / edit / delete, a sliding thread panel, and a
composer that broadcasts typing and sends on Enter). The WS client
auto-reconnects with exponential backoff and re-fetches messages on reconnect.

---

## MCP server (`lounge-ops`)

A FastMCP **stdio** server that drives the REST API via httpx.

Configuration (env):
- `LOUNGE_URL` — base URL (default `http://127.0.0.1:8000`)
- `LOUNGE_TOKEN` — JWT or bot token used for all calls
- `LOUNGE_WORKSPACE_ID` — optional; otherwise the first visible workspace is used

Tools: `workspace_overview`, `channel_list`, `channel_info`, `channel_create`,
`channel_update`, `channel_delete*`, `category_create`, `thread_list`,
`thread_create`, `message_send`, `message_edit`, `message_delete*`,
`member_list`, `bots_list`, `bot_create`, `bot_provision`, `bot_remove*`,
`role_list`, `role_create`, `role_assign`, `role_remove`, `role_delete*`,
`bot_set_roles`. Tools marked `*` are destructive and take `confirm: bool = False`
— when `False` they return `{"would_do": [...]}` and make **zero** REST calls.
Errors (401/403/404/422) are mapped to clean messages and never leak tokens.

Run it directly:

```bash
LOUNGE_URL=http://127.0.0.1:8000 LOUNGE_TOKEN=<bot-or-jwt> uv run lounge-mcp
```

---

## Connecting Hermes

1. **Receive messages** — point a channel's watch hook at a Hermes webhook so
   every `message.create` is POSTed to Hermes:

   ```bash
   hermes webhook subscribe lounge --filter 'event=message.create'
   # use the subscription URL as the channel watch hook, e.g. via:
   #   POST /api/workspaces/{wid}/bots/provision {"name":"Hermes","watch_url":"<sub-url>"}
   ```

2. **Register the MCP** so Hermes can act back in Lounge:

   ```bash
   hermes mcp add lounge-ops \
     --command "$(command -v uv)" \
     --args run --directory "$(pwd)" -- lounge-mcp
   ```

   The same pattern works for Claude Code:

   ```bash
   claude mcp add lounge-ops \
     --env LOUNGE_URL=http://127.0.0.1:8000 \
     --env LOUNGE_TOKEN=<bot-token> \
     -- "$(command -v uv)" run --directory "$(pwd)" -- lounge-mcp
   ```

3. **Flow:** Lounge posts a message → watch hook → Hermes webhook → Hermes
   replies with `message_send` via the MCP → it appears in the channel.

---

## Gates

```bash
uv sync
uv run pytest -q
uv run lounge selfcheck
uv run uvicorn lounge.api.app:app    # then: curl /api/health → {"ok": true}
```

---

## Roadmap (out of scope for v1)

Voice/video (WebRTC), file/image uploads, reactions/emoji, push notifications,
E2E encryption, federation, invite links (admin creates users for now), a
Hermes gateway adapter that makes Hermes a real "online member", message search,
and rate limiting beyond the basics. The `channels.kind` column already exists
to make room for future voice channels.
