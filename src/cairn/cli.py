"""Command-line entry point for cairn.

Management commands (``up``/``down``/``status``/``bench``) are parsed with
argparse. Launch commands (``claude``/``codex``/``run``) instead pass every
remaining argument through to the target CLI verbatim — argparse must not see
them, or it would reject flags such as ``-p`` that belong to the wrapped tool.
"""

import argparse
import os
import shutil
import sys
from collections.abc import Callable, Sequence

from . import __version__, profiles
from .backend import Backend, BackendError
from .bench import run as run_bench
from .config import Config, ConfigError

type Handler = Callable[[Config, argparse.Namespace], int]


def main(argv: Sequence[str] | None = None) -> int:
    tokens = list(sys.argv[1:] if argv is None else argv)
    if tokens:
        head = tokens[0]
        if head in profiles.profiles():
            return _dispatch_launch(head, tokens[1:])
        if head == "run":
            if not tokens[1:]:
                print("cairn: `run` requires a profile name", file=sys.stderr)
                return 2
            return _dispatch_launch(tokens[1], tokens[2:])
    return _dispatch_management(tokens)


# -- management commands (argparse) ----------------------------------------


def _dispatch_management(tokens: list[str]) -> int:
    parser = _build_parser()
    args = parser.parse_args(tokens)
    try:
        config = Config.load()
    except (ConfigError, OSError) as error:
        print(f"cairn: {error}", file=sys.stderr)
        return 2
    handler: Handler = args.handler
    try:
        return handler(config, args)
    except BackendError as error:
        print(f"cairn: {error}", file=sys.stderr)
        return 1


def _build_parser() -> argparse.ArgumentParser:
    launch_lines = "\n".join(
        f"    {p.name:<8} {p.description}" for p in profiles.profiles().values()
    )
    parser = argparse.ArgumentParser(
        prog="cairn",
        description="Run agent CLIs against a local MLX model on Apple Silicon.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "launch commands (all arguments are passed through to the tool):\n"
            f"{launch_lines}\n"
            "    run <profile> [args...]   launch an arbitrary profile\n\n"
            "examples:\n"
            '    cairn claude -p "explain this regex"\n'
            "    cairn codex\n"
        ),
    )
    parser.add_argument("--version", action="version", version=f"cairn {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("up", help="start the local model server and wait until ready").set_defaults(
        handler=_cmd_up
    )
    sub.add_parser("down", help="stop the local model server").set_defaults(handler=_cmd_down)
    sub.add_parser("status", help="show server status").set_defaults(handler=_cmd_status)
    bench = sub.add_parser("bench", help="measure warm generation throughput (tok/s)")
    bench.add_argument("--max-tokens", type=int, default=256)
    bench.set_defaults(handler=_cmd_bench)
    return parser


def _cmd_up(config: Config, _args: argparse.Namespace) -> int:
    backend = Backend(config)
    print(f"cairn: starting {config.backend_command} ({config.model}) on {config.base_url} …")
    backend.ensure_running()
    print("cairn: ready")
    return 0


def _cmd_down(config: Config, _args: argparse.Namespace) -> int:
    stopped = Backend(config).stop()
    print("cairn: stopped" if stopped else "cairn: not running")
    return 0


def _cmd_status(config: Config, _args: argparse.Namespace) -> int:
    status = Backend(config).status()
    state = "healthy" if status.healthy else "running" if status.running else "stopped"
    print(f"state : {state}")
    print(f"pid   : {status.pid if status.pid is not None else '-'}")
    print(f"url   : {status.base_url}")
    print(f"model : {config.model}")
    return 0 if status.healthy else 1


def _cmd_bench(config: Config, args: argparse.Namespace) -> int:
    backend = Backend(config)
    if not backend.is_healthy():
        print("cairn: server is not running; start it with `cairn up`", file=sys.stderr)
        return 1
    result = run_bench(config, max_tokens=int(args.max_tokens))
    print(f"decode        : {result.decode_tps:.1f} tok/s ({result.decode_tokens} tokens)")
    print(f"prefix tokens : {result.prefix_tokens}")
    print(f"prefill cold  : {result.cold_s:.2f}s")
    print(f"prefill warm  : {result.warm_s:.2f}s (prefix cache)")
    print(f"cache speedup : {result.cache_speedup:.1f}x")
    return 0


# -- launch commands (passthrough) -----------------------------------------


def _dispatch_launch(profile_name: str, rest: list[str]) -> int:
    try:
        config = Config.load()
    except (ConfigError, OSError) as error:
        print(f"cairn: {error}", file=sys.stderr)
        return 2
    try:
        return _launch(config, profile_name, _strip_separator(rest))
    except BackendError as error:
        print(f"cairn: {error}", file=sys.stderr)
        return 1


def _launch(config: Config, profile_name: str, rest: list[str]) -> int:
    profile = profiles.get(profile_name)
    if profile is None:
        known = ", ".join(profiles.profiles())
        print(f"cairn: unknown profile {profile_name!r} (known: {known})", file=sys.stderr)
        return 2
    binary = shutil.which(profile.command)
    if binary is None:
        print(f"cairn: {profile.command!r} not found on PATH", file=sys.stderr)
        return 127

    Backend(config).ensure_running()
    env = os.environ | profile.build_env(config)
    os.execvpe(binary, [binary, *rest], env)  # noqa: S606 - replaces the process; never returns


def _strip_separator(rest: list[str]) -> list[str]:
    if rest and rest[0] == "--":
        return rest[1:]
    return rest
