"""Lifecycle management for the local MLX inference server (vllm-mlx).

cairn shells out to the ``vllm-mlx`` binary rather than importing it, so the
heavy MLX/torch-free inference stack stays out of cairn's own environment. The
server is bound to loopback only, so no API key is required; the agent CLIs we
launch still send a dummy token, which the server ignores.
"""

import os
import shutil
import signal
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import paths
from .config import Config


class BackendError(RuntimeError):
    """Raised when the backend cannot be started or located."""


@dataclass(frozen=True, slots=True)
class ServerStatus:
    running: bool
    pid: int | None
    healthy: bool
    base_url: str


class Backend:
    """Start/stop/inspect a ``vllm-mlx serve`` process for one model."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._pid_file = paths.pid_file()
        self._log_file = paths.log_file()

    # -- introspection -----------------------------------------------------

    def read_pid(self) -> int | None:
        try:
            return int(self._pid_file.read_text().strip())
        except (FileNotFoundError, ValueError):
            return None

    def status(self) -> ServerStatus:
        pid = self.read_pid()
        running = pid is not None and _pid_alive(pid)
        return ServerStatus(
            running=running,
            pid=pid if running else None,
            healthy=self.is_healthy(),
            base_url=self._config.base_url,
        )

    def is_healthy(self, *, timeout: float = 2.0) -> bool:
        request = urllib.request.Request(  # noqa: S310 - fixed loopback URL
            f"{self._config.base_url}/v1/models",
            headers={"Authorization": "Bearer cairn-local"},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
                return 200 <= response.status < 300
        except (urllib.error.URLError, TimeoutError, OSError):
            return False

    # -- lifecycle ---------------------------------------------------------

    def ensure_running(self, *, ready_timeout: float = 600.0) -> ServerStatus:
        """Start the server if needed and block until it answers health checks."""
        if self.is_healthy():
            return self.status()
        self.start()
        self.wait_ready(timeout=ready_timeout)
        return self.status()

    def start(self) -> int:
        if self.is_healthy():
            existing = self.read_pid()
            if existing is not None:
                return existing
        binary = shutil.which(self._config.backend_command)
        if binary is None:
            raise BackendError(
                f"backend command {self._config.backend_command!r} not found on PATH; "
                "install it with: uv tool install vllm-mlx"
            )
        self._log_file.parent.mkdir(parents=True, exist_ok=True)
        log_handle = self._log_file.open("ab")
        try:
            process = subprocess.Popen(  # noqa: S603 - args are config-controlled
                self._serve_argv(binary),
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
        finally:
            log_handle.close()
        self._pid_file.parent.mkdir(parents=True, exist_ok=True)
        self._pid_file.write_text(str(process.pid))
        return process.pid

    def stop(self) -> bool:
        pid = self.read_pid()
        if pid is None or not _pid_alive(pid):
            self._pid_file.unlink(missing_ok=True)
            return False
        os.kill(pid, signal.SIGTERM)
        for _ in range(50):
            if not _pid_alive(pid):
                break
            time.sleep(0.1)
        else:
            os.kill(pid, signal.SIGKILL)
        self._pid_file.unlink(missing_ok=True)
        return True

    def wait_ready(self, *, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.is_healthy():
                return
            time.sleep(2.0)
        raise BackendError(
            f"server did not become healthy within {timeout:.0f}s; "
            f"see log at {self._log_file}"
        )

    # -- internals ---------------------------------------------------------

    def _serve_argv(self, binary: str) -> list[str]:
        cfg = self._config
        argv = [
            binary,
            "serve",
            cfg.model,
            "--host",
            cfg.host,
            "--port",
            str(cfg.port),
            "--continuous-batching",
            "--enable-auto-tool-choice",
            "--tool-call-parser",
            cfg.tool_call_parser,
        ]
        if cfg.enable_prefix_cache:
            argv.append("--enable-prefix-cache")
        argv.extend(cfg.extra_serve_args)
        return argv

    @property
    def log_path(self) -> Path:
        return self._log_file


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
