import os

import pytest

from cairn import cli
from cairn.backend import Backend


def _which(command: str) -> str:
    return f"/usr/bin/{command}"


def _noop_ensure(_self: Backend, **_kwargs: object) -> None:
    return None


def _capture_launch(
    monkeypatch: pytest.MonkeyPatch, argv: list[str]
) -> tuple[list[str], dict[str, str]]:
    recorded_argv: list[str] = []
    recorded_env: dict[str, str] = {}

    def fake_exec(_binary: str, exec_argv: list[str], env: dict[str, str]) -> None:
        recorded_argv[:] = exec_argv
        recorded_env.update(env)
        raise SystemExit(0)

    monkeypatch.setattr(os, "execvpe", fake_exec)
    monkeypatch.setattr(cli.shutil, "which", _which)
    monkeypatch.setattr(Backend, "ensure_running", _noop_ensure)

    with pytest.raises(SystemExit):
        cli.main(argv)
    return recorded_argv, recorded_env


def test_launch_passes_flags_through(monkeypatch: pytest.MonkeyPatch) -> None:
    argv, env = _capture_launch(
        monkeypatch, ["claude", "-p", "hello", "--dangerously-skip-permissions"]
    )
    assert argv == ["/usr/bin/claude", "-p", "hello", "--dangerously-skip-permissions"]
    assert env["ANTHROPIC_BASE_URL"].startswith("http://127.0.0.1")


def test_launch_strips_double_dash(monkeypatch: pytest.MonkeyPatch) -> None:
    argv, _env = _capture_launch(monkeypatch, ["claude", "--", "-p", "hi"])
    assert argv == ["/usr/bin/claude", "-p", "hi"]


def test_run_routes_to_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    argv, env = _capture_launch(monkeypatch, ["run", "claude", "--foo"])
    assert argv == ["/usr/bin/claude", "--foo"]  # claude injects no default args
    assert "ANTHROPIC_BASE_URL" in env


def test_codex_injects_provider_args(monkeypatch: pytest.MonkeyPatch) -> None:
    argv, env = _capture_launch(monkeypatch, ["codex", "exec", "hi"])
    assert argv[0] == "/usr/bin/codex"
    # cairn provider override + local model are injected before the user's args
    assert "model_provider=cairn" in argv
    assert "model_providers.cairn.wire_api=responses" in argv
    assert argv[-2:] == ["exec", "hi"]
    assert "OPENAI_BASE_URL" in env


def test_run_requires_profile_name(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["run"]) == 2
    assert "requires a profile" in capsys.readouterr().err


def test_unknown_profile_via_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Backend, "ensure_running", _noop_ensure)
    assert cli.main(["run", "bogus"]) == 2
