"""The `orion` CLI, trace scrubbing / rotation / retention, and packaging metadata."""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import tomllib

from harness import cli
from harness import settings_store as ss
from harness.permissions import redact_pii
from harness.trace import Tracer
from harness.types import Span, SpanKind

ROOT = Path(__file__).resolve().parent.parent


def run_cli(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class CliCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self._env = patch.dict(os.environ, {"ORION_STATE_DB": str(root / "s.db"), "ORION_AUTH_DB": str(root / "a.db"), "ORION_ADMIN_PASSWORD": "admin-pass-123"})
        self._env.start()
        os.environ.pop("ORION_ENV", None)
        ss.clear_cache()

    def tearDown(self) -> None:
        self._env.stop()
        ss.clear_cache()
        self._tmp.cleanup()


class CliTests(CliCase):
    def test_version(self) -> None:
        code, out, _ = run_cli("version")
        self.assertEqual((code, out.strip()), (0, f"orion-harness {cli.VERSION}"))

    def test_settings_roundtrip(self) -> None:
        self.assertEqual(run_cli("settings", "get", "guard.threshold")[1].strip(), "0.9")
        code, out, _ = run_cli("settings", "set", "guard.threshold", "0.6")
        self.assertEqual((code, out.strip()), (0, "0.6"))
        self.assertEqual(run_cli("settings", "get", "guard.threshold")[1].strip(), "0.6")
        listing = run_cli("settings", "list")[1]
        self.assertIn("guard.threshold", listing)
        self.assertIn("[admin]", listing)
        self.assertEqual(run_cli("settings", "reset", "guard.threshold")[1].strip(), "0.9")

    def test_settings_errors_are_exit_code_one_with_a_message(self) -> None:
        code, _, err = run_cli("settings", "set", "guard.threshold", "7")
        self.assertEqual(code, 1)
        self.assertIn("at most", err)
        self.assertEqual(run_cli("settings", "get", "no.such.key")[0], 1)

    def test_users_create_and_list(self) -> None:
        with patch.dict(os.environ, {"CLI_PW": "a-good-password"}):
            code, out, _ = run_cli("users", "create", "dana", "--role", "super_user", "--password-env", "CLI_PW")
        self.assertEqual((code, out.strip()), (0, "created dana (super_user)"))
        listing = run_cli("users", "list")[1]
        self.assertIn("dana", listing)
        self.assertIn("admin", listing)
        from backend import database as db

        self.assertTrue(db.verify_user("dana", "a-good-password"))

    def test_users_create_rejects_weak_duplicate_and_empty_passwords(self) -> None:
        with patch.dict(os.environ, {"CLI_PW": "short"}):
            code, _, err = run_cli("users", "create", "erin", "--password-env", "CLI_PW")
        self.assertEqual(code, 1)
        self.assertIn("8 characters", err)
        with patch.dict(os.environ, {"CLI_PW": "a-good-password"}):
            self.assertEqual(run_cli("users", "create", "frank", "--password-env", "CLI_PW")[0], 0)
            self.assertEqual(run_cli("users", "create", "frank", "--password-env", "CLI_PW")[0], 1)
        with patch.dict(os.environ, {"CLI_PW": ""}), self.assertRaises(SystemExit):
            run_cli("users", "create", "gina", "--password-env", "CLI_PW")

    def test_a_password_can_never_be_passed_as_an_argument(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            cli.main(["users", "create", "x", "--password", "hunter2hunter2"])
        self.assertEqual(raised.exception.code, 2)

    def test_invalid_role_and_missing_command_are_usage_errors(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            for argv in (["users", "create", "x", "--role", "root"], []):
                with self.assertRaises(SystemExit) as raised:
                    cli.main(argv)
                self.assertEqual(raised.exception.code, 2)

    def test_connectors_list_and_test(self) -> None:
        from harness import connectors as pkg
        from harness.connectors import ConnectorConfig, ConnectorRegistry

        pkg.set_registry(ConnectorRegistry([ConnectorConfig.from_dict({"id": "docs", "type": "files", "path": self._tmp.name, "entity": "DOCS"})]))
        try:
            listing = json.loads(run_cli("connectors", "list")[1])
            self.assertEqual(listing[0]["id"], "docs")
            code, out, _ = run_cli("connectors", "test", "docs")
            self.assertEqual((code, json.loads(out)["ok"]), (0, True))
            self.assertEqual(run_cli("connectors", "test", "ghost")[0], 1)
        finally:
            pkg.set_registry(None)

    def test_check_reports_json_and_a_status_code(self) -> None:
        with patch("harness.check.snapshot", return_value={"frozen_ok": True, "worker_provider": "x"}):
            code, out, _ = run_cli("check")
        self.assertEqual((code, json.loads(out)["frozen_ok"]), (0, True))
        with patch("harness.check.snapshot", return_value={"frozen_ok": False}):
            self.assertEqual(run_cli("check")[0], 2)

    def test_serve_without_uvicorn_explains_the_extra(self) -> None:
        with patch.dict(sys.modules, {"uvicorn": None}):
            code, _, err = run_cli("serve")
        self.assertEqual(code, 2)
        self.assertIn("orion-harness[api]", err)

    def test_serve_passes_its_options_to_uvicorn(self) -> None:
        with patch("uvicorn.run") as run:
            run_cli("serve", "--host", "0.0.0.0", "--port", "9001")
        self.assertEqual(run.call_args.args[0], "backend.api:app")
        self.assertEqual((run.call_args.kwargs["host"], run.call_args.kwargs["port"]), ("0.0.0.0", 9001))

    def test_default_bind_is_loopback_only(self) -> None:
        self.assertEqual(cli.build_parser().parse_args(["serve"]).host, "127.0.0.1")


class TraceCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "traces").mkdir()
        self.tracer = Tracer(traces_dir=self.root / "traces")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def span(self, rid: str = "REQ-1", **extra) -> Span:
        return Span(request_id=rid, span_id="s1", parent_id=None, kind=SpanKind.TOOL, name="tool.x", started_at="now", extra=extra)


class ScrubTests(TraceCase):
    SECRET = "k" * 45

    def test_pii_is_masked_on_disk_but_not_in_memory(self) -> None:
        self.tracer.append(self.span(preview=f"mail jane.doe@corp.com phone 555-123-4567 token {self.SECRET}", nested={"who": "bob@corp.com"}))
        disk = (self.root / "traces" / "REQ-1.jsonl").read_text(encoding="utf-8")
        audit = (self.root / "audit_log.jsonl").read_text(encoding="utf-8")
        for text in (disk, audit):
            self.assertNotIn("jane.doe@corp.com", text)
            self.assertNotIn("555-123-4567", text)
            self.assertNotIn(self.SECRET, text)
            self.assertNotIn("bob@corp.com", text)
            self.assertIn("[EMAIL REDACTED]", text)
        in_memory = self.tracer.spans_for("REQ-1")[0]
        self.assertIn("jane.doe@corp.com", in_memory.extra["preview"])  # the judge and the bench read this copy

    def test_scrubbing_can_be_turned_off(self) -> None:
        self.tracer.scrub = False
        self.tracer.append(self.span(preview="jane.doe@corp.com"))
        self.assertIn("jane.doe@corp.com", (self.root / "traces" / "REQ-1.jsonl").read_text(encoding="utf-8"))

    def test_ordinary_numbers_and_text_are_untouched(self) -> None:
        self.tracer.append(self.span(preview="CPIAUCSL=213.153 on 2024-05-15, 4,000 rows, id EMP123456"))
        text = (self.root / "traces" / "REQ-1.jsonl").read_text(encoding="utf-8")
        for fragment in ("213.153", "2024-05-15", "EMP123456"):
            self.assertIn(fragment, text)

    def test_redact_pii_helper(self) -> None:
        self.assertEqual(redact_pii("write to a.b@c.io now"), "write to [EMAIL REDACTED] now")
        self.assertEqual(redact_pii("nothing sensitive"), "nothing sensitive")


class RotationAndRetentionTests(TraceCase):
    def test_the_audit_log_rotates_and_keeps_three_backups_without_losing_lines(self) -> None:
        self.tracer.max_audit_bytes = 400
        for i in range(60):
            self.tracer.append(self.span(rid=f"R{i}", preview="x" * 40))
        names = sorted(p.name for p in self.root.glob("audit_log*.jsonl"))
        self.assertEqual(names, ["audit_log.1.jsonl", "audit_log.2.jsonl", "audit_log.3.jsonl", "audit_log.jsonl"])
        sizes = [p.stat().st_size for p in self.root.glob("audit_log*.jsonl")]
        self.assertTrue(all(s < 1500 for s in sizes), sizes)
        newest = (self.root / "audit_log.jsonl").read_text(encoding="utf-8")
        self.assertIn("R59", newest)  # the newest events are never the ones dropped

    def test_log_events_rotate_too(self) -> None:
        self.tracer.max_audit_bytes = 300
        for i in range(40):
            self.tracer.log_event("evt", "u", f"R{i}", {"blob": "y" * 50})
        self.assertTrue((self.root / "audit_log.1.jsonl").exists())

    def test_prune_removes_only_old_request_files(self) -> None:
        old, fresh = self.root / "traces" / "OLD.jsonl", self.root / "traces" / "NEW.jsonl"
        old_meta = self.root / "traces" / "OLD.meta.json"
        for path in (old, fresh, old_meta):
            path.write_text("{}", encoding="utf-8")
        long_ago = time.time() - 40 * 86400
        os.utime(old, (long_ago, long_ago))
        os.utime(old_meta, (long_ago, long_ago))
        self.tracer.append(self.span())  # creates the audit log
        self.assertEqual(self.tracer.prune(30), 2)
        self.assertFalse(old.exists())
        self.assertFalse(old_meta.exists())
        self.assertTrue(fresh.exists())
        self.assertTrue((self.root / "audit_log.jsonl").exists())

    def test_prune_with_zero_keeps_everything(self) -> None:
        path = self.root / "traces" / "OLD.jsonl"
        path.write_text("{}", encoding="utf-8")
        os.utime(path, (1, 1))
        self.assertEqual(self.tracer.prune(0), 0)
        self.assertTrue(path.exists())


class ExporterTests(TraceCase):
    def test_exporters_receive_scrubbed_span_dicts(self) -> None:
        seen: list[dict] = []
        self.tracer.add_exporter(seen.append)
        self.tracer.append(self.span(preview="jane.doe@corp.com"))
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0]["name"], "tool.x")
        self.assertNotIn("jane.doe@corp.com", json.dumps(seen[0]))

    def test_a_failing_exporter_never_breaks_tracing(self) -> None:
        def broken(_: dict) -> None:
            raise RuntimeError("collector down")

        good: list[dict] = []
        self.tracer.add_exporter(broken)
        self.tracer.add_exporter(good.append)
        self.tracer.append(self.span())
        self.assertEqual(len(good), 1)
        self.assertTrue((self.root / "traces" / "REQ-1.jsonl").exists())


class PackagingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.meta = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    def test_project_metadata_and_console_script(self) -> None:
        project = self.meta["project"]
        self.assertEqual(project["name"], "orion-harness")
        self.assertEqual(project["version"], cli.VERSION)
        self.assertEqual(project["scripts"]["orion"], "harness.cli:main")
        self.assertEqual(project["entry-points"]["orion.packs"]["enterprise_lake"], "packs.enterprise_lake:PACK")

    def test_extras_cover_every_optional_feature(self) -> None:
        extras = self.meta["project"]["optional-dependencies"]
        for name in ("api", "guard", "mcp", "search", "rag", "sandbox", "dev", "all"):
            self.assertIn(name, extras)
        joined = " ".join(extras["guard"])
        self.assertIn("transformers", joined)
        self.assertTrue(any(dep.startswith("ddgs") for dep in extras["search"]))
        self.assertTrue(any(dep.startswith("mcp") for dep in extras["mcp"]))

    def test_core_dependencies_do_not_pull_in_heavy_optional_stacks(self) -> None:
        core = " ".join(self.meta["project"]["dependencies"])
        for heavy in ("torch", "transformers", "chromadb", "gradio", "fastapi"):
            self.assertNotIn(heavy, core)

    def test_the_packaged_modules_exist(self) -> None:
        for package in self.meta["tool"]["setuptools"]["packages"]["find"]["include"]:
            self.assertTrue((ROOT / package.rstrip("*")).is_dir(), package)

    def test_the_paper_and_bench_are_not_shipped(self) -> None:
        included = " ".join(self.meta["tool"]["setuptools"]["packages"]["find"]["include"])
        for private in ("bench", "paper", "data", "outputs"):
            self.assertNotIn(private, included)

    def test_the_pack_entry_point_resolves(self) -> None:
        import importlib

        module, _, attr = self.meta["project"]["entry-points"]["orion.packs"]["enterprise_lake"].partition(":")
        self.assertEqual(getattr(importlib.import_module(module), attr).name, "enterprise_lake")

    def test_the_lazy_package_exports_resolve(self) -> None:
        import harness
        from harness import planner

        self.assertIs(harness.coordinate, planner.coordinate)
        self.assertIs(harness.coordinate_stream, planner.coordinate_stream)
        with self.assertRaises(AttributeError):
            harness.not_a_real_name  # noqa: B018


if __name__ == "__main__":
    unittest.main()
