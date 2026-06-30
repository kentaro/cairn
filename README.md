# cairn

Run agent CLIs — Claude Code, Codex, … — against a **local MLX model** on Apple
Silicon. No API spend, no cloud round-trip.

A *cairn* is a stack of stones that marks a path. This one is a small local
waypoint: it stands up an MLX-backed inference server that speaks **both** the
OpenAI and Anthropic wire protocols, then launches your agent CLI pointed at it.

```
$ cairn up                 # start the local model server, wait until ready
$ cairn claude             # launch Claude Code on the local model
$ cairn codex              # launch Codex on the local model
$ cairn bench              # measure warm generation throughput
$ cairn status             # is it up? which model? which port?
$ cairn down               # stop the server
```

## Why

Claude Code speaks the Anthropic Messages API; most local servers speak only
OpenAI. cairn uses [`vllm-mlx`](https://github.com/waybarrios/vllm-mlx), which
exposes **both** from one process, so no translation proxy is needed — and your
Claude **subscription token never passes through any third-party tool** (it
stays with the official `claude`). Use plain `claude` for real work; use `cairn`
for throwaway / offline / cheap tasks on a local model.

See [`docs/DESIGN.md`](docs/DESIGN.md) for the design rationale, and
[`docs/BENCHMARK.md`](docs/BENCHMARK.md) for **measured** accuracy/speed and the
analysis (incl. why full-local Claude Code is prefill-bound while Codex is not).

## Requirements

- Apple Silicon Mac (tested on M4 Max / 64GB)
- Python 3.12+
- The backend, installed separately:
  ```
  uv tool install vllm-mlx
  ```

## Install

```
uv tool install cairn
# …or bundle the backend into cairn's environment:
uv tool install "cairn[backend]"
```

## Configuration

Optional `~/.config/cairn/config.toml`:

```toml
model = "mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit"
host = "127.0.0.1"
port = 8000
tool_call_parser = "qwen3_coder"
enable_prefix_cache = true
# extra_serve_args = ["--kv-cache-quantization"]
```

## Honest performance note

On an M4 Max, the model and server are fast where it counts: **decode** ~90 tok/s
for this MoE model, and the **prefix cache** reuses a shared prompt prefix across
requests (measured 36–50× speedup, incl. partial prefixes).

What actually makes or breaks a local agent is whether the **client keeps its
prompt prefix byte-stable** — that's what lets the cache skip re-prefilling the
big head every turn. Measured, same model and task:

| | Claude Code | Codex CLI |
| --- | --- | --- |
| prompt / turn | ~45K tok | ~16K tok |
| re-prefill on turn 2 | **45,190 tok** (cache busts) | **96 tok** (99% hit) |
| feel | minutes/turn | near-instant after turn 1 |

Claude Code injects dynamic context into its prompt head (it's co-designed with
Anthropic's server-side prompt caching), so full-local CC re-prefills ~45K every
turn. **Codex keeps its prefix stable, so `cairn codex` is genuinely practical
on a local model.** Use plain `claude` for hard work; use `cairn codex` (or your
own prompt-stable apps/scripts, batch jobs, RAG) for local. Details in
[`docs/BENCHMARK.md`](docs/BENCHMARK.md).

Run `cairn bench` to see decode tok/s and the cold→warm prefix-cache speedup on
your machine.

## License

MIT
