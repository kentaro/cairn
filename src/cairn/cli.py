"""Command-line entry point for cairn."""

import argparse
import os
import shutil
import sys
from collections.abc import Callable, Sequence
from typing import cast

from . import __version__, profiles
from .backend import Backend, BackendError
from .bench import run as run_bench
from .config import Config, ConfigError

type Handler = Callable[[Config, argparse.Namespace], int]


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
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


# -- argument parser -------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cairn",
        description="Run agent CLIs against a local MLX model on Apple Silicon.",
    )
    parser.add_argument("--version", action="version", version=f"cairn {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    up = sub.add_parser("up", help="start the local model server and wait until ready")
    up.set_defaults(handler=_cmd_up)

    down = sub.add_parser("down", help="stop the local model server")
    down.set_defaults(handler=_cmd_down)

    status = sub.add_parser("status", help="show server status")
    status.set_defaults(handler=_cmd_status)

    bench = sub.add_parser("bench", help="measure warm generation throughput (tok/s)")
    bench.add_argument("--max-tokens", type=int, default=256)
    bench.set_defaults(handler=_cmd_bench)

    for name, profile in profiles.profiles().items():
        launch = sub.add_parser(name, help=profile.description, add_help=False)
        launch.add_argument("rest", nargs=argparse.REMAINDER)
        launch.set_defaults(handler=_make_launcher(name))

    run = sub.add_parser("run", help="launch an arbitrary profile: cairn run <profile> -- ...")
    run.add_argument("profile")
    run.add_argument("rest", nargs=argparse.REMAINDER)
    run.set_defaults(handler=_cmd_run)

    return parser


# -- command handlers ------------------------------------------------------


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
    max_tokens = int(args.max_tokens)
    result = run_bench(config, max_tokens=max_tokens)
    print(f"prompt tokens : {result.prompt_tokens}")
    print(f"output tokens : {result.output_tokens}")
    print(f"elapsed       : {result.elapsed_s:.2f}s")
    print(f"throughput    : {result.tokens_per_second:.1f} tok/s")
    return 0


def _cmd_run(config: Config, args: argparse.Namespace) -> int:
    profile_name = str(args.profile)
    return _launch(config, profile_name, _strip_separator(args.rest))


def _make_launcher(profile_name: str) -> Handler:
    def handler(config: Config, args: argparse.Namespace) -> int:
        return _launch(config, profile_name, _strip_separator(args.rest))

    return handler


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
    argv = [binary, *rest]
    os.execvpe(binary, argv, env)  # noqa: S606 - replaces the process; never returns


def _strip_separator(rest: object) -> list[str]:
    if not isinstance(rest, list):
        return []
    items = [str(item) for item in cast("list[object]", rest)]
    if items and items[0] == "--":
        return items[1:]
    return items
