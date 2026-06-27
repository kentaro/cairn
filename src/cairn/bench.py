"""A minimal generation-throughput benchmark against the local server.

This measures *warm generation* tok/s only. It is deliberately not a proxy for
end-to-end agent latency: real agent turns are dominated by prefill of a large
system + tool-definition prompt (tens of thousands of tokens), which this short
probe does not exercise. See ``docs/DESIGN.md``.
"""

import json
import time
import urllib.request
from dataclasses import dataclass
from typing import cast

from .config import Config

_WARMUP_PROMPT = "hi"
_BENCH_PROMPT = (
    "Write a Python function fib(n) that returns the nth Fibonacci number "
    "iteratively. Reply with only the code."
)


@dataclass(frozen=True, slots=True)
class BenchResult:
    prompt_tokens: int
    output_tokens: int
    elapsed_s: float

    @property
    def tokens_per_second(self) -> float:
        return self.output_tokens / self.elapsed_s if self.elapsed_s > 0 else 0.0


def run(config: Config, *, max_tokens: int = 256) -> BenchResult:
    _request(config, _WARMUP_PROMPT, max_tokens=8)  # warm the model / cache
    start = time.monotonic()
    payload = _request(config, _BENCH_PROMPT, max_tokens=max_tokens)
    elapsed = time.monotonic() - start
    usage = payload.get("usage", {})
    return BenchResult(
        prompt_tokens=_int(usage, "input_tokens"),
        output_tokens=_int(usage, "output_tokens"),
        elapsed_s=elapsed,
    )


def _request(config: Config, prompt: str, *, max_tokens: int) -> dict[str, object]:
    body = json.dumps(
        {
            "model": config.model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
    ).encode()
    request = urllib.request.Request(  # noqa: S310 - fixed loopback URL
        f"{config.base_url}/v1/messages",
        data=body,
        headers={
            "content-type": "application/json",
            "authorization": "Bearer cairn-local",
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=300) as response:  # noqa: S310
        decoded: object = json.loads(response.read())
    if not isinstance(decoded, dict):
        raise BenchError(f"unexpected response shape: {type(decoded).__name__}")
    return cast("dict[str, object]", decoded)


def _int(usage: object, key: str) -> int:
    if isinstance(usage, dict):
        value = cast("dict[str, object]", usage).get(key)
        if isinstance(value, int):
            return value
    return 0


class BenchError(RuntimeError):
    """Raised when the benchmark response cannot be interpreted."""
