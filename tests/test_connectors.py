"""Bring-your-own-data connectors: live F4 introspection, read-only, masking, caps, joins, secrets."""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from harness import connectors as conn_pkg
from harness.connectors import ConnectorConfig, ConnectorError, ConnectorRegistry
from harness.connectors.base import scrub
from harness.connectors.files import FilesConnector
from harness.connectors.sql import SqlAlchemyConnector
from harness.context import new_context
from harness.errors import SchemaInvalidError
from harness.packs import PackRegistry, set_registry
from harness.tool_registry import ToolRegistry
from packs.enterprise_lake import PACK

ROWS = [
    (1, "Ada", "eng", 120.0, "111"),
    (2, "Bob", "eng", 100.0, "222"),
    (3, "Cy", "ops", 80.0, "333"),
    (4, "Di", "ops", 90.0, "444"),
    (5, "Ed%", "hr", 70.0, "555"),
    (6, "Flo", "hr", 75.0, "666"),
]


def make_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE employees (id INTEGER, name TEXT, dept TEXT, salary REAL, ssn TEXT)")
    conn.execute("CREATE TABLE secrets (k TEXT)")
    conn.executemany("INSERT INTO employees VALUES (?,?,?,?,?)", ROWS)
    conn.commit()
    conn.close()


class SqlConnectorCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "hr.db"
        make_db(self.db)
        self._env = patch.dict(os.environ, {"ORION_TEST_DB_URL": f"sqlite:///{self.db.as_posix()}"})
        self._env.start()
        self.conn = SqlAlchemyConnector(
            ConnectorConfig.from_dict(
                {
                    "id": "hrdb",
                    "type": "sqlalchemy",
                    "url_env": "ORION_TEST_DB_URL",
                    "entity": "hrdb",
                    "tables": ["employees"],
                    "sensitive_columns": {"limited": ["salary", "ssn"]},
                    "row_cap": 5,
                }
            )
        )

    def tearDown(self) -> None:
        self.conn.dispose()
        self._env.stop()
        self._tmp.cleanup()


class SqlConnectorTests(SqlConnectorCase):
    def test_only_allow_listed_tables_are_visible(self) -> None:
        self.assertEqual(self.conn.tables(), ["employees"])
        for forbidden in ("sqlite_master", "secrets"):
            with self.assertRaises(SchemaInvalidError):
                self.conn.query({"table": forbidden})

    def test_schema_is_live_and_masked_by_role(self) -> None:
        full = [c.name for c in self.conn.schema("employees")]
        limited = [c.name for c in self.conn.schema("employees", "limited")]
        self.assertEqual(full, ["id", "name", "dept", "salary", "ssn"])
        self.assertEqual(limited, ["id", "name", "dept"])

    def test_renamed_column_surfaces_as_f4_with_the_new_name(self) -> None:
        self.conn.query({"table": "employees", "filters": [{"column": "salary", "op": "gt", "value": 0}]})
        writer = sqlite3.connect(self.db)
        writer.execute("ALTER TABLE employees RENAME COLUMN salary TO salary_inr")
        writer.commit()
        writer.close()
        with self.assertRaises(SchemaInvalidError) as err:
            self.conn.query({"table": "employees", "filters": [{"column": "salary", "op": "gt", "value": 0}]})
        self.assertIn("salary_inr", str(err.exception))  # the repair hint carries the live column list
        fixed = self.conn.query({"table": "employees", "filters": [{"column": "salary_inr", "op": "gt", "value": 100}]})
        self.assertEqual(fixed["rows"], 1)

    def test_the_engine_cannot_write(self) -> None:
        from sqlalchemy import text
        from sqlalchemy.exc import SQLAlchemyError

        with self.assertRaises(SQLAlchemyError):
            with self.conn.engine().begin() as c:
                c.execute(text("INSERT INTO employees VALUES (99, 'X', 'y', 1, 'z')"))
        self.assertEqual(self.conn.query({"table": "employees", "agg": {"id": "count"}})["records"][0]["id_count"], 6)

    def test_filters_group_by_and_aggregates_match_pandas(self) -> None:
        frame = pd.DataFrame(ROWS, columns=["id", "name", "dept", "salary", "ssn"])
        out = self.conn.query({"table": "employees", "group_by": ["dept"], "agg": {"salary": "mean"}, "sort": {"column": "dept", "ascending": True}})
        got = {r["dept"]: r["salary"] for r in out["records"]}
        self.assertEqual(got, frame.groupby("dept")["salary"].mean().to_dict())
        counted = self.conn.query({"table": "employees", "filters": [{"column": "dept", "op": "in", "value": ["eng", "ops"]}], "agg": {"id": "count"}})
        self.assertEqual(counted["records"][0]["id_count"], 4)
        nunique = self.conn.query({"table": "employees", "agg": {"dept": "nunique"}})
        self.assertEqual(nunique["records"][0]["dept_nunique"], 3)

    def test_row_cap_is_enforced(self) -> None:
        out = self.conn.query({"table": "employees", "limit": 100})
        self.assertEqual(out["rows"], 5)

    def test_masked_columns_cannot_be_referenced_and_do_not_leak(self) -> None:
        with self.assertRaises(SchemaInvalidError) as err:
            self.conn.query({"table": "employees", "columns": ["salary"]}, role="limited")
        self.assertNotIn("salary", str(err.exception).split("available:")[1])
        self.assertNotIn("ssn", str(err.exception))
        rows = self.conn.query({"table": "employees"}, role="limited")["columns"]
        self.assertEqual(rows, ["id", "name", "dept"])

    def test_contains_escapes_like_wildcards(self) -> None:
        out = self.conn.query({"table": "employees", "filters": [{"column": "name", "op": "contains", "value": "%"}]})
        self.assertEqual([r["name"] for r in out["records"]], ["Ed%"])

    def test_values_are_bound_not_interpolated(self) -> None:
        out = self.conn.query({"table": "employees", "filters": [{"column": "name", "op": "eq", "value": "x' OR '1'='1"}]})
        self.assertTrue(out["empty"])

    def test_unsupported_agg_and_op_are_schema_errors(self) -> None:
        with self.assertRaises(SchemaInvalidError):
            self.conn.query({"table": "employees", "agg": {"salary": "std"}})
        with self.assertRaises(SchemaInvalidError):
            self.conn.query({"table": "employees", "filters": [{"column": "id", "op": "regex", "value": "."}]})

    def test_result_has_the_standard_tool_payload(self) -> None:
        out = self.conn.query({"table": "employees", "limit": 2})
        for key in ("ok", "table", "entity", "rows", "columns", "records", "preview", "result_hash", "empty"):
            self.assertIn(key, out)
        self.assertEqual((out["table"], out["entity"]), ("hrdb.employees", "HRDB"))


class SecretHandlingTests(unittest.TestCase):
    def test_missing_env_var_is_a_clear_config_error(self) -> None:
        c = SqlAlchemyConnector(ConnectorConfig.from_dict({"id": "x", "type": "sqlalchemy", "url_env": "ORION_NOT_SET_XYZ", "entity": "X"}))
        with self.assertRaises(ConnectorError):
            c.tables()
        self.assertFalse(c.test()["ok"])

    def test_credentials_never_appear_in_errors_or_test_output(self) -> None:
        with patch.dict(os.environ, {"ORION_BAD_URL": "postgresql://admin:SuperSecretPw@127.0.0.1:1/db"}):
            c = SqlAlchemyConnector(
                ConnectorConfig.from_dict(
                    {"id": "x", "type": "sqlalchemy", "url_env": "ORION_BAD_URL", "entity": "X", "statement_timeout_s": 1}
                )
            )
            report = c.test()
            self.assertFalse(report["ok"])
            self.assertNotIn("SuperSecretPw", str(report))
            with self.assertRaises(Exception) as err:
                c.query({"table": "t"})
            self.assertNotIn("SuperSecretPw", str(err.exception))
            c.dispose()

    def test_scrub_removes_url_credentials(self) -> None:
        self.assertEqual(scrub("could not connect to mysql://root:pw@host/db now"), "could not connect to mysql://***@host/db now")

    def test_config_validation(self) -> None:
        with self.assertRaises(ValueError):
            ConnectorConfig.from_dict({"id": "bad id!", "type": "files", "entity": "X"})
        with self.assertRaises(ValueError):
            ConnectorConfig.from_dict({"type": "files", "entity": "X"})
        cfg = ConnectorConfig.from_dict({"id": "ok", "type": "files", "entity": "x", "row_cap": 10**9})
        self.assertEqual((cfg.entity, cfg.row_cap), ("X", 5000))


class FilesConnectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        pd.DataFrame(ROWS, columns=["id", "name", "dept", "salary", "ssn"]).to_csv(self.dir / "staff.csv", index=False)
        pd.DataFrame({"a": [1, 2, 3]}).to_parquet(self.dir / "nums.parquet")
        (self.dir / "notes.txt").write_text("ignored")
        self.conn = FilesConnector(
            ConnectorConfig.from_dict(
                {"id": "files", "type": "files", "path": str(self.dir), "entity": "FILES", "sensitive_columns": {"limited": ["salary"]}, "row_cap": 3}
            )
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_tables_are_supported_file_stems_only(self) -> None:
        self.assertEqual(sorted(self.conn.tables()), ["nums", "staff"])

    def test_query_masking_and_cap(self) -> None:
        out = self.conn.query({"table": "staff"}, role="limited")
        self.assertNotIn("salary", out["columns"])
        self.assertEqual(out["rows"], 3)
        agg = self.conn.query({"table": "nums", "agg": {"a": "sum"}})
        self.assertEqual(agg["records"][0]["a_sum"], 6)

    def test_unknown_table_and_column_are_f4(self) -> None:
        with self.assertRaises(SchemaInvalidError):
            self.conn.query({"table": "missing"})
        with self.assertRaises(SchemaInvalidError):
            self.conn.query({"table": "staff", "filters": [{"column": "nope", "op": "eq", "value": 1}]})

    def test_replaced_file_is_reloaded(self) -> None:
        self.assertEqual(self.conn.query({"table": "nums", "agg": {"a": "sum"}})["records"][0]["a_sum"], 6)
        frame = pd.DataFrame({"a": [10, 20]})
        frame.to_parquet(self.dir / "nums.parquet")
        os.utime(self.dir / "nums.parquet", (9_999_999_999, 9_999_999_999))
        self.assertEqual(self.conn.query({"table": "nums", "agg": {"a": "sum"}})["records"][0]["a_sum"], 30)

    def test_table_allow_list(self) -> None:
        restricted = FilesConnector(ConnectorConfig.from_dict({"id": "f", "type": "files", "path": str(self.dir), "entity": "F", "tables": ["nums"]}))
        self.assertEqual(restricted.tables(), ["nums"])
        with self.assertRaises(SchemaInvalidError):
            restricted.query({"table": "staff"})

    def test_missing_folder_is_a_config_error(self) -> None:
        bad = FilesConnector(ConnectorConfig.from_dict({"id": "f", "type": "files", "path": str(self.dir / "nope"), "entity": "F"}))
        with self.assertRaises(ConnectorError):
            bad.tables()


class RegistryAndToolTests(SqlConnectorCase):
    def setUp(self) -> None:
        super().setUp()
        set_registry(PackRegistry([PACK]))
        self.reg = ConnectorRegistry([self.conn.config])
        conn_pkg.set_registry(self.reg)
        self.tools = ToolRegistry()

    def tearDown(self) -> None:
        conn_pkg.set_registry(None)
        set_registry(None)
        super().tearDown()

    def _ctx(self, product: bool = True, role=None):
        ctx = new_context(role=role)
        if product:
            ctx.extra["mode"] = "product"
        return ctx

    def test_entity_joins_with_the_built_in_lake_are_illegal_by_default(self) -> None:
        from harness.packs import registry

        ok, reason = registry().entities_join_legal(["HRDB", "MNC"])
        self.assertFalse(ok)
        self.assertIn("cannot join", reason)
        self.assertTrue(registry().entities_join_legal(["HRDB", "HRDB"])[0])

    def test_declared_legal_join_is_symmetric(self) -> None:
        cfg = ConnectorConfig.from_dict({"id": "wh", "type": "files", "path": ".", "entity": "WH", "legal_joins": ["AAPL"]})
        self.reg.add(cfg)
        from harness.packs import registry

        self.assertTrue(registry().entities_join_legal(["WH", "AAPL"])[0])
        self.assertTrue(registry().entities_join_legal(["AAPL", "WH"])[0])
        self.assertFalse(registry().entities_join_legal(["WH", "MNC"])[0])

    def test_cues_make_the_planner_see_connector_entities(self) -> None:
        cfg = ConnectorConfig.from_dict({"id": "wh", "type": "files", "path": ".", "entity": "WH", "cues": ["warehouse"]})
        self.reg.add(cfg)
        from harness.entities import detect_mentioned_entities

        self.assertIn("WH", detect_mentioned_entities("how many items are in the warehouse?"))

    def test_sql_spec_routes_connector_tables_in_product_mode(self) -> None:
        res = self.tools.call("sql_spec", {"table": "hrdb.employees", "agg": {"id": "count"}}, self._ctx())
        self.assertTrue(res.ok, res.error)
        self.assertEqual(res.data["records"][0]["id_count"], 6)

    def test_connector_tables_do_not_exist_in_eval_mode(self) -> None:
        res = self.tools.call("sql_spec", {"table": "hrdb.employees"}, self._ctx(product=False))
        self.assertFalse(res.ok)
        self.assertEqual(res.error_class, "SchemaInvalid")

    def test_limited_role_cannot_read_masked_columns_through_the_tool(self) -> None:
        res = self.tools.call("sql_spec", {"table": "hrdb.employees", "columns": ["salary"]}, self._ctx(role="limited"))
        self.assertFalse(res.ok)
        self.assertEqual(res.error_class, "SchemaInvalid")

    def test_schema_lookup_lists_and_describes_connector_tables(self) -> None:
        listing = self.tools.call("schema_lookup", {}, self._ctx())
        self.assertIn("hrdb.employees", listing.data["connector_tables"])
        detail = self.tools.call("schema_lookup", {"table": "hrdb.employees"}, self._ctx(role="limited"))
        self.assertEqual([c["name"] for c in detail.data["columns"]], ["id", "name", "dept"])

    def test_cross_entity_query_is_denied_after_touching_another_entity(self) -> None:
        ctx = self._ctx()
        ctx.extra["touched_entities"] = ["MNC"]
        res = self.tools.call("sql_spec", {"table": "hrdb.employees"}, ctx)
        self.assertFalse(res.ok)
        self.assertEqual(res.error_class, "PolicyDenied")

    def test_unknown_connector_is_f4_with_the_known_list(self) -> None:
        res = self.tools.call("sql_spec", {"table": "ghost.t"}, self._ctx())
        self.assertEqual(res.error_class, "SchemaInvalid")
        self.assertIn("hrdb", res.error)

    def test_worker_prompt_text_hides_masked_columns(self) -> None:
        self.assertIn("salary", self.reg.prompt_text(None))
        self.assertNotIn("salary", self.reg.prompt_text("limited"))

    def test_config_file_loading_skips_bad_entries_and_cleans_up_on_reload(self) -> None:
        path = Path(self._tmp.name) / "connectors.json"
        path.write_text(
            '{"connectors": [{"id": "good", "type": "files", "path": ".", "entity": "GOOD"},'
            ' {"id": "bad", "type": "nonsense", "entity": "BAD"}, {"oops": true}]}',
            encoding="utf-8",
        )
        reg = ConnectorRegistry()
        self.assertEqual(reg.load_file(path), ["good"])
        from harness.packs import registry

        self.assertIn("GOOD", registry().allowed_entities())
        reg.load_file(Path(self._tmp.name) / "missing.json")  # reload with no file removes everything
        self.assertNotIn("GOOD", registry().allowed_entities())

    def test_admin_api_lists_and_tests_connectors(self) -> None:
        from fastapi.testclient import TestClient

        from tests.apitest import ADMIN_PASSWORD

        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {
                "ORION_AUTH_DB": str(Path(tmp) / "a.db"),
                "ORION_STATE_DB": str(Path(tmp) / "s.db"),
                "ORION_ADMIN_PASSWORD": ADMIN_PASSWORD,
                "ORION_AUTH_REQUIRED": "true",
            },
        ):
            from backend.api import app

            client = TestClient(app)
            token = client.post("/auth/login", json={"username": "admin", "password": ADMIN_PASSWORD}).json()["token"]
            headers = {"Authorization": f"Bearer {token}"}
            self.assertEqual(client.get("/admin/connectors", headers=headers).json()[0]["id"], "hrdb")
            report = client.post("/admin/connectors/hrdb/test", headers=headers).json()
            self.assertTrue(report["ok"])
            self.assertEqual(client.post("/admin/connectors/ghost/test", headers=headers).status_code, 404)
            self.assertEqual(client.get("/admin/connectors").status_code, 401)


if __name__ == "__main__":
    unittest.main()
