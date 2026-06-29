"""Front-end profiles: how each agent CLI is pointed at the local server.

Claude Code speaks the Anthropic Messages API; Codex speaks the OpenAI API.
vllm-mlx exposes both from one process, so a profile is just the command to
exec plus the environment overlay (and any default CLI args) that redirect it
to the local base URL.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from .config import Config

type EnvBuilder = Callable[[Config], dict[str, str]]
type ArgsBuilder = Callable[[Config], list[str]]

# A dummy credential. The loopback server requires no auth, but the CLIs refuse
# to start without *some* token, so we hand them an inert one.
_LOCAL_TOKEN = "cairn-local"  # noqa: S105 - inert placeholder; loopback server needs no auth


@dataclass(frozen=True, slots=True)
class Profile:
    name: str
    command: str
    build_env: EnvBuilder
    build_args: ArgsBuilder
    description: str


def _claude_env(config: Config) -> dict[str, str]:
    # ANTHROPIC_AUTH_TOKEN -> "Authorization: Bearer", which is what vllm-mlx
    # accepts; ANTHROPIC_API_KEY (x-api-key) is rejected when a key is set.
    return {
        "ANTHROPIC_BASE_URL": config.base_url,
        "ANTHROPIC_AUTH_TOKEN": _LOCAL_TOKEN,
        "ANTHROPIC_MODEL": config.model,
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": config.model,
        "ANTHROPIC_DEFAULT_SONNET_MODEL": config.model,
    }


def _codex_env(config: Config) -> dict[str, str]:
    return {
        "OPENAI_BASE_URL": f"{config.base_url}/v1",
        "OPENAI_API_KEY": _LOCAL_TOKEN,
    }


def _no_args(_config: Config) -> list[str]:
    return []


def _codex_args(config: Config) -> list[str]:
    # Codex (>= 0.142) loads model providers from config; OPENAI_BASE_URL alone
    # is not enough, and `wire_api = "chat"` was removed in favour of
    # "responses". Inject a "cairn" provider pointing at the local server so
    # `cairn codex` works with no ~/.codex/config.toml edits.
    return [
        "-c", "model_providers.cairn.name=cairn",
        "-c", f"model_providers.cairn.base_url={config.base_url}/v1",
        "-c", "model_providers.cairn.wire_api=responses",
        "-c", "model_providers.cairn.env_key=OPENAI_API_KEY",
        "-c", "model_provider=cairn",
        "-m", config.model,
    ]


_PROFILES: dict[str, Profile] = {
    "claude": Profile(
        name="claude",
        command="claude",
        build_env=_claude_env,
        build_args=_no_args,
        description="Claude Code via the Anthropic Messages API.",
    ),
    "codex": Profile(
        name="codex",
        command="codex",
        build_env=_codex_env,
        build_args=_codex_args,
        description="OpenAI Codex via the OpenAI Responses API.",
    ),
}


def profiles() -> Mapping[str, Profile]:
    return _PROFILES


def get(name: str) -> Profile | None:
    return _PROFILES.get(name)
