"""Lounge command-line interface.

Commands:
  lounge serve                      run the web server (first boot prints a
                                    one-time setup token to create the admin)
  lounge create-user NAME --admin   create a user directly in the database
  lounge selfcheck                  in-process end-to-end smoke test (exit 0 OK)
"""

from __future__ import annotations

import argparse
import os
import secrets
import sys
import tempfile

from . import auth, config, db


def _init_runtime() -> None:
    config.get_secret()
    if db.engine is None:
        db.init_engine()


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    _init_runtime()
    from .db import User
    from sqlalchemy import func, select

    with db.new_session() as session:
        user_count = session.execute(
            select(func.count()).select_from(User)
        ).scalar_one()

    if user_count == 0:
        token = secrets.token_hex(16)
        os.environ["LOUNGE_SETUP_TOKEN"] = token
        print("=" * 60)
        print("  Lounge first-boot setup")
        print("  No users exist yet. Create the admin account with this")
        print("  one-time setup token:")
        print()
        print(f"      SETUP TOKEN: {token}")
        print()
        print(f"  Open http://{args.host}:{args.port}/ and complete setup,")
        print("  or POST /api/setup {username, password, setup_token}.")
        print("=" * 60)

    uvicorn.run(
        "lounge.api.app:app", host=args.host, port=args.port, reload=False
    )
    return 0


def cmd_create_user(args: argparse.Namespace) -> int:
    _init_runtime()
    from .db import User
    from sqlalchemy import select

    with db.new_session() as session:
        existing = session.execute(
            select(User).where(User.username == args.name)
        ).scalar_one_or_none()
        if existing is not None:
            print(f"error: user '{args.name}' already exists", file=sys.stderr)
            return 1
        password = args.password or secrets.token_urlsafe(12)
        user = User(
            username=args.name,
            password_hash=auth.hash_password(password),
            display_name=args.name,
            is_bot=False,
            is_admin=args.admin,
        )
        session.add(user)
        session.commit()
        print(f"Created user '{args.name}' (id={user.id}, admin={args.admin}).")
        if not args.password:
            print(f"Generated password: {password}")
    return 0


def cmd_selfcheck(_args: argparse.Namespace) -> int:
    # Isolated temp DB + ephemeral secret (never touches the real .env/db).
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    os.environ["LOUNGE_DB_URL"] = f"sqlite:///{tmp.name}"
    os.environ["LOUNGE_SECRET"] = secrets.token_hex(32)
    os.environ["LOUNGE_SETUP_TOKEN"] = "selfcheck-token"

    db.init_engine(os.environ["LOUNGE_DB_URL"])

    from fastapi.testclient import TestClient

    from . import mcp_server
    from .api.app import app

    oks: list[str] = []
    with TestClient(app) as client:
        # health
        assert client.get("/api/health").json() == {"ok": True}
        oks.append("health")

        # setup admin
        r = client.post(
            "/api/setup",
            json={"username": "admin", "password": "pw", "setup_token": "selfcheck-token"},
        )
        assert r.status_code == 200, r.text
        token = r.json()["token"]
        hdr = {"Authorization": f"Bearer {token}"}
        oks.append("setup-admin")

        # workspace + channel
        wid = client.post("/api/workspaces", json={"name": "HQ"}, headers=hdr).json()["id"]
        cid = client.post(
            f"/api/workspaces/{wid}/channels",
            json={"name": "general", "kind": "text"},
            headers=hdr,
        ).json()["id"]
        oks.append("workspace+channel")

        # WS echo: connect, post a message via REST, receive message.create
        with client.websocket_connect(f"/api/ws?wid={wid}&token={token}") as ws:
            assert ws.receive_json()["type"] == "hello"
            client.post(
                f"/api/channels/{cid}/messages",
                json={"content": "hello lounge"},
                headers=hdr,
            )
            evt = None
            for _ in range(10):  # skip own presence.update etc.
                frame = ws.receive_json()
                if frame["type"] == "message.create":
                    evt = frame
                    break
            assert evt is not None, "no message.create received"
            assert evt["message"]["content"] == "hello lounge"
        oks.append("ws-echo")

        # MCP workspace_overview via direct function call (in-process client)
        mcp_client = mcp_server.LoungeClient(
            token=token, workspace_id=wid, client=client
        )
        overview = mcp_server._workspace_overview(mcp_client)
        assert overview["counts"]["channels"] >= 1, overview
        oks.append("mcp-overview")

    print("SELFCHECK OK — " + ", ".join(oks))
    try:
        os.unlink(tmp.name)
    except OSError:
        pass
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lounge", description="Lounge CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p_serve = sub.add_parser("serve", help="run the web server")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8000)
    p_serve.set_defaults(func=cmd_serve)

    p_user = sub.add_parser("create-user", help="create a user")
    p_user.add_argument("name")
    p_user.add_argument("--admin", action="store_true")
    p_user.add_argument("--password", default=None)
    p_user.set_defaults(func=cmd_create_user)

    p_check = sub.add_parser("selfcheck", help="in-process smoke test")
    p_check.set_defaults(func=cmd_selfcheck)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
