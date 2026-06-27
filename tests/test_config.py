from pathlib import Path

import pytest

from cairn.config import DEFAULT_MODEL, DEFAULT_PORT, Config, ConfigError


def test_defaults() -> None:
    config = Config()
    assert config.model == DEFAULT_MODEL
    assert config.port == DEFAULT_PORT
    assert config.base_url == f"http://127.0.0.1:{DEFAULT_PORT}"
    assert config.extra_serve_args == ()


def test_overlay_applies_known_keys() -> None:
    config = Config().overlay(
        {"port": 9000, "model": "m", "extra_serve_args": ["--foo", "bar"]}
    )
    assert config.port == 9000
    assert config.model == "m"
    assert config.extra_serve_args == ("--foo", "bar")
    assert config.base_url == "http://127.0.0.1:9000"


def test_overlay_rejects_unknown_key() -> None:
    with pytest.raises(ConfigError, match="unknown config key"):
        Config().overlay({"nope": 1})


def test_overlay_rejects_wrong_type() -> None:
    with pytest.raises(ConfigError, match="must be an integer"):
        Config().overlay({"port": "8000"})


def test_overlay_rejects_bool_as_int() -> None:
    with pytest.raises(ConfigError, match="must be an integer"):
        Config().overlay({"port": True})


def test_overlay_rejects_non_string_in_array() -> None:
    with pytest.raises(ConfigError, match="only strings"):
        Config().overlay({"extra_serve_args": ["ok", 1]})


def test_load_missing_file_returns_defaults(tmp_path: Path) -> None:
    assert Config.load(tmp_path / "absent.toml") == Config()


def test_load_reads_toml(tmp_path: Path) -> None:
    target = tmp_path / "config.toml"
    target.write_text('port = 1234\nmodel = "local/m"\n')
    config = Config.load(target)
    assert config.port == 1234
    assert config.model == "local/m"


def test_config_is_frozen() -> None:
    config = Config()
    with pytest.raises(AttributeError):
        config.port = 1  # type: ignore[misc]
