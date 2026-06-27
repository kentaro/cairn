"""Typed configuration model and validating TOML loader."""

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Self

from . import paths

DEFAULT_MODEL = "mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
DEFAULT_TOOL_PARSER = "qwen3_coder"
DEFAULT_BACKEND_COMMAND = "vllm-mlx"


class ConfigError(ValueError):
    """Raised when ``config.toml`` contains a key of the wrong type."""


@dataclass(frozen=True, slots=True, kw_only=True)
class Config:
    """Resolved cairn configuration.

    Immutable; construct defaults with ``Config()`` or load+merge a TOML file
    with :meth:`load`.
    """

    model: str = DEFAULT_MODEL
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    tool_call_parser: str = DEFAULT_TOOL_PARSER
    backend_command: str = DEFAULT_BACKEND_COMMAND
    enable_prefix_cache: bool = True
    extra_serve_args: tuple[str, ...] = ()

    @property
    def base_url(self) -> str:
        """OpenAI/Anthropic-compatible base URL of the local server."""
        return f"http://{self.host}:{self.port}"

    @classmethod
    def load(cls, path: Path | None = None) -> Self:
        """Load defaults, overlaying any keys present in ``config.toml``."""
        target = path if path is not None else paths.config_file()
        if not target.is_file():
            return cls()
        with target.open("rb") as fp:
            raw: dict[str, object] = tomllib.load(fp)
        return cls().overlay(raw, source=target)

    def overlay(self, raw: Mapping[str, object], *, source: Path | None = None) -> Self:
        """Return a copy with recognised keys from ``raw`` applied, validated."""
        where = f" in {source}" if source is not None else ""
        changes: dict[str, object] = {}
        for key, value in raw.items():
            match key:
                case "model" | "host" | "tool_call_parser" | "backend_command":
                    changes[key] = _expect_str(key, value, where)
                case "port":
                    changes[key] = _expect_int(key, value, where)
                case "enable_prefix_cache":
                    changes[key] = _expect_bool(key, value, where)
                case "extra_serve_args":
                    changes[key] = _expect_str_tuple(key, value, where)
                case _:
                    raise ConfigError(f"unknown config key {key!r}{where}")
        return replace(self, **changes)


def _expect_str(key: str, value: object, where: str) -> str:
    if not isinstance(value, str):
        raise ConfigError(f"{key}{where} must be a string, got {type(value).__name__}")
    return value


def _expect_int(key: str, value: object, where: str) -> int:
    # bool is a subclass of int; reject it explicitly for clarity.
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{key}{where} must be an integer, got {type(value).__name__}")
    return value


def _expect_bool(key: str, value: object, where: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigError(f"{key}{where} must be a boolean, got {type(value).__name__}")
    return value


def _expect_str_tuple(key: str, value: object, where: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ConfigError(f"{key}{where} must be an array of strings")
    items: list[str] = []
    for element in value:  # pyright: ignore[reportUnknownVariableType]
        if not isinstance(element, str):
            raise ConfigError(f"{key}{where} must contain only strings")
        items.append(element)
    return tuple(items)
