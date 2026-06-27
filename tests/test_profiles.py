from cairn import profiles
from cairn.config import Config


def test_known_profiles_present() -> None:
    assert set(profiles.profiles()) == {"claude", "codex"}


def test_claude_env_redirects_to_local_anthropic() -> None:
    config = Config(port=8123)
    profile = profiles.get("claude")
    assert profile is not None
    env = profile.build_env(config)
    assert env["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:8123"
    assert env["ANTHROPIC_MODEL"] == config.model
    # Uses AUTH_TOKEN (Bearer), never API_KEY (x-api-key), which vllm-mlx rejects.
    assert "ANTHROPIC_API_KEY" not in env
    assert env["ANTHROPIC_AUTH_TOKEN"]


def test_codex_env_redirects_to_local_openai() -> None:
    config = Config(port=8123)
    profile = profiles.get("codex")
    assert profile is not None
    env = profile.build_env(config)
    assert env["OPENAI_BASE_URL"] == "http://127.0.0.1:8123/v1"
    assert env["OPENAI_API_KEY"]


def test_unknown_profile_is_none() -> None:
    assert profiles.get("nope") is None
