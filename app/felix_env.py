"""Load the Felix bot's .env file into os.environ (read-only).

The gift fetchers live in ~/workspace/felix-bot and read their key
(PORTALS_PARTNERS_KEY) from the environment at import time. This
loader reads the same .env file Felix itself uses, so the API service
never needs its own copy of the key.

Security: values are NEVER logged or printed. Keys already present
in the environment win over the file.
"""
import os
from pathlib import Path

from . import config


def load_felix_env(env_file: Path | None = None) -> list[str]:
    """Read KEY=VALUE lines from the Felix .env into os.environ.

    Returns the list of keys that were set (names only, never values).
    Missing file or unreadable lines are skipped silently — the
    fetchers fail open (Partners calls return None) when the key
    is absent, exactly as the bot behaves.
    """
    path = env_file or config.FELIX_ENV_FILE
    loaded: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return loaded
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ and value:
            os.environ[key] = value
            loaded.append(key)
    return loaded
