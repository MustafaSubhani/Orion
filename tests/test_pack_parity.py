"""The domain pack extraction must not change what the harness exposes to models or policy."""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from tests.registry_snapshot import SNAPSHOT_PATH, build_snapshot

ROOT = Path(__file__).resolve().parent.parent
CORE_DIRS = ("harness", "workers", "sandbox", "backend", "kb", "lab", "repair")
# Files that legitimately mention a dataset: the lazy compatibility aliases carry no literals.
EXEMPT = {"harness/tool_extras.py"}
FORBIDDEN = re.compile(r"\b(MNC|AAPL|MSFT|FRED|SYNTH_CAL)\b|John Doe|EMP\d{4,}|Salary_INR|PerformanceScore")


class SnapshotParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.old = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
        cls.new = build_snapshot()

    def _same(self, key: str) -> None:
        if isinstance(self.old[key], dict):
            for name in sorted(set(self.old[key]) | set(self.new[key])):
                self.assertEqual(self.old[key].get(name), self.new[key].get(name), f"{key}[{name}] changed")
        self.assertEqual(self.old[key], self.new[key])

    def test_agent_cards_are_identical(self) -> None:
        self._same("cards")

    PLATFORM_ADDITIONS = {"web_search"}  # core tools added after the snapshot; everything else must match

    def test_tool_manifests_are_identical(self) -> None:
        for name, manifest in self.old["manifests"].items():
            self.assertEqual(manifest, self.new["manifests"].get(name), f"manifest {name} changed")
        extra = set(self.new["manifests"]) - set(self.old["manifests"])
        self.assertLessEqual(extra, self.PLATFORM_ADDITIONS, f"unexpected new tools: {extra - self.PLATFORM_ADDITIONS}")

    def test_tools_exposed_to_each_agent_are_identical(self) -> None:
        self._same("card_tools")

    def test_entity_policy_is_identical(self) -> None:
        for key in ("legal_joins", "allowed_entities", "egress_allowlist", "known_collections"):
            self._same(key)

    def test_role_policy_is_identical(self) -> None:
        self._same("role_tools")
        self._same("salary_columns")


class NoHardcodingTests(unittest.TestCase):
    def test_core_has_no_dataset_literals(self) -> None:
        hits: list[str] = []
        for folder in CORE_DIRS:
            for path in sorted((ROOT / folder).rglob("*.py")):
                rel = path.relative_to(ROOT).as_posix()
                if rel in EXEMPT or "__pycache__" in rel or "/generated/" in rel:
                    continue
                for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                    if FORBIDDEN.search(line):
                        hits.append(f"{rel}:{number}: {line.strip()[:100]}")
        self.assertEqual(hits, [], "dataset-specific text found in core:\n" + "\n".join(hits))

    def test_core_never_evals_or_execs_outside_the_sandbox(self) -> None:
        hits: list[str] = []
        pattern = re.compile(r"(?<![\w.])(eval|exec)\(")
        for folder in ("harness", "workers", "backend", "kb", "lab", "repair"):
            for path in sorted((ROOT / folder).rglob("*.py")):
                rel = path.relative_to(ROOT).as_posix()
                if "__pycache__" in rel or "/generated/" in rel:
                    continue
                for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                    code = line.split("#", 1)[0]
                    if "_BANNED" in code:
                        continue  # the skill ban-list names eval( as a forbidden token
                    if pattern.search(code) and not code.strip().startswith(("r\"", "'", '"')):
                        hits.append(f"{rel}:{number}: {line.strip()[:100]}")
        # permissions.py lists "exec(" only as a blocked pattern; harness/sandbox.py is the legacy AST gate.
        hits = [h for h in hits if "harness/permissions.py" not in h and "harness/sandbox.py" not in h]
        self.assertEqual(hits, [], "eval/exec outside sandbox/:\n" + "\n".join(hits))


class PackRegistryTests(unittest.TestCase):
    def test_default_pack_loads_and_unknown_pack_is_an_error(self) -> None:
        from harness.packs import load_packs

        self.assertEqual([p.name for p in load_packs(["enterprise_lake"])], ["enterprise_lake"])
        with self.assertRaises(ValueError):
            load_packs(["no_such_pack"])

    def test_module_attr_path_loads_a_pack(self) -> None:
        from harness.packs import load_packs

        self.assertEqual(load_packs(["packs.enterprise_lake:PACK"])[0].name, "enterprise_lake")

    def test_second_pack_extends_but_never_removes_policy(self) -> None:
        from harness.packs import PackRegistry
        from packs.enterprise_lake import PACK

        class Extra:
            name = "extra"

            def entities(self):
                return ("ACME",)

            def legal_joins(self):
                return {"ACME": frozenset({"ACME"})}

            def egress_allowlist(self):
                return ("acme.example",)

        reg = PackRegistry([PACK, Extra()])
        self.assertIn("ACME", reg.allowed_entities())
        self.assertIn("sec.gov", reg.egress_allowlist())
        self.assertIn("acme.example", reg.egress_allowlist())
        self.assertFalse(reg.entities_join_legal(["ACME", "MNC"])[0])
        self.assertTrue(reg.entities_join_legal(["AAPL", "FRED"])[0])

    def test_runtime_extension_adds_entities_and_can_be_removed(self) -> None:
        from harness.packs import PackRegistry
        from packs.enterprise_lake import PACK

        reg = PackRegistry([PACK])
        self.assertFalse(reg.entities_join_legal(["WAREHOUSE", "AAPL"])[0])
        reg.extend_entities("conn:wh", {"WAREHOUSE": frozenset({"WAREHOUSE"})})
        self.assertIn("WAREHOUSE", reg.allowed_entities())
        ok, reason = reg.entities_join_legal(["WAREHOUSE", "AAPL"])
        self.assertFalse(ok)
        self.assertIn("cannot join", reason)
        reg.remove_extension("conn:wh")
        self.assertNotIn("WAREHOUSE", reg.allowed_entities())

    def test_empty_registry_has_no_lake_knowledge(self) -> None:
        from harness.packs import PackRegistry

        reg = PackRegistry([])
        self.assertEqual(reg.agents(), [])
        self.assertEqual(reg.allowed_entities(), ("KB",))
        self.assertEqual(reg.egress_allowlist(), ())
        self.assertEqual(reg.detect_entities("payroll for Apple"), [])
        self.assertEqual(reg.synthesis_hint(), "")

    def test_kb_never_joins_anything_else(self) -> None:
        from harness.packs import registry

        self.assertFalse(registry().entities_join_legal(["KB", "AAPL"])[0])


if __name__ == "__main__":
    unittest.main()
