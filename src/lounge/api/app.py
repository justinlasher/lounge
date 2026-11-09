"""FastAPI application assembly."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .. import config, db
from . import bots, channels, messages, roles, threads, users, workspaces, ws

STATIC_DIR = config.PROJECT_ROOT / "static"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    config.get_secret()
    if db.engine is None:
        db.init_engine()
    yield


app = FastAPI(title="Lounge", version="0.1.0", lifespan=lifespan)


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


# API routers (registered before the static mount so /api wins).
app.include_router(users.router)
app.include_router(workspaces.router)
app.include_router(channels.router)
app.include_router(messages.router)
app.include_router(threads.router)
app.include_router(roles.router)
app.include_router(bots.router)
app.include_router(ws.router)

# Static web UI served at "/".
if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
