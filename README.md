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

See [`docs/DESIGN.md`](docs/DESIGN.md) for the full rationale and **measured**
performance (incl. why full-local Claude Code is prefill-bound).

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

On an M4 Max, warm generation is fast (~91 tok/s for this MoE model). But a real
Claude Code turn re-prefills a ~44K-token prompt (system + ~98 tool definitions)
every step, so each turn takes minutes. **Full-local Claude Code is great for
experiments and short one-shots, not for heavy day-to-day agentic work.** Details
and numbers in [`docs/DESIGN.md`](docs/DESIGN.md).

## License

MIT
