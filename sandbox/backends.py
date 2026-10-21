"""Pluggable isolation backends for generated agents.

`sandbox.backend` (an admin setting) picks one:

- `subprocess` (default): a child process with a Windows Job Object or POSIX rlimits, a minimal
  environment and a cut network. No extra software needed.
- `docker`: a throwaway container with no network, a read-only root, dropped capabilities, a PID
  limit, memory and CPU caps, and a non-root user. Stronger isolation for hostile-code threat models.

Both run the same `worker_loop.py` with the same `go` handshake, so generated code behaves the same.
A requested backend that is unavailable is an error. It never silently falls back to weaker isolation.
"""

from __future__ import annotations

import shutil
import subprocess
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from harness.config import PROJECT_ROOT
from harness.errors import SandboxError


@dataclass(frozen=True)
class Outcome:
    code: int
    stdout: str
    stderr: str
    timed_out: bool


class SandboxBackend(ABC):
    name: str = ""

    @abstractmethod
    def available(self) -> tuple[bool, str]:
        """`(True, "")` when usable, else `(False, reason)` suitable for an administrator."""

    @abstractmethod
    def run(self, module_path: Path, task: str, *, memory_mb: int, cpu_s: float, wall_s: float) -> Outcome:
        """Run `module_path` against `task` and return when it finishes or the wall clock expires."""


class SubprocessBackend(SandboxBackend):
    name = "subprocess"

    def available(self) -> tuple[bool, str]:
        return True, ""

    def run(self, module_path: Path, task: str, *, memory_mb: int, cpu_s: float, wall_s: float) -> Outcome:
        from sandbox.limits import minimal_env, python_executable, run_limited

        runner = Path(__file__).with_name("worker_loop.py")
        code, out, err, timed_out = run_limited(
            [python_executable(), str(runner), str(module_path), task],
            env=minimal_env(PROJECT_ROOT),
            cwd=str(PROJECT_ROOT),
            memory_mb=memory_mb,
            cpu_s=cpu_s,
            wall_s=wall_s,
        )
        return Outcome(code, out, err, timed_out)


def docker_command(
    *, image: str, name: str, module_path: Path, task: str, memory_mb: int, cpu_s: float, runner: Optional[Path] = None
) -> list[str]:
    """The exact `docker run` argv. Pure, so a test can pin every isolation flag."""
    runner_dir = (runner or Path(__file__).with_name("worker_loop.py")).resolve().parent
    module = module_path.resolve()
    return [
        "docker", "run", "--rm", "-i",
        "--name", name,
        "--network", "none",
        "--read-only",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--user", "65534:65534",
        "--pids-limit", "64",
        "--memory", f"{int(memory_mb)}m",
        "--memory-swap", f"{int(memory_mb)}m",  # equal to --memory: no swap
        "--cpus", "1",  # rate cap; total CPU seconds are capped by the ulimit below
        "--ulimit", f"cpu={max(1, int(cpu_s))}:{max(1, int(cpu_s))}",
        "--tmpfs", "/tmp:rw,noexec,nosuid,size=16m",
        "-e", "ORION_SANDBOX=1",
        "-e", "PYTHONDONTWRITEBYTECODE=1",
        "-e", "PYTHONIOENCODING=utf-8",
        "-v", f"{runner_dir}:/runner:ro",
        "-v", f"{module.parent}:/agent:ro",
        image,
        "python", "/runner/worker_loop.py", f"/agent/{module.name}", task,
    ]


class DockerBackend(SandboxBackend):
    name = "docker"

    def __init__(self, image: str = "python:3.12-slim", *, exec_fn: Optional[Callable[..., Any]] = None) -> None:
        self.image = image
        self._exec = exec_fn or subprocess.run

    def available(self) -> tuple[bool, str]:
        if shutil.which("docker") is None:
            return False, "the docker CLI is not installed or not on PATH"
        try:
            probe = self._exec(["docker", "info", "--format", "{{.ServerVersion}}"], capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError) as exc:
            return False, f"docker is not responding ({type(exc).__name__})"
        if probe.returncode != 0:
            return False, "the docker daemon is not running"
        return True, ""

    def run(self, module_path: Path, task: str, *, memory_mb: int, cpu_s: float, wall_s: float) -> Outcome:
        name = f"orion-sbx-{uuid.uuid4().hex[:12]}"
        cmd = docker_command(
            image=self.image, name=name, module_path=module_path, task=task, memory_mb=memory_mb, cpu_s=cpu_s
        )
        try:
            done = self._exec(cmd, input="go\n", capture_output=True, text=True, timeout=wall_s)
        except subprocess.TimeoutExpired:
            self._kill(name)
            return Outcome(-9, "", "", True)
        except OSError as exc:
            raise SandboxError(f"could not start docker: {type(exc).__name__}") from exc
        # 125-127 are docker's own failures (daemon, image, exec); 137 is OOM-kill. Both are infrastructure.
        if done.returncode in (125, 126, 127):
            raise SandboxError("docker could not run the sandbox container", details=(done.stderr or "")[:400])
        return Outcome(done.returncode, done.stdout or "", done.stderr or "", False)

    def _kill(self, name: str) -> None:
        try:
            self._exec(["docker", "kill", name], capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError):  # already gone, or the daemon died: nothing more to do
            pass


def get_backend(name: Optional[str] = None) -> SandboxBackend:
    """The configured backend, verified usable. Raises `SandboxError` rather than weakening isolation."""
    from harness.settings_store import effective

    chosen = (name or str(effective("sandbox.backend"))).strip().lower()
    if chosen == "subprocess":
        return SubprocessBackend()
    if chosen == "docker":
        backend = DockerBackend(str(effective("sandbox.docker_image")))
        ok, why = backend.available()
        if not ok:
            raise SandboxError(f"sandbox.backend=docker but {why}")
        return backend
    raise SandboxError(f"unknown sandbox backend '{chosen}'")
