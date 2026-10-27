"""Paths and settings.

Settings live in the SQLite database (editable from the Settings page).
Environment variables override them, so secrets such as API keys never
have to be stored by the app.
"""
from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "scrollback"
LEGACY_NAME = "annals"  # the app's first name; old installs are moved over
ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT / "static"


def _adopt_legacy_env() -> None:
    """Accept environment variables written for the old name."""
    prefix = LEGACY_NAME.upper() + "_"
    for key, value in list(os.environ.items()):
        if key.startswith(prefix):
            os.environ.setdefault("SCROLLBACK_" + key[len(prefix):], value)


_adopt_legacy_env()


def data_dir() -> Path:
    base = os.environ.get("SCROLLBACK_DATA_DIR")
    if base:
        path = Path(base).expanduser()
    else:
        xdg = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
        path = Path(xdg) / APP_NAME
        legacy = Path(xdg) / LEGACY_NAME
        if not path.exists() and legacy.is_dir():
            try:
                legacy.rename(path)
            except OSError:
                path = legacy
    path.mkdir(parents=True, exist_ok=True)
    return path


def db_path() -> Path:
    folder = data_dir()
    path = folder / "scrollback.db"
    legacy = folder / f"{LEGACY_NAME}.db"
    if not path.exists() and legacy.exists():
        for suffix in ("", "-wal", "-shm"):
            old = Path(str(legacy) + suffix)
            if old.exists():
                old.rename(Path(str(path) + suffix))
    return path


# Defaults for settings stored in the DB. Values are strings in the DB and
# converted on read by `get_setting` in db.py.
DEFAULT_SETTINGS = {
    "daily_goal": "20",            # posts read per day to keep the streak
    "language": "en",              # Wikipedia language edition
    "include_people": "0",         # also ingest births and deaths
    "quiz_every": "7",             # one quiz card every N posts
    "llm_provider": "none",        # none | anthropic | openai
    "llm_model": "",
    "llm_base_url": "",
    "contact": "",                 # added to the User-Agent sent to Wikimedia
    "tts_voice": "",               # Piper voice, such as en_GB-cori-medium
    "tts_speed": "1.0",            # 0.6 to 1.6, 1.0 is the voice's normal pace
}

# Environment overrides: setting key -> env var
ENV_OVERRIDES = {
    "llm_provider": "SCROLLBACK_LLM_PROVIDER",
    "llm_model": "SCROLLBACK_LLM_MODEL",
    "llm_base_url": "SCROLLBACK_LLM_BASE_URL",
    "language": "SCROLLBACK_LANGUAGE",
    "contact": "SCROLLBACK_CONTACT",
}


def llm_api_key(provider: str) -> str:
    key = os.environ.get("SCROLLBACK_LLM_API_KEY", "")
    if key:
        return key
    if provider == "anthropic":
        return os.environ.get("ANTHROPIC_API_KEY", "")
    if provider == "openai":
        return os.environ.get("OPENAI_API_KEY", "")
    return ""
