"""Generated code runs under OS limits, a minimal environment and no network; one policy guards it."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from harness.config import PROJECT_ROOT
from harness.errors import SandboxError
from sandbox import worker_loop
from sandbox.exec_sandbox import run_generated_agent
from sandbox.gate import static_gate
from sandbox.limits import minimal_env, python_executable, run_limited
from sandbox.policy import WORKER_IMPORTS, check_source


class AgentFileCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._state = patch.dict(os.environ, {"ORION_STATE_DB": str(Path(self._tmp.name) / "s.db")})
        self._state.start()

    def tearDown(self) -> None:
        self._state.stop()
        self._tmp.cleanup()

    def agent(self, body: str) -> Path:
        path = Path(self._tmp.name) / "agent.py"
        path.write_text(textwrap.dedent(body), encoding="utf-8")
        return path


class RunnerTests(AgentFileCase):
    def test_a_normal_agent_runs_and_returns_text(self) -> None:
        path = self.agent('def answer(task):\n    return "hello " + task\n')
        self.assertEqual(run_generated_agent(path, "world"), "hello world")

    def test_structured_results_are_json(self) -> None:
        path = self.agent('def answer(task):\n    return {"n": len(task)}\n')
        self.assertEqual(json.loads(run_generated_agent(path, "abcd")), {"n": 4})

    def test_a_memory_bomb_is_stopped_by_the_os_limit(self) -> None:
        path = self.agent('def answer(task):\n    blob = "a" * (3 * 1024 ** 3)\n    return str(len(blob))\n')
        with self.assertRaises(SandboxError) as err:
            run_generated_agent(path, "x", memory_mb=128)
        self.assertIn("crashed", str(err.exception))
        self.assertIn("memory", (err.exception.details or "").lower())

    def test_a_cpu_spin_is_killed_well_before_the_wall_clock(self) -> None:
        path = self.agent("def answer(task):\n    n = 0\n    while True:\n        n += 1\n")
        started = time.monotonic()
        with self.assertRaises(SandboxError):
            run_generated_agent(path, "x", timeout_s=30, cpu_s=1)
        self.assertLess(time.monotonic() - started, 12, "the CPU limit should fire long before the 30s wall clock")

    def test_the_wall_clock_is_the_smaller_of_the_card_and_the_setting(self) -> None:
        from harness import settings_store as ss

        ss.set_value("sandbox.wall_s", 1, "test")
        try:
            path = self.agent("def answer(task):\n    n = 0\n    while True:\n        n += 1\n")
            with self.assertRaises(SandboxError) as err:
                run_generated_agent(path, "x", timeout_s=60, cpu_s=60)
            self.assertIn("timed out after 1s", str(err.exception))
        finally:
            ss.clear_cache()

    def test_a_crash_is_a_typed_sandbox_error_not_an_exception_in_the_harness(self) -> None:
        path = self.agent('def answer(task):\n    return 1 / 0\n')
        with self.assertRaises(SandboxError) as err:
            run_generated_agent(path, "x")
        self.assertIn("division by zero", err.exception.details or "")

    def test_a_file_that_fails_the_gate_never_runs(self) -> None:
        marker = Path(self._tmp.name) / "ran.txt"
        path = self.agent(f'import os\nopen(r"{marker}", "w").write("x")\ndef answer(task):\n    return "x"\n')
        with self.assertRaises(SandboxError) as err:
            run_generated_agent(path, "x")
        self.assertIn("static gate", str(err.exception))
        self.assertFalse(marker.exists())

    def test_only_allow_listed_builtins_exist_at_runtime(self) -> None:
        path = self.agent('def answer(task):\n    return str(sorted(["b", "a"]))\n')
        self.assertEqual(run_generated_agent(path, "x"), "['a', 'b']")
        # `open` is rejected by the gate first; reaching a missing builtin at runtime fails safely too.
        runtime_only = self.agent('def answer(task):\n    f = len\n    return str(f("abc")) + str(type(1))\n')
        with self.assertRaises(SandboxError):
            run_generated_agent(runtime_only, "x")

    def test_the_builtins_allow_list_is_pinned(self) -> None:
        self.assertEqual(
            set(worker_loop.SAFE_BUILTIN_NAMES),
            {"abs", "min", "max", "sum", "len", "range", "enumerate", "list", "dict", "str", "int", "float",
             "bool", "print", "round", "sorted", "set", "tuple", "zip", "any", "all", "map", "filter", "isinstance"},
        )
        for dangerous in ("open", "eval", "exec", "compile", "__import__", "getattr", "globals", "input", "type"):
            self.assertNotIn(dangerous, worker_loop.SAFE_BUILTIN_NAMES)


class EnvironmentTests(unittest.TestCase):
    def test_the_child_sees_no_secrets_and_only_the_allow_listed_environment(self) -> None:
        secrets = {"MISTRAL_API_KEY": "m-secret", "OPENAI_API_KEY": "o-secret", "ORION_API_KEY": "svc-secret",
                   "ORION_ADMIN_PASSWORD": "pw-secret", "DATABASE_URL": "db-secret", "SOME_TOKEN": "t-secret"}
        with patch.dict(os.environ, secrets):
            env = minimal_env(PROJECT_ROOT)
            code, out, _, _ = run_limited(
                [python_executable(), "-c", "import os, json; print(json.dumps(sorted(os.environ)))"],
                env=env, cwd=str(PROJECT_ROOT), memory_mb=256, cpu_s=10, wall_s=30, stdin_text="",
            )
        self.assertEqual(code, 0)
        for value in secrets.values():
            self.assertNotIn(value, out)
        visible = {name.upper() for name in json.loads(out)}
        for name in secrets:
            self.assertNotIn(name.upper(), visible)
        self.assertIn("ORION_SANDBOX", visible)

    def test_proxies_point_nowhere(self) -> None:
        env = minimal_env(PROJECT_ROOT)
        self.assertTrue(env["HTTPS_PROXY"].endswith(":9"))
        self.assertEqual(env["ORION_SANDBOX"], "1")


class NetworkAndProcessTests(unittest.TestCase):
    def run_py(self, code: str):
        return run_limited(
            [python_executable(), "-c", code], env=minimal_env(PROJECT_ROOT), cwd=str(PROJECT_ROOT),
            memory_mb=256, cpu_s=10, wall_s=30, stdin_text="",
        )

    def test_sockets_are_unusable_after_the_network_is_cut(self) -> None:
        code, out, err, _ = self.run_py(
            "from sandbox.worker_loop import _cut_network\n_cut_network()\nimport socket\n"
            "for attempt in (lambda: socket.socket(), lambda: socket.create_connection(('127.0.0.1', 9)),"
            " lambda: socket.getaddrinfo('example.com', 80)):\n"
            "    try:\n        attempt()\n        print('LEAK')\n    except PermissionError:\n        print('blocked')\n"
        )
        self.assertEqual(out.split(), ["blocked", "blocked", "blocked"], err)

    @unittest.skipUnless(os.name == "nt", "ActiveProcessLimit is the Windows job-object limit")
    def test_the_child_cannot_start_other_processes(self) -> None:
        code, out, err, _ = self.run_py(
            "import subprocess\ntry:\n    subprocess.run(['cmd', '/c', 'echo spawned'], capture_output=True, timeout=10, check=True)\n"
            "    print('SPAWNED')\nexcept Exception as exc:\n    print('blocked', type(exc).__name__)\n"
        )
        self.assertIn("blocked", out + err)
        self.assertNotIn("SPAWNED", out)


class SecurityProbeTests(unittest.TestCase):
    """The frozen 300-probe attack set must stay fully blocked after the policy unification."""

    def test_every_probe_is_blocked_by_the_full_pipeline_and_by_the_ast_gate_alone(self) -> None:
        from bench.eval_security import PROBES, test_probe_blocked
        from harness.sandbox import validate_code_ast

        total = sum(len(v) for v in PROBES.values())
        self.assertEqual(total, 300)
        escaped = [p for probes in PROBES.values() for p in probes if not test_probe_blocked(p)]
        self.assertEqual(escaped, [])
        ast_missed = [p for probes in PROBES.values() for p in probes if validate_code_ast(p)[0]]
        self.assertEqual(ast_missed, [], "the AST gate alone must also block every probe")


class UnifiedPolicyTests(unittest.TestCase):
    BAD = {
        "eval": "def answer(t):\n    return eval(t)\n",
        "exec": "def answer(t):\n    exec(t)\n",
        "open": "def answer(t):\n    return open('x').read()\n",
        "os import": "import os\ndef answer(t):\n    return os.getcwd()\n",
        "subprocess": "import subprocess\n",
        "from os": "from os import path\n",
        "dunder": "def answer(t):\n    return t.__class__.__bases__\n",
        "getattr": "def answer(t):\n    return getattr(t, 'upper')()\n",
        "__import__": "def answer(t):\n    return __import__('os')\n",
        "builtins": "def answer(t):\n    return __builtins__['open']\n",
        "globals": "def answer(t):\n    return globals()\n",
        "harness import": "from harness.config import get_settings\n",
        "bench import": "import bench.oracles\n",
        "relative import": "from . import sibling\n",
        "environ": "import json\ndef answer(t):\n    return json.environ\n",
        "unknown import": "import numpy\n",
        "syntax": "def answer(:\n",
    }

    def test_the_gate_blocks_every_escape(self) -> None:
        for label, source in self.BAD.items():
            self.assertTrue(check_source(source, allow_imports=WORKER_IMPORTS), f"{label} was not blocked")

    def test_pure_code_passes_the_gate(self) -> None:
        ok = "import json, math\nimport re\ndef answer(task):\n    return json.dumps({'n': math.floor(len(task) / 2)})\n"
        self.assertEqual(check_source(ok, allow_imports=WORKER_IMPORTS), [])

    def test_the_benchmark_gate_uses_the_same_rules_but_allows_no_imports(self) -> None:
        from harness.sandbox import validate_code_ast

        self.assertTrue(validate_code_ast("x = [1, 2, 3]\ny = sum(x)")[0])
        self.assertFalse(validate_code_ast("import json")[0])  # the generated-code gate allows json, this one does not
        self.assertEqual(check_source("import json", allow_imports=WORKER_IMPORTS), [])
        for label, source in self.BAD.items():
            if label in {"syntax", "unknown import"}:
                continue
            self.assertFalse(validate_code_ast(source)[0], f"benchmark gate missed {label}")

    def test_gate_and_benchmark_gate_agree_on_every_non_import_violation(self) -> None:
        from harness.sandbox import validate_code_ast

        for label in ("eval", "exec", "open", "dunder", "getattr", "globals", "builtins"):
            gate_blocked = bool(check_source(self.BAD[label], allow_imports=WORKER_IMPORTS))
            bench_blocked = not validate_code_ast(self.BAD[label])[0]
            self.assertEqual((gate_blocked, bench_blocked), (True, True), label)

    def test_static_gate_reads_files_and_reports_the_first_violation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "agent.py"
            path.write_text(self.BAD["os import"], encoding="utf-8")
            result = static_gate(path)
            self.assertFalse(result.ok)
            self.assertIn("os", result.reason)
            self.assertFalse(static_gate(Path(tmp) / "missing.py").ok)

    def test_the_old_eval_path_is_gone(self) -> None:
        import harness.sandbox as legacy

        self.assertFalse(hasattr(legacy, "execute_sandboxed"))


if __name__ == "__main__":
    unittest.main()
