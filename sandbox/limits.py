"""OS-enforced resource limits for the generated-code subprocess.

- Windows: a Job Object with a per-process memory cap, a per-process user-time cap, and
  kill-on-close, so the child cannot outlive the harness.
- POSIX: `resource.setrlimit` for address space and CPU seconds, applied in the child before exec.

The child blocks on a `go` line on stdin before it runs any generated code (see `worker_loop.py`), so
the job assignment always happens before user code starts.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

#: Environment the sandboxed child is allowed to see. Nothing else is inherited, so no secrets.
_ENV_ALLOWLIST = ("SYSTEMROOT", "SystemDrive", "PATHEXT", "TEMP", "TMP", "LANG", "LC_ALL")


def minimal_env(project_root: Path) -> dict[str, str]:
    env = {k: os.environ[k] for k in _ENV_ALLOWLIST if k in os.environ}
    env.update(
        {
            "ORION_SANDBOX": "1",
            "PYTHONPATH": str(project_root),
            "PYTHONIOENCODING": "utf-8",
            "PYTHONDONTWRITEBYTECODE": "1",
            "HTTP_PROXY": "http://127.0.0.1:9",  # a dead proxy, as a second wall behind the socket stub
            "HTTPS_PROXY": "http://127.0.0.1:9",
            "NO_PROXY": "",
        }
    )
    return env


def spawn_limited(cmd: list[str], *, env: dict[str, str], cwd: str, memory_mb: int, cpu_s: float) -> subprocess.Popen:
    """Start `cmd` under the limits. The caller must write `go\\n` to stdin to let it proceed."""
    if os.name == "nt":
        return _spawn_windows(cmd, env=env, cwd=cwd, memory_mb=memory_mb, cpu_s=cpu_s)
    return _spawn_posix(cmd, env=env, cwd=cwd, memory_mb=memory_mb, cpu_s=cpu_s)


def _spawn_posix(cmd, *, env, cwd, memory_mb, cpu_s) -> subprocess.Popen:  # pragma: no cover - exercised on Linux
    import resource

    def apply_limits() -> None:
        limit = memory_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
        resource.setrlimit(resource.RLIMIT_CPU, (int(cpu_s), int(cpu_s) + 1))
        os.setsid()

    return subprocess.Popen(
        cmd, env=env, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, preexec_fn=apply_limits,
    )


def _spawn_windows(cmd, *, env, cwd, memory_mb, cpu_s) -> subprocess.Popen:
    proc = subprocess.Popen(
        cmd, env=env, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        job = _job_for(proc.pid, memory_mb, cpu_s)
    except Exception:
        proc.kill()
        raise
    proc._orion_job = job  # type: ignore[attr-defined]  # closing this handle (or exiting) kills the child
    return proc


def _job_for(pid: int, memory_mb: int, cpu_s: float):
    try:
        import win32api
        import win32con
        import win32job
    except ImportError as exc:
        raise RuntimeError("pywin32 is required for sandbox resource limits on Windows (pip install pywin32)") from exc
    job = win32job.CreateJobObject(None, "")
    info = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
    basic = info["BasicLimitInformation"]
    basic["PerProcessUserTimeLimit"] = int(cpu_s * 10_000_000)  # 100 ns units
    basic["LimitFlags"] = (
        win32job.JOB_OBJECT_LIMIT_PROCESS_MEMORY
        | win32job.JOB_OBJECT_LIMIT_PROCESS_TIME
        | win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        | win32job.JOB_OBJECT_LIMIT_ACTIVE_PROCESS
    )
    basic["ActiveProcessLimit"] = 1  # no child processes
    info["ProcessMemoryLimit"] = memory_mb * 1024 * 1024
    win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, info)
    handle = win32api.OpenProcess(win32con.PROCESS_SET_QUOTA | win32con.PROCESS_TERMINATE, False, pid)
    try:
        win32job.AssignProcessToJobObject(job, handle)
    finally:
        win32api.CloseHandle(handle)
    return job


def run_limited(
    cmd: list[str], *, env: dict[str, str], cwd: str, memory_mb: int, cpu_s: float, wall_s: float, stdin_text: str = "go\n"
) -> tuple[int, str, str, bool]:
    """Run to completion. Returns `(returncode, stdout, stderr, timed_out)`; the child is always reaped."""
    proc = spawn_limited(cmd, env=env, cwd=cwd, memory_mb=memory_mb, cpu_s=cpu_s)
    try:
        out, err = proc.communicate(input=stdin_text, timeout=wall_s)
        return proc.returncode, out or "", err or "", False
    except subprocess.TimeoutExpired:
        proc.kill()
        out, err = proc.communicate()
        return -9, out or "", err or "", True
    finally:
        job = getattr(proc, "_orion_job", None)
        if job is not None:
            try:
                job.Close()
            except Exception:  # the process is already gone; a failed handle close changes nothing
                pass


def python_executable() -> str:
    return sys.executable
