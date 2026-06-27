"""Benchmark the local server: decode throughput and prefix-cache reuse.

Two numbers that actually matter for agent workloads on Apple Silicon:

* **decode** — warm generation throughput (tok/s). Fast here.
* **prefix cache** — sending a large shared prefix a second time should be near
  instant if the server reuses the cached KV. This is what makes multi-turn /
  repeated-template workloads usable; it only helps when *you* keep the prompt
  prefix byte-stable. See ``docs/DESIGN.md``.
"""

import json
import time
import urllib.request
import uuid
from dataclasses import dataclass
from typing import cast

from .config import Config

_DECODE_PROMPT = "Write a Python function fib(n) returning the nth Fibonacci number. Code only."
# A stable ~4K-token block used to exercise the prefix cache.
_BLOCK = (
    "You are a coding assistant with tools to read, write, and run files. "
    "Follow instructions precisely and never fabricate file contents. "
) * 60


@dataclass(frozen=True, slots=True)
class BenchResult:
    decode_tokens: int
    decode_s: float
    prefix_tokens: int
    cold_s: float
    warm_s: float

    @property
    def decode_tps(self) -> float:
        return self.decode_tokens / self.decode_s if self.decode_s > 0 else 0.0

    @property
    def cache_speedup(self) -> float:
        return self.cold_s / self.warm_s if self.warm_s > 0 else 0.0


def run(config: Config, *, max_tokens: int = 256) -> BenchResult:
    # 1) decode throughput on a small prompt.
    _call(config, "hi", max_tokens=8)  # warm the model
    decode_elapsed, _pin, decode_out = _call(config, _DECODE_PROMPT, max_tokens=max_tokens)

    # 2) prefix-cache reuse: unique nonce forces a cold first pass, then an
    #    identical second pass should hit the cache.
    prefix = f"[{uuid.uuid4()}]\n{_BLOCK}"
    cold_elapsed, prefix_tokens, _ = _call(config, prefix, max_tokens=8)
    warm_elapsed, _, _ = _call(config, prefix, max_tokens=8)

    return BenchResult(
        decode_tokens=decode_out,
        decode_s=decode_elapsed,
        prefix_tokens=prefix_tokens,
        cold_s=cold_elapsed,
        warm_s=warm_elapsed,
    )


def _call(config: Config, content: str, *, max_tokens: int) -> tuple[float, int, int]:
    body = json.dumps(
        {
            "model": config.model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": content}],
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
    start = time.monotonic()
    with urllib.request.urlopen(request, timeout=600) as response:  # noqa: S310
        decoded: object = json.loads(response.read())
    elapsed = time.monotonic() - start
    if not isinstance(decoded, dict):
        raise BenchError(f"unexpected response shape: {type(decoded).__name__}")
    usage = cast("dict[str, object]", decoded).get("usage")
    return elapsed, _int(usage, "input_tokens"), _int(usage, "output_tokens")


def _int(usage: object, key: str) -> int:
    if isinstance(usage, dict):
        value = cast("dict[str, object]", usage).get(key)
        if isinstance(value, int):
            return value
    return 0


class BenchError(RuntimeError):
    """Raised when the benchmark response cannot be interpreted."""
