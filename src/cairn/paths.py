"""XDG base-directory resolution for cairn's config, state, and logs."""

import os
from pathlib import Path

_APP = "cairn"


def _xdg(env: str, fallback: Path) -> Path:
    raw = os.environ.get(env)
    base = Path(raw) if raw else fallback
    return base / _APP


def config_dir() -> Path:
    """Directory holding ``config.toml`` (``$XDG_CONFIG_HOME/cairn``)."""
    return _xdg("XDG_CONFIG_HOME", Path.home() / ".config")


def state_dir() -> Path:
    """Directory holding runtime state such as the PID file and server log."""
    return _xdg("XDG_STATE_HOME", Path.home() / ".local" / "state")


def config_file() -> Path:
    return config_dir() / "config.toml"


def pid_file() -> Path:
    return state_dir() / "server.pid"


def log_file() -> Path:
    return state_dir() / "server.log"
