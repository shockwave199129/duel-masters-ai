"""
Database configuration helpers.
"""

import os
from pathlib import Path


DEFAULT_ENV_PATH = Path(__file__).resolve().parents[2] / ".env"


def load_env_file(env_path: Path = DEFAULT_ENV_PATH) -> None:
    """Load simple KEY=VALUE pairs from a local .env file."""
    if not env_path.exists():
        return

    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        os.environ.setdefault(key, value)


def get_database_url(cli_dsn: str | None = None) -> str | None:
    """Return the CLI DSN when provided, otherwise DATABASE_URL from .env/env."""
    load_env_file()
    return cli_dsn or os.getenv("DATABASE_URL")
