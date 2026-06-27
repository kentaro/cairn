"""Front-end profiles: how each agent CLI is pointed at the local server.

Claude Code speaks the Anthropic Messages API; Codex speaks the OpenAI API.
vllm-mlx exposes both from one process, so a profile is just the command to
exec plus the environment overlay that redirects it to the local base URL.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from .config import Config

type EnvBuilder = Callable[[Config], dict[str, str]]

# A dummy credential. The loopback server requires no auth, but the CLIs refuse
# to start without *some* token, so we hand them an inert one.
_LOCAL_TOKEN = "cairn-local"  # noqa: S105 - inert placeholder; loopback server needs no auth


@dataclass(frozen=True, slots=True)
class Profile:
    name: str
    command: str
    build_env: EnvBuilder
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


_PROFILES: dict[str, Profile] = {
    "claude": Profile(
        name="claude",
        command="claude",
        build_env=_claude_env,
        description="Claude Code via the Anthropic Messages API.",
    ),
    "codex": Profile(
        name="codex",
        command="codex",
        build_env=_codex_env,
        description="OpenAI Codex via the OpenAI API.",
    ),
}


def profiles() -> Mapping[str, Profile]:
    return _PROFILES


def get(name: str) -> Profile | None:
    return _PROFILES.get(name)
