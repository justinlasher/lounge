"""Project-local configuration.

Reads/creates a project-local ``.env`` that stores the JWT signing secret.
A fresh ``secrets.token_hex(32)`` is generated and written on first boot when
``LOUNGE_SECRET`` is not already set.  The secret is never printed.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

# src/lounge/config.py -> parents[2] == project root (where pyproject.toml lives).
PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = Path(os.environ.get("LOUNGE_ENV_FILE", PROJECT_ROOT / ".env"))
DB_PATH = Path(os.environ.get("LOUNGE_DB_PATH", PROJECT_ROOT / "lounge.db"))

DEFAULT_URL = "http://127.0.0.1:8000"


def _read_env_file(path: Path) -> dict[str, str]:
    data: dict[str, str] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            data[key.strip()] = value.strip()
    return data


def _set_env_value(path: Path, key: str, value: str) -> None:
    """Idempotently write ``key=value`` into the .env file."""
    lines: list[str] = []
    found = False
    if path.exists():
        for line in path.read_text().splitlines():
            if line.strip().startswith(f"{key}="):
                lines.append(f"{key}={value}")
                found = True
            else:
                lines.append(line)
    if not found:
        lines.append(f"{key}={value}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def get_secret() -> str:
    """Return the JWT signing secret, generating + persisting one if missing.

    The resulting value is cached into ``os.environ['LOUNGE_SECRET']`` but is
    never printed anywhere.
    """
    secret = os.environ.get("LOUNGE_SECRET")
    if secret:
        return secret
    env = _read_env_file(ENV_PATH)
    secret = env.get("LOUNGE_SECRET")
    if not secret:
        secret = secrets.token_hex(32)
        _set_env_value(ENV_PATH, "LOUNGE_SECRET", secret)
    os.environ["LOUNGE_SECRET"] = secret
    return secret


def database_url() -> str:
    """SQLAlchemy URL for the SQLite database (project root ``lounge.db``)."""
    override = os.environ.get("LOUNGE_DB_URL")
    if override:
        return override
    return f"sqlite:///{DB_PATH}"
