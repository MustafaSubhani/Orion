"""Settings precedence (admin > env > default), validation, audit, and product-only consumers."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness import settings_store as ss
from harness.context import new_context
from harness.errors import RetrievalMissError
from harness.tool_registry import _egress_hosts, _enforce_min_similarity, best_similarity


class StoreTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._env = patch.dict(os.environ, {"ORION_STATE_DB": str(Path(self._tmp.name) / "state.db")})
        self._env.start()
        ss.clear_cache()

    def tearDown(self) -> None:
        self._env.stop()
        ss.clear_cache()
        self._tmp.cleanup()


class PrecedenceTests(StoreTestCase):
    def test_default_when_nothing_is_set(self) -> None:
        self.assertEqual(ss.effective("guard.threshold"), 0.9)
        self.assertEqual(ss.source("guard.threshold"), "default")

    def test_env_overrides_default(self) -> None:
        with patch.dict(os.environ, {"ORION_GUARD_THRESHOLD": "0.5"}):
            ss.clear_cache()
            self.assertEqual(ss.effective("guard.threshold"), 0.5)
            self.assertEqual(ss.source("guard.threshold"), "env")

    def test_admin_override_beats_env_and_takes_effect_immediately(self) -> None:
        with patch.dict(os.environ, {"ORION_GUARD_ENABLED": "false"}):
            ss.clear_cache()
            self.assertFalse(ss.effective("guard.enabled"))
            ss.set_value("guard.enabled", True, "admin")
            self.assertTrue(ss.effective("guard.enabled"))
            self.assertEqual(ss.source("guard.enabled"), "admin")

    def test_reset_returns_to_env_or_default(self) -> None:
        ss.set_value("parallel.max_workers", 8, "admin")
        self.assertEqual(ss.effective("parallel.max_workers"), 8)
        ss.reset("parallel.max_workers", "admin")
        self.assertEqual(ss.effective("parallel.max_workers"), 4)

    def test_tenants_are_isolated(self) -> None:
        ss.set_value("guard.enabled", True, "a", tenant="acme")
        self.assertTrue(ss.effective("guard.enabled", tenant="acme"))
        self.assertFalse(ss.effective("guard.enabled", tenant="other"))

    def test_bad_env_value_falls_back_to_default(self) -> None:
        with patch.dict(os.environ, {"ORION_PARALLEL_MAX_WORKERS": "not-a-number"}):
            ss.clear_cache()
            self.assertEqual(ss.effective("parallel.max_workers"), 4)


class ValidationTests(StoreTestCase):
    def test_unknown_key_is_rejected(self) -> None:
        with self.assertRaises(ss.SettingsError):
            ss.set_value("nope.key", 1, "admin")
        with self.assertRaises(ss.SettingsError):
            ss.effective("nope.key")

    def test_type_and_range_validation(self) -> None:
        with self.assertRaises(ss.SettingsError):
            ss.set_value("guard.threshold", 1.5, "admin")
        with self.assertRaises(ss.SettingsError):
            ss.set_value("parallel.max_workers", 0, "admin")
        with self.assertRaises(ss.SettingsError):
            ss.set_value("parallel.max_workers", "abc", "admin")
        with self.assertRaises(ss.SettingsError):
            ss.set_value("guard.enabled", "maybe", "admin")

    def test_choices_and_prefixed_values(self) -> None:
        with self.assertRaises(ss.SettingsError):
            ss.set_value("guard.fail_mode", "sometimes", "admin")
        self.assertEqual(ss.set_value("web_search.backend", "ddg", "admin"), "ddg")
        self.assertEqual(ss.set_value("web_search.backend", "mcp:brave", "admin"), "mcp:brave")
        with self.assertRaises(ss.SettingsError):
            ss.set_value("web_search.backend", "mcp:", "admin")

    def test_bool_strings_and_list_parsing(self) -> None:
        self.assertTrue(ss.set_value("guard.enabled", "yes", "admin"))
        self.assertEqual(ss.set_value("egress.allowlist", "Example.com, api.test.io", "admin"), ["example.com", "api.test.io"])
        self.assertEqual(ss.set_value("egress.allowlist", '["a.com"]', "admin"), ["a.com"])

    def test_describe_lists_every_key_with_source(self) -> None:
        rows = {r["key"]: r for r in ss.describe()}
        self.assertEqual(set(rows), set(ss.SCHEMA))
        self.assertIn("source", rows["guard.enabled"])

    def test_every_default_passes_its_own_validation(self) -> None:
        for key, spec in ss.SCHEMA.items():
            ss._coerce(spec, spec.default, key)


class AuditTests(StoreTestCase):
    def test_change_is_written_to_the_audit_log(self) -> None:
        with patch("harness.trace.get_tracer") as tracer:
            ss.set_value("guard.enabled", True, "alice")
            tracer.return_value.log_event.assert_called_once()
            args = tracer.return_value.log_event.call_args.args
            self.assertEqual(args[0], "settings_change")
            self.assertEqual(args[1], "alice")


class ProductOnlyConsumerTests(StoreTestCase):
    def _chunks(self, distance: float) -> list[dict]:
        return [{"chunk_id": "c", "distance": distance, "metadata": {}}]

    def test_best_similarity_is_cosine(self) -> None:
        self.assertAlmostEqual(best_similarity(self._chunks(0.25)), 0.75)
        self.assertIsNone(best_similarity([{"distance": None}]))

    def test_low_similarity_is_a_miss_in_product_only(self) -> None:
        ss.set_value("retrieval.min_similarity", 0.5, "admin")
        product = new_context()
        product.extra["mode"] = "product"
        with self.assertRaises(RetrievalMissError):
            _enforce_min_similarity(self._chunks(0.9), product, "c")
        _enforce_min_similarity(self._chunks(0.2), product, "c")  # similarity 0.8 passes
        _enforce_min_similarity(self._chunks(0.9), new_context(), "c")  # eval mode ignores the floor

    def test_zero_floor_disables_the_check(self) -> None:
        ctx = new_context()
        ctx.extra["mode"] = "product"
        _enforce_min_similarity(self._chunks(0.99), ctx, "c")

    def test_egress_additions_apply_in_product_only(self) -> None:
        ss.set_value("egress.allowlist", ["internal.example.com"], "admin")
        product = new_context()
        product.extra["mode"] = "product"
        self.assertIn("internal.example.com", _egress_hosts(product))
        self.assertNotIn("internal.example.com", _egress_hosts(new_context()))


if __name__ == "__main__":
    unittest.main()
