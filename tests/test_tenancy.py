"""Tenant isolation, sandbox backends and OIDC sign-in.

The rule under test: nothing a tenant creates, configures or can reach is visible to another tenant, and
an administrator of one tenant has no authority over another. The default tenant is the operator tenant:
it keeps the legacy locations and its administrators manage deployment-wide resources.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from harness import settings_store as ss
from harness import tenancy
from tests.apitest import USER_PASSWORD, ApiTestCase, FakeGateway, installed_runtime
from tests.labkit import lab_workspace


class TempState(unittest.TestCase):
    """Temp auth and state databases for tests that are not HTTP tests."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._env = patch.dict(
            os.environ,
            {"ORION_STATE_DB": str(self.root / "s.db"), "ORION_AUTH_DB": str(self.root / "a.db"), "ORION_ADMIN_PASSWORD": "admin-pass-123"},
        )
        self._env.start()
        os.environ.pop("ORION_ENV", None)
        ss.clear_cache()

    def tearDown(self) -> None:
        self._env.stop()
        ss.clear_cache()
        self._tmp.cleanup()


# --------------------------------------------------------------------------- primitives
class SlugTests(unittest.TestCase):
    def test_valid_slugs(self) -> None:
        for value in ("default", "acme", "acme-corp", "a", "org_42", "x" * 48):
            self.assertEqual(tenancy.slug(value), value)

    def test_empty_means_default(self) -> None:
        self.assertEqual(tenancy.slug(None), "default")
        self.assertEqual(tenancy.slug(""), "default")

    def test_anything_that_could_escape_a_path_or_collection_name_is_rejected(self) -> None:
        for value in ("../etc", "a/b", "a\\b", "Acme", "-lead", "_lead", "has space", "dot.dot", "x" * 49, "ünï", "a\x00b", "tenants/../x"):
            with self.assertRaises(tenancy.TenantError, msg=value):
                tenancy.slug(value)
            self.assertFalse(tenancy.is_valid(value))

    def test_tenant_error_is_a_value_error(self) -> None:
        self.assertTrue(issubclass(tenancy.TenantError, ValueError))


class ScopeTests(unittest.TestCase):
    def test_default_outside_any_scope(self) -> None:
        self.assertEqual(tenancy.current(), "default")

    def test_use_binds_nests_and_restores_even_on_error(self) -> None:
        with tenancy.use("acme"):
            self.assertEqual(tenancy.current(), "acme")
            with tenancy.use("beta"):
                self.assertEqual(tenancy.current(), "beta")
            self.assertEqual(tenancy.current(), "acme")
            with contextlib.suppress(RuntimeError), tenancy.use("beta"):
                raise RuntimeError("boom")
            self.assertEqual(tenancy.current(), "acme")
        self.assertEqual(tenancy.current(), "default")

    def test_use_rejects_an_invalid_tenant_without_binding_it(self) -> None:
        with self.assertRaises(tenancy.TenantError), tenancy.use("../x"):
            pass
        self.assertEqual(tenancy.current(), "default")

    def test_new_threads_start_unbound_and_bound_carries_the_tenant(self) -> None:
        seen: dict[str, str] = {}

        def probe(label: str) -> None:
            seen[label] = tenancy.current()

        with tenancy.use("acme"):
            plain = threading.Thread(target=probe, args=("plain",))
            carried = threading.Thread(target=tenancy.bound(probe, "acme"), args=("carried",))
            for t in (plain, carried):
                t.start()
                t.join()
        self.assertEqual(seen, {"plain": "default", "carried": "acme"})

    def test_concurrent_scopes_do_not_bleed_into_each_other(self) -> None:
        results: dict[str, set[str]] = {"a": set(), "b": set()}

        def work(tenant: str, key: str) -> None:
            with tenancy.use(tenant):
                for _ in range(200):
                    results[key].add(tenancy.current())
                    time.sleep(0.0005)

        threads = [threading.Thread(target=work, args=("acme", "a")), threading.Thread(target=work, args=("beta", "b"))]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(results, {"a": {"acme"}, "b": {"beta"}})

    def test_directories_and_collections(self) -> None:
        base = Path("base")
        self.assertEqual(tenancy.scoped_dir(base, "default"), base)
        self.assertEqual(tenancy.scoped_dir(base, "acme"), base / "tenants" / "acme")
        self.assertEqual(tenancy.collection("kb_live", "default"), "kb_live")
        self.assertEqual(tenancy.collection("kb_live", "acme"), "kb_live__acme")
        with tenancy.use("beta"):
            self.assertEqual(tenancy.scoped_dir(base), base / "tenants" / "beta")
            self.assertEqual(tenancy.collection("kb_live"), "kb_live__beta")

    def test_agent_ownership_rules(self) -> None:
        builtin = SimpleNamespace(is_builtin=True, is_custom=False, metadata={})
        stamped = SimpleNamespace(is_builtin=False, is_custom=True, metadata={"tenant": "acme"})
        legacy = SimpleNamespace(is_builtin=False, is_custom=True, metadata={})
        self.assertTrue(all(tenancy.card_visible(builtin, t) for t in ("default", "acme", "beta")))
        self.assertTrue(tenancy.card_visible(stamped, "acme"))
        self.assertFalse(tenancy.card_visible(stamped, "beta"))
        self.assertFalse(tenancy.card_visible(stamped, "default"))
        self.assertTrue(tenancy.card_visible(legacy, "default"))  # an unstamped agent belongs to the operator tenant
        self.assertFalse(tenancy.card_visible(legacy, "acme"))
        self.assertEqual(tenancy.card_owner(legacy), "default")


# --------------------------------------------------------------------------- settings
class SettingsIsolationTests(TempState):
    def test_a_tenant_override_never_reaches_another_tenant(self) -> None:
        ss.set_value("web_search.max_results", 3, "root", "acme")
        self.assertEqual(ss.effective("web_search.max_results", "acme"), 3)
        self.assertEqual(ss.effective("web_search.max_results", "beta"), 5)
        self.assertEqual(ss.effective("web_search.max_results"), 5)
        self.assertEqual(ss.source("web_search.max_results", "acme"), "admin")
        self.assertEqual(ss.source("web_search.max_results", "beta"), "default")

    def test_effective_follows_the_bound_tenant(self) -> None:
        ss.set_value("parallel.max_workers", 2, "root", "acme")
        with tenancy.use("acme"):
            self.assertEqual(ss.effective("parallel.max_workers"), 2)
        self.assertEqual(ss.effective("parallel.max_workers"), 4)
        with tenancy.use("beta"):
            self.assertEqual(ss.effective("parallel.max_workers"), 4)

    def test_an_explicit_tenant_beats_the_bound_one(self) -> None:
        ss.set_value("parallel.max_workers", 2, "root", "acme")
        with tenancy.use("beta"):
            self.assertEqual(ss.effective("parallel.max_workers", "acme"), 2)

    def test_reset_only_affects_the_named_tenant(self) -> None:
        ss.set_value("parallel.max_workers", 2, "root", "acme")
        ss.set_value("parallel.max_workers", 3, "root", "beta")
        ss.reset("parallel.max_workers", "root", "acme")
        self.assertEqual(ss.effective("parallel.max_workers", "acme"), 4)
        self.assertEqual(ss.effective("parallel.max_workers", "beta"), 3)

    def test_an_invalid_tenant_cannot_be_written(self) -> None:
        with self.assertRaises(tenancy.TenantError):
            ss.set_value("parallel.max_workers", 2, "root", "../x")
        with self.assertRaises(tenancy.TenantError):
            ss.reset("parallel.max_workers", "root", "Bad Tenant")

    def test_the_environment_baseline_is_shared_but_a_tenant_override_wins(self) -> None:
        with patch.dict(os.environ, {"ORION_PARALLEL_MAX_WORKERS": "6"}):
            ss.clear_cache()
            self.assertEqual(ss.effective("parallel.max_workers", "acme"), 6)
            ss.set_value("parallel.max_workers", 1, "root", "acme")
            self.assertEqual(ss.effective("parallel.max_workers", "acme"), 1)
            self.assertEqual(ss.effective("parallel.max_workers", "beta"), 6)

    def test_background_work_uses_the_threads_tenant(self) -> None:
        from harness import summarizer

        ss.set_value("memory.enabled", False, "root", "acme")
        self.assertEqual(summarizer.fold_thread("thr_x", "acme", "u")["reason"], "memory_disabled")
        self.assertEqual(summarizer.fold_thread("thr_x", "beta", "u")["reason"], "unknown_thread")


# --------------------------------------------------------------------------- planner binding
class PlannerBindingTests(unittest.TestCase):
    def _run(self, n: int, tenant: str) -> list[str]:
        from harness.context import new_context
        from harness.graph import GraphNode, RunGraph
        from harness.planner import _run_wave
        from harness.types import TaskStatus

        graph = RunGraph()
        for i in range(n):
            graph.add_node(GraphNode(id=f"n{i}", agent="a", description=f"t{i}", original=f"t{i}"))
        ctx = new_context()
        ctx.graph = graph
        ctx.extra["tenant"] = tenant
        seen: list[str] = []

        def fake(task, query, wctx):
            seen.append(tenancy.current())
            task.status, task.result = TaskStatus.COMPLETED, "ok"
            return task

        settings = SimpleNamespace(node_timeout_s=0.0, parallel_workers=4)
        with patch("harness.planner._execute_with_heal", side_effect=fake), patch("harness.planner.get_settings", return_value=settings):
            _run_wave(list(graph.nodes.values()), "q", ctx)
        return seen

    def test_a_single_node_runs_in_the_requests_tenant(self) -> None:
        self.assertEqual(self._run(1, "acme"), ["acme"])
        self.assertEqual(tenancy.current(), "default")

    def test_parallel_pool_threads_are_bound_to_the_requests_tenant(self) -> None:
        self.assertEqual(self._run(4, "acme"), ["acme"] * 4)
        self.assertEqual(self._run(3, "beta"), ["beta"] * 3)
        self.assertEqual(tenancy.current(), "default")

    def test_an_eval_context_runs_as_default(self) -> None:
        self.assertEqual(self._run(2, "default"), ["default"] * 2)


# --------------------------------------------------------------------------- knowledge base and skills
class KbIsolationTests(TempState):
    def setUp(self) -> None:
        super().setUp()
        self._kb = patch.dict(os.environ, {"ORION_KB_DIR": str(self.root / "kb")})
        self._kb.start()
        self._noindex = patch("kb.ingest._try_index", return_value=False)
        self._noindex.start()

    def tearDown(self) -> None:
        self._noindex.stop()
        self._kb.stop()
        super().tearDown()

    def _ingest(self, tenant: str, text: str, name: str = "notes.txt") -> None:
        from kb.ingest import ingest_bytes

        with tenancy.use(tenant):
            ingest_bytes(name, text.encode(), uploaded_by="u")

    def test_each_tenant_has_its_own_directory(self) -> None:
        from kb import store

        self.assertEqual(store.root(), self.root / "kb")
        with tenancy.use("acme"):
            self.assertEqual(store.root(), self.root / "kb" / "tenants" / "acme")
            self.assertEqual(store.collection_name(), "kb_live__acme")
        self.assertEqual(store.collection_name(), "kb_live")

    def test_uploads_and_search_never_cross_tenants(self) -> None:
        from kb.ingest import lexical_search
        from kb.store import list_resources

        self._ingest("acme", "the acme launch codename is falcon")
        self._ingest("beta", "the beta roadmap mentions osprey")
        with tenancy.use("acme"):
            self.assertEqual(len(list_resources()), 1)
            self.assertTrue(lexical_search("falcon"))
            self.assertEqual(lexical_search("osprey"), [])
        with tenancy.use("beta"):
            self.assertTrue(lexical_search("osprey"))
            self.assertEqual(lexical_search("falcon"), [])
        self.assertEqual(list_resources(), [])  # the default tenant sees neither
        self.assertEqual(lexical_search("falcon"), [])

    def test_delete_is_scoped_to_the_tenant(self) -> None:
        from kb.store import delete_resource, list_resources

        self._ingest("acme", "acme only")
        with tenancy.use("acme"):
            rid = list_resources()[0]["id"]
        with tenancy.use("beta"):
            self.assertFalse(delete_resource(rid))
        with tenancy.use("acme"):
            self.assertEqual(len(list_resources()), 1)
            self.assertTrue(delete_resource(rid))

    def test_table_names_are_per_tenant(self) -> None:
        from kb.store import kb_table_names

        self._ingest("acme", "id,amount\n1,10\n2,20\n", "sales.csv")
        with tenancy.use("acme"):
            self.assertIn("kb_sales", kb_table_names())
        with tenancy.use("beta"):
            self.assertNotIn("kb_sales", kb_table_names())
        self._ingest("beta", "id,amount\n9,90\n", "sales.csv")
        with tenancy.use("beta"):
            self.assertIn("kb_sales", kb_table_names())  # same name, a different table: no "_2" suffix leaks the other tenant


class SkillIsolationTests(TempState):
    PROCEDURE = "Call schema_lookup first, then build the sql_spec from the verified column names."

    def setUp(self) -> None:
        super().setUp()
        from harness import skills

        self._patch = patch.multiple(skills, SKILLS_DIR=self.root / "skills", ARCHIVE_DIR=self.root / "skills" / "_archive")
        self._patch.start()
        self.skills = skills
        skills._STORES.clear()

    def tearDown(self) -> None:
        self.skills._STORES.clear()
        self._patch.stop()
        super().tearDown()

    def test_each_tenant_gets_its_own_store(self) -> None:
        default, acme = self.skills.default_store(), self.skills.default_store("acme")
        self.assertEqual(default.root, self.root / "skills")
        self.assertEqual(acme.root, self.root / "skills" / "tenants" / "acme")
        self.assertIs(self.skills.default_store("acme"), acme)  # one instance per tenant, so hit batching works

    def test_the_bound_tenant_selects_the_store(self) -> None:
        with tenancy.use("acme"):
            self.assertEqual(self.skills.default_store().root, self.root / "skills" / "tenants" / "acme")

    def test_a_skill_saved_by_one_tenant_is_invisible_to_the_others(self) -> None:
        self.skills.default_store("acme").create(name="acme join check", description="check joins", procedure=self.PROCEDURE, scope="global")
        self.assertEqual([s.slug for s in self.skills.default_store("acme").list()], ["acme-join-check"])
        self.assertEqual(self.skills.default_store("beta").list(), [])
        self.assertEqual(self.skills.default_store().list(), [])
        self.assertEqual(self.skills.default_store("beta").select("check joins", agent_id="hr", k=3), [])

    def test_a_skill_cannot_be_named_after_the_tenants_directory(self) -> None:
        with self.assertRaises(ValueError):
            self.skills.default_store().path_for("tenants")

    def test_the_default_stores_listing_ignores_tenant_directories(self) -> None:
        self.skills.default_store("acme").create(name="only acme", description="d", procedure=self.PROCEDURE, scope="global")
        self.assertEqual(self.skills.default_store().list(), [])


# --------------------------------------------------------------------------- agents and routing
class AgentIsolationTests(TempState):
    def _lab_agent(self, tenant: str, name: str = "Helper"):
        from harness.types import AgentStatus
        from lab.synthesizer import synthesize

        card = synthesize({"name": name, "job": f"answers {tenant} questions", "entities": "", "tools": "default", "mode": "card"}, created_by="t", tenant=tenant)
        from harness.agent_registry import get_registry

        get_registry().set_status(card.id, AgentStatus.ACTIVE, actor="t")
        return get_registry().get(card.id)

    def _ctx(self, tenant: str, **kwargs):
        from harness.context import new_context

        ctx = new_context(**kwargs)
        ctx.extra.update({"mode": "product", "tenant": tenant})
        return ctx

    def test_a_synthesized_agent_is_stamped_with_its_tenant(self) -> None:
        with lab_workspace():
            card = self._lab_agent("acme")
            self.assertEqual(card.metadata["tenant"], "acme")
            self.assertEqual(tenancy.card_owner(card), "acme")

    def test_an_invalid_tenant_cannot_create_an_agent(self) -> None:
        from lab.synthesizer import synthesize

        with lab_workspace(), self.assertRaises(tenancy.TenantError):
            synthesize({"name": "x", "job": "y"}, created_by="t", tenant="../x")

    def test_routing_hides_another_tenants_agent(self) -> None:
        from harness.errors import RoutingError
        from harness.router import shortlist

        with lab_workspace() as (registry, _), installed_runtime(FakeGateway(), agents=registry):
            card = self._lab_agent("acme")
            self.assertEqual([c.id for c in shortlist(self._ctx("acme", pin_agent=card.id), "q")], [card.id])
            with self.assertRaises(RoutingError):
                shortlist(self._ctx("beta", pin_agent=card.id), "q")
            with self.assertRaises(RoutingError):
                shortlist(self._ctx("default", pin_agent=card.id), "q")
            self.assertEqual([c.id for c in shortlist(self._ctx("acme", allowed_agents=[card.id]), "q")], [card.id])
            with self.assertRaises(RoutingError):
                shortlist(self._ctx("beta", allowed_agents=[card.id]), "q")

    def test_the_candidate_pool_excludes_other_tenants_agents(self) -> None:
        from harness.router import shortlist

        with lab_workspace() as (registry, _), installed_runtime(FakeGateway(), agents=registry):
            acme, beta = self._lab_agent("acme", "A"), self._lab_agent("beta", "B")
            with patch("retrieval.embeddings.get_embedding", side_effect=RuntimeError("no embeddings")):
                ids = {c.id for c in shortlist(self._ctx("acme"), "answers questions", k=64)}
            self.assertIn(acme.id, ids)
            self.assertNotIn(beta.id, ids)
            self.assertTrue(any(registry.get(i).is_builtin for i in ids))  # built-ins are shared

    def test_builtin_agents_can_be_pinned_by_every_tenant(self) -> None:
        from harness.router import shortlist

        with lab_workspace() as (registry, _), installed_runtime(FakeGateway(), agents=registry):
            builtin = next(c for c in registry.all() if c.is_builtin and c.id != "orchestrator")
            for tenant in ("default", "acme", "beta"):
                self.assertEqual([c.id for c in shortlist(self._ctx(tenant, pin_agent=builtin.id), "q")], [builtin.id])

    def test_a_legacy_unstamped_custom_agent_belongs_to_the_default_tenant(self) -> None:
        from harness.errors import RoutingError
        from harness.router import shortlist
        from harness.types import AgentCard

        legacy = AgentCard(id="legacy_x", name="Legacy", description="d", intent="i", tools=["retrieve"], is_custom=True, is_builtin=False)
        with lab_workspace() as (registry, _), installed_runtime(FakeGateway(), agents=registry):
            registry.register(legacy, actor="t")
            self.assertEqual([c.id for c in shortlist(self._ctx("default", pin_agent="legacy_x"), "q")], ["legacy_x"])
            with self.assertRaises(RoutingError):
                shortlist(self._ctx("acme", pin_agent="legacy_x"), "q")

    def test_eval_mode_routing_is_unchanged(self) -> None:
        from harness.context import new_context
        from harness.router import shortlist

        with lab_workspace() as (registry, _), installed_runtime(FakeGateway(), agents=registry):
            card = self._lab_agent("acme")
            ctx = new_context(pin_agent=card.id)  # no product mode: the benchmark path never filters by tenant
            self.assertEqual([c.id for c in shortlist(ctx, "q")], [card.id])

    def test_pinning_from_chat_treats_another_tenants_agent_as_unknown(self) -> None:
        from backend import service
        from backend.errors import ApiError

        with lab_workspace():
            card = self._lab_agent("acme")
            self.assertEqual(service.parse_pin(f"@{card.id} hello", None, None, "acme")[1], card.id)
            with self.assertRaises(ApiError) as raised:
                service.parse_pin(f"@{card.id} hello", None, None, "beta")
            self.assertEqual(raised.exception.code, "unknown_agent")

    def test_the_lab_conversation_is_scoped(self) -> None:
        from lab.synthesizer import LabAgent

        with lab_workspace():
            card = self._lab_agent("acme")
            lab = LabAgent()
            self.assertIn(card.id, lab.process_message("u1", "list", "acme"))
            self.assertNotIn(card.id, lab.process_message("u2", "list", "beta"))
            self.assertIn("Unknown", lab.process_message("u2", f"evaluate {card.id}", "beta"))
            self.assertIn("Unknown", lab.process_message("u2", f"deploy {card.id}", "beta"))
            self.assertIn("Cannot delete", lab.process_message("u2", f"delete {card.id}", "beta"))
            self.assertIn("Unknown", lab.process_message("u2", f"use {card.id}", "beta"))
            from harness.agent_registry import get_registry

            self.assertIsNotNone(get_registry().get(card.id))  # the delete did not happen

    def test_the_quality_queue_refuses_to_repair_another_tenants_agent(self) -> None:
        from harness import quality

        with lab_workspace():
            card = self._lab_agent("acme")
            item = quality.enqueue(tenant="beta", request_id="r1", agent_id=card.id, query="q", answer="a", judge={}, reason="weak")
            with self.assertRaises(LookupError):
                quality.apply_repair(item, tenant="beta", actor="admin-b")

    def test_sampling_uses_the_tenants_own_rate(self) -> None:
        from harness import quality

        ss.set_value("quality.sample_rate", 1.0, "root", "acme")
        with patch("harness.quality.threading.Thread") as thread:
            self.assertTrue(quality.maybe_sample(tenant="acme", request_id="r", query="q", answer="a", agents=[]))
            self.assertFalse(quality.maybe_sample(tenant="beta", request_id="r", query="q", answer="a", agents=[]))
        self.assertEqual(thread.call_count, 1)


# --------------------------------------------------------------------------- connectors
class ConnectorTenancyTests(TempState):
    def _registry(self, **tenants):
        from harness.connectors import ConnectorConfig, ConnectorRegistry

        (self.root / "data").mkdir(exist_ok=True)
        (self.root / "data" / "sales.csv").write_text("id,amount\n1,10\n2,20\n", encoding="utf-8")
        configs = []
        for cid, tenant_list in tenants.items():
            entry = {"id": cid, "type": "files", "path": str(self.root / "data"), "entity": cid.upper()}
            if tenant_list is not None:
                entry["tenants"] = tenant_list
            configs.append(ConnectorConfig.from_dict(entry))
        registry = ConnectorRegistry(configs)
        self.addCleanup(registry.clear)
        return registry

    def test_an_unlisted_connector_is_for_the_default_tenant_only(self) -> None:
        reg = self._registry(warehouse=None)
        self.assertEqual([c.id for c in reg.for_tenant("default")], ["warehouse"])
        self.assertEqual(reg.for_tenant("acme"), [])

    def test_explicit_lists_and_the_wildcard(self) -> None:
        reg = self._registry(shared=["*"], acme_only=["acme"], pair=["acme", "beta"])
        self.assertEqual({c.id for c in reg.for_tenant("acme")}, {"shared", "acme_only", "pair"})
        self.assertEqual({c.id for c in reg.for_tenant("beta")}, {"shared", "pair"})
        self.assertEqual({c.id for c in reg.for_tenant("default")}, {"shared"})  # naming acme does not include default

    def test_a_hidden_connector_looks_exactly_like_a_missing_one(self) -> None:
        from harness.errors import SchemaInvalidError

        reg = self._registry(acme_only=["acme"])
        with self.assertRaises(SchemaInvalidError) as hidden:
            reg.resolve("acme_only.sales", "beta")
        with self.assertRaises(SchemaInvalidError) as missing:
            reg.resolve("nonexistent.sales", "beta")
        self.assertEqual(str(hidden.exception).replace("acme_only", "X"), str(missing.exception).replace("nonexistent", "X"))
        self.assertNotIn("acme_only", str(hidden.exception).split("Connectors:")[1])
        self.assertEqual(reg.resolve("acme_only.sales", "acme")[1], "sales")

    def test_prompt_text_and_table_refs_list_only_the_tenants_sources(self) -> None:
        reg = self._registry(acme_only=["acme"], beta_only=["beta"])
        self.assertIn("acme_only.sales", reg.prompt_text(None, tenant="acme"))
        self.assertNotIn("beta_only", reg.prompt_text(None, tenant="acme"))
        self.assertEqual(reg.table_refs("beta"), ["beta_only.sales"])
        self.assertEqual(reg.prompt_text(None, tenant="default"), "")

    def test_the_bound_tenant_is_the_default_scope(self) -> None:
        reg = self._registry(acme_only=["acme"])
        with tenancy.use("acme"):
            self.assertEqual(len(reg.for_tenant()), 1)
        self.assertEqual(reg.for_tenant(), [])

    def test_sql_spec_uses_the_requests_tenant(self) -> None:
        from harness.context import new_context
        from harness.errors import SchemaInvalidError

        reg = self._registry(acme_only=["acme"])
        ctx = new_context()
        ctx.extra.update({"mode": "product", "tenant": "beta"})
        with self.assertRaises(SchemaInvalidError):
            reg.sql_spec({"table": "acme_only.sales"}, ctx)
        with self.assertRaises(SchemaInvalidError):
            reg.schema_lookup({"table": "acme_only.sales"}, ctx)
        ctx.extra["tenant"] = "acme"
        self.assertEqual(reg.schema_lookup({"table": "acme_only.sales"}, ctx)["table"], "acme_only.sales")

    def test_config_validation(self) -> None:
        from harness.connectors import ConnectorConfig

        base = {"id": "x", "type": "files", "path": ".", "entity": "X"}
        self.assertEqual(ConnectorConfig.from_dict({**base, "tenants": "acme"}).tenants, ["acme"])
        self.assertEqual(ConnectorConfig.from_dict({**base, "tenants": ["acme", "acme", "*"]}).tenants, ["acme", "*"])
        for bad in (["../x"], ["Acme"], ["a b"]):
            with self.assertRaises(ValueError):
                ConnectorConfig.from_dict({**base, "tenants": bad})

    def test_describe_reports_the_effective_tenants(self) -> None:
        reg = self._registry(a=None, b=["acme"])
        described = {c.id: c.describe()["tenants"] for c in reg.all()}
        self.assertEqual(described, {"a": ["default"], "b": ["acme"]})


# --------------------------------------------------------------------------- HTTP: users, settings, traces, KB, agents
class TenantApiTests(ApiTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.headers = {}
        for name, tenant, role in (
            ("alice-a", "acme", "limited"),
            ("admin-a", "acme", "super_user"),
            ("bob-b", "beta", "limited"),
            ("admin-b", "beta", "super_user"),
        ):
            res = cls.client.post(
                "/admin/users",
                headers=cls().admin(),
                json={"username": name, "password": USER_PASSWORD, "role": role, "tenant": tenant},
            )
            assert res.status_code in (201, 409), res.text
        cls.headers = {name: cls().login(name, USER_PASSWORD) for name in ("alice-a", "admin-a", "bob-b", "admin-b")}

    def setUp(self) -> None:
        from backend.ratelimit import login_throttle

        login_throttle.reset()
        ss.clear_cache()

    def h(self, name: str) -> dict[str, str]:
        """Auth headers for a tenant user; `admin` (or `root`) is the platform administrator."""
        return self.admin() if name in {"admin", "root"} else self.headers[name]

    # ---- users
    def test_sessions_carry_the_users_tenant(self) -> None:
        self.assertEqual(self.client.get("/auth/me", headers=self.h("alice-a")).json()["tenant"], "acme")
        self.assertEqual(self.client.get("/auth/me", headers=self.admin()).json()["tenant"], "default")

    def test_a_tenant_admin_sees_only_their_own_users(self) -> None:
        names = {u["username"] for u in self.client.get("/admin/users", headers=self.h("admin-a")).json()}
        self.assertTrue({"alice-a", "admin-a"} <= names)
        self.assertFalse({"bob-b", "admin-b", "admin"} & names)

    def test_the_platform_admin_sees_every_tenant(self) -> None:
        rows = {u["username"]: u["tenant"] for u in self.client.get("/admin/users", headers=self.admin()).json()}
        self.assertEqual((rows["alice-a"], rows["bob-b"], rows["admin"]), ("acme", "beta", "default"))

    def test_a_tenant_admin_cannot_change_the_role_of_another_tenants_user(self) -> None:
        res = self.client.put("/admin/users/bob-b/role", headers=self.h("admin-a"), json={"role": "super_user"})
        self.assertEqual(res.status_code, 404)
        me = self.client.get("/auth/me", headers=self.h("bob-b")).json()
        self.assertEqual(me["role"], "limited")

    def test_a_tenant_admin_cannot_deactivate_another_tenants_user(self) -> None:
        res = self.client.delete("/admin/users/bob-b", headers=self.h("admin-a"))
        self.assertEqual(res.status_code, 404)
        self.assertEqual(self.client.get("/auth/me", headers=self.h("bob-b")).status_code, 200)

    def test_a_tenant_admin_cannot_touch_the_platform_admin(self) -> None:
        self.assertEqual(self.client.put("/admin/users/admin/role", headers=self.h("admin-a"), json={"role": "limited"}).status_code, 404)
        self.assertEqual(self.client.delete("/admin/users/admin", headers=self.h("admin-a")).status_code, 404)

    def test_a_tenant_admin_can_manage_their_own_tenant(self) -> None:
        created = self.client.post(
            "/admin/users", headers=self.h("admin-a"), json={"username": "carol-a", "password": USER_PASSWORD, "role": "limited"}
        )
        self.assertEqual((created.status_code, created.json()["tenant"]), (201, "acme"))
        self.assertEqual(self.client.put("/admin/users/carol-a/role", headers=self.h("admin-a"), json={"role": "super_user"}).status_code, 200)
        self.assertEqual(self.client.delete("/admin/users/carol-a", headers=self.h("admin-a")).status_code, 200)

    def test_a_tenant_admin_cannot_create_users_in_another_tenant(self) -> None:
        res = self.client.post(
            "/admin/users",
            headers=self.h("admin-a"),
            json={"username": "mallory", "password": USER_PASSWORD, "role": "super_user", "tenant": "beta"},
        )
        self.assertEqual(res.status_code, 403)
        listing = {u["username"] for u in self.client.get("/admin/users", headers=self.admin()).json()}
        self.assertNotIn("mallory", listing)

    def test_a_tenant_admin_cannot_create_a_platform_admin(self) -> None:
        res = self.client.post(
            "/admin/users",
            headers=self.h("admin-a"),
            json={"username": "root2", "password": USER_PASSWORD, "role": "super_user", "tenant": "default"},
        )
        self.assertEqual(res.status_code, 403)

    def test_an_invalid_tenant_id_is_rejected(self) -> None:
        for bad in ("../x", "Acme", "a b", "x" * 60):
            res = self.client.post(
                "/admin/users", headers=self.admin(), json={"username": "zed", "password": USER_PASSWORD, "tenant": bad}
            )
            self.assertIn(res.status_code, (400, 422), bad)
        self.assertNotIn("zed", {u["username"] for u in self.client.get("/admin/users", headers=self.admin()).json()})

    def test_the_platform_admin_can_create_users_in_any_tenant(self) -> None:
        res = self.client.post(
            "/admin/users", headers=self.admin(), json={"username": "dave-c", "password": USER_PASSWORD, "tenant": "corp-c"}
        )
        self.assertIn(res.status_code, (201, 409))
        row = next(u for u in self.client.get("/admin/users", headers=self.admin()).json() if u["username"] == "dave-c")
        self.assertEqual(row["tenant"], "corp-c")

    # ---- deployment-wide resources
    def test_mcp_and_connector_admin_is_platform_only(self) -> None:
        for method, path in (
            ("get", "/admin/mcp"),
            ("post", "/admin/mcp/reload"),
            ("get", "/admin/connectors"),
            ("post", "/admin/connectors/reload"),
            ("post", "/admin/connectors/x/test"),
        ):
            res = getattr(self.client, method)(path, headers=self.h("admin-a"))
            self.assertEqual(res.status_code, 403, path)
        self.assertEqual(self.client.get("/admin/connectors", headers=self.admin()).status_code, 200)
        self.assertEqual(self.client.get("/admin/mcp", headers=self.admin()).status_code, 200)

    # ---- settings
    def test_admin_settings_are_per_tenant_and_take_effect_at_runtime(self) -> None:
        put = self.client.put("/admin/settings/web_search.max_results", headers=self.h("admin-a"), json={"value": 2})
        self.assertEqual(put.status_code, 200, put.text)
        row = lambda who: next(r for r in self.client.get("/admin/settings", headers=self.h(who)).json() if r["key"] == "web_search.max_results")  # noqa: E731
        self.assertEqual((row("admin-a")["value"], row("admin-a")["source"]), (2, "admin"))
        self.assertEqual((row("admin-b")["value"], row("admin-b")["source"]), (5, "default"))
        self.assertEqual(row("admin")["value"], 5)
        self.assertEqual(ss.effective("web_search.max_results", "acme"), 2)
        with tenancy.use("beta"):
            self.assertEqual(ss.effective("web_search.max_results"), 5)
        self.client.delete("/admin/settings/web_search.max_results", headers=self.h("admin-a"))

    def test_a_limited_user_cannot_change_settings(self) -> None:
        res = self.client.put("/admin/settings/web_search.max_results", headers=self.h("alice-a"), json={"value": 1})
        self.assertEqual(res.status_code, 403)

    # ---- traces
    def _traces(self):
        from harness.trace import Tracer

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        traces = Path(tmp.name) / "traces"
        traces.mkdir()
        tracer = Tracer(traces_dir=traces)
        stack = contextlib.ExitStack()
        stack.enter_context(patch("backend.routes.traces.get_tracer", return_value=tracer))
        stack.enter_context(patch("backend.routes.traces.TRACES_DIR", traces))
        self.addCleanup(stack.close)
        return tracer, traces

    def _trace(self, tracer, traces: Path, rid: str, tenant: str, user: str, meta: bool = True) -> None:
        (traces / f"{rid}.jsonl").write_text(json.dumps({"kind": "tool", "name": "x", "extra": {}}) + "\n", encoding="utf-8")
        if meta:
            tracer.write_meta(rid, {"request_id": rid, "tenant": tenant, "user": user})

    def test_traces_are_tenant_scoped_for_admins_too(self) -> None:
        tracer, traces = self._traces()
        self._trace(tracer, traces, "REQ-A1", "acme", "alice-a")
        status = lambda who, path="/traces/REQ-A1": self.client.get(path, headers=self.h(who)).status_code  # noqa: E731
        self.assertEqual(status("alice-a"), 200)
        self.assertEqual(status("admin-a"), 200)  # an admin reads their tenant's traces
        self.assertEqual(status("bob-b"), 404)
        self.assertEqual(status("admin-b"), 404)  # another tenant's admin must not
        self.assertEqual(self.client.get("/traces/REQ-A1", headers=self.admin()).status_code, 200)  # the platform admin may
        for path in ("/traces/REQ-A1/export", "/requests/REQ-A1/graph"):
            self.assertEqual(status("admin-b", path), 404, path)

    def test_a_trace_without_metadata_is_platform_only(self) -> None:
        tracer, traces = self._traces()
        self._trace(tracer, traces, "REQ-OLD", "", "", meta=False)
        self.assertEqual(self.client.get("/traces/REQ-OLD", headers=self.h("admin-a")).status_code, 404)
        self.assertEqual(self.client.get("/traces/REQ-OLD", headers=self.admin()).status_code, 200)

    def test_trace_listings_are_scoped_to_the_tenant(self) -> None:
        tracer, traces = self._traces()
        self._trace(tracer, traces, "REQ-L1", "acme", "alice-a")
        self._trace(tracer, traces, "REQ-L2", "beta", "bob-b")
        ids = lambda who: {r["request_id"] for r in self.client.get("/traces", headers=self.h(who)).json()}  # noqa: E731
        self.assertEqual(ids("admin-a"), {"REQ-L1"})
        self.assertEqual(ids("admin-b"), {"REQ-L2"})

    # ---- knowledge base
    def test_the_knowledge_base_is_isolated_over_http(self) -> None:
        upload = lambda who, name, body: self.client.post(  # noqa: E731
            "/ingest", headers=self.h(who), files=[("files", (name, body, "text/plain"))]
        )
        with patch("kb.ingest._try_index", return_value=False):
            self.assertEqual(upload("alice-a", "acme-notes.txt", b"acme secret plan falcon").status_code, 200)
            self.assertEqual(upload("bob-b", "beta-notes.txt", b"beta secret plan osprey").status_code, 200)
        names = lambda who: [r["filename"] for r in self.client.get("/kb", headers=self.h(who)).json()["resources"]]  # noqa: E731
        self.assertEqual(names("alice-a"), ["acme-notes.txt"])
        self.assertEqual(names("admin-a"), ["acme-notes.txt"])
        self.assertEqual(names("bob-b"), ["beta-notes.txt"])
        self.assertNotIn("acme-notes.txt", names("admin"))
        root = Path(os.environ["ORION_KB_DIR"])
        self.assertTrue((root / "tenants" / "acme" / "catalog.json").exists())
        self.assertTrue((root / "tenants" / "beta" / "catalog.json").exists())

    def test_deleting_another_tenants_document_is_a_404(self) -> None:
        with patch("kb.ingest._try_index", return_value=False):
            self.client.post("/ingest", headers=self.h("alice-a"), files=[("files", ("doomed.txt", b"keep me", "text/plain"))])
        rid = next(r["id"] for r in self.client.get("/kb", headers=self.h("alice-a")).json()["resources"] if r["filename"] == "doomed.txt")
        self.assertEqual(self.client.delete(f"/kb/{rid}", headers=self.h("admin-b")).status_code, 404)
        self.assertEqual(self.client.delete(f"/kb/{rid}", headers=self.h("alice-a")).status_code, 200)

    # ---- agents and the Lab
    def test_lab_agents_belong_to_the_creating_tenant(self) -> None:
        with lab_workspace():
            created = self.client.post(
                "/lab/agents", headers=self.h("admin-a"), json={"name": "Acme helper", "job": "answers acme questions"}
            )
            self.assertEqual(created.status_code, 201, created.text)
            aid = created.json()["id"]
            ids = lambda who, path="/lab/agents": {a["id"] for a in self.client.get(path, headers=self.h(who) if who != "root" else self.admin()).json()}  # noqa: E731
            self.assertIn(aid, ids("admin-a"))
            self.assertNotIn(aid, ids("admin-b"))
            self.assertIn(aid, ids("root"))  # the platform admin sees across tenants
            for method, path in (
                ("get", f"/lab/agents/{aid}"),
                ("post", f"/lab/agents/{aid}/evaluate"),
                ("get", f"/lab/agents/{aid}/evaluation"),
                ("post", f"/lab/agents/{aid}/deploy"),
                ("get", f"/agents/{aid}"),
                ("get", f"/agents/{aid}/versions"),
            ):
                self.assertEqual(getattr(self.client, method)(path, headers=self.h("admin-b")).status_code, 404, path)
            self.assertEqual(
                self.client.post(f"/agents/{aid}/status", headers=self.h("admin-b"), json={"status": "retired"}).status_code, 404
            )
            self.assertEqual(
                self.client.post(f"/agents/{aid}/rollback", headers=self.h("admin-b"), json={"version": 1}).status_code, 404
            )

    def test_the_agent_catalogue_hides_other_tenants_agents_but_shares_builtins(self) -> None:
        with lab_workspace() as (registry, _):
            from harness.types import AgentStatus

            created = self.client.post("/lab/agents", headers=self.h("admin-a"), json={"name": "Catalogue", "job": "j"})
            aid = created.json()["id"]
            registry.set_status(aid, AgentStatus.ACTIVE, actor="t")
            for who, expect in (("alice-a", True), ("admin-a", True), ("bob-b", False), ("admin-b", False)):
                listed = {a["id"] for a in self.client.get("/agents", headers=self.h(who)).json()}
                self.assertEqual(aid in listed, expect, who)
                self.assertTrue(any(a["builtin"] for a in self.client.get("/agents", headers=self.h(who)).json()), who)

    def test_chat_cannot_pin_another_tenants_agent(self) -> None:
        with lab_workspace() as (registry, _):
            from harness.types import AgentStatus

            aid = self.client.post("/lab/agents", headers=self.h("admin-a"), json={"name": "Pinned", "job": "j"}).json()["id"]
            registry.set_status(aid, AgentStatus.ACTIVE, actor="t")
            res = self.client.post("/chat", headers=self.h("bob-b"), json={"message": f"@{aid} hello"})
            self.assertEqual(res.status_code, 400)
            self.assertEqual(res.json()["error"]["code"], "unknown_agent")
            res = self.client.post("/chat", headers=self.h("bob-b"), json={"message": "hello", "pin_agent": aid})
            self.assertEqual(res.status_code, 400)


# --------------------------------------------------------------------------- sandbox backends
class SandboxBackendTests(TempState):
    def setUp(self) -> None:
        super().setUp()
        self.agent = self.root / "agent.py"
        self.agent.write_text("def answer(task):\n    return 'hi ' + task\n", encoding="utf-8")

    def test_docker_command_pins_every_isolation_flag(self) -> None:
        from sandbox.backends import docker_command

        cmd = docker_command(image="img:1", name="n1", module_path=self.agent, task="t", memory_mb=256, cpu_s=7)
        joined = " ".join(cmd)
        for fragment in (
            "--network none", "--read-only", "--cap-drop ALL", "--security-opt no-new-privileges", "--user 65534:65534",
            "--pids-limit 64", "--memory 256m", "--memory-swap 256m", "--ulimit cpu=7:7", "--cpus 1", "--rm",
        ):
            self.assertIn(fragment, joined)
        self.assertEqual(cmd[-5:], ["img:1", "python", "/runner/worker_loop.py", "/agent/agent.py", "t"])
        mounts = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-v"]
        self.assertEqual(len(mounts), 2)
        self.assertTrue(all(m.endswith(":ro") for m in mounts), mounts)  # nothing the agent can write to
        self.assertNotIn("--privileged", cmd)
        self.assertNotIn("--network host", joined)

    def test_the_task_is_one_argv_element_never_a_shell_string(self) -> None:
        from sandbox.backends import docker_command

        task = "x; rm -rf / && $(whoami) `id`"
        cmd = docker_command(image="img", name="n", module_path=self.agent, task=task, memory_mb=64, cpu_s=1)
        self.assertEqual(cmd[-1], task)
        self.assertNotIn("sh", cmd[cmd.index("img") + 1 : cmd.index("img") + 2])

    def _docker(self, replies):
        calls: list[list[str]] = []

        def fake(cmd, **kwargs):
            calls.append(list(cmd))
            reply = replies(cmd, kwargs) if callable(replies) else replies
            if isinstance(reply, Exception):
                raise reply
            return reply

        return calls, fake

    def test_a_successful_run_returns_stdout(self) -> None:
        from sandbox.backends import DockerBackend

        calls, fake = self._docker(subprocess.CompletedProcess([], 0, stdout="hi world\n", stderr=""))
        outcome = DockerBackend("img", exec_fn=fake).run(self.agent, "world", memory_mb=64, cpu_s=2, wall_s=5)
        self.assertEqual((outcome.code, outcome.stdout.strip(), outcome.timed_out), (0, "hi world", False))
        self.assertEqual(calls[0][:3], ["docker", "run", "--rm"])

    def test_a_timeout_kills_the_container_by_name(self) -> None:
        from sandbox.backends import DockerBackend

        def reply(cmd, kwargs):
            if cmd[1] == "run":
                return subprocess.TimeoutExpired(cmd, 1)
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        calls, fake = self._docker(reply)
        outcome = DockerBackend("img", exec_fn=fake).run(self.agent, "t", memory_mb=64, cpu_s=2, wall_s=1)
        self.assertTrue(outcome.timed_out)
        run_cmd, kill_cmd = calls[0], calls[1]
        self.assertEqual(kill_cmd[:2], ["docker", "kill"])
        self.assertEqual(kill_cmd[2], run_cmd[run_cmd.index("--name") + 1])

    def test_docker_own_failures_are_sandbox_errors_not_agent_crashes(self) -> None:
        from harness.errors import SandboxError
        from sandbox.backends import DockerBackend

        for code in (125, 126, 127):
            _, fake = self._docker(subprocess.CompletedProcess([], code, stdout="", stderr="no such image"))
            with self.assertRaises(SandboxError):
                DockerBackend("img", exec_fn=fake).run(self.agent, "t", memory_mb=64, cpu_s=2, wall_s=5)

    def test_an_agent_crash_is_reported_as_an_exit_code(self) -> None:
        from sandbox.backends import DockerBackend

        _, fake = self._docker(subprocess.CompletedProcess([], 1, stdout="", stderr="run error: boom"))
        outcome = DockerBackend("img", exec_fn=fake).run(self.agent, "t", memory_mb=64, cpu_s=2, wall_s=5)
        self.assertEqual((outcome.code, outcome.timed_out), (1, False))

    def test_availability_checks(self) -> None:
        from sandbox.backends import DockerBackend

        with patch("sandbox.backends.shutil.which", return_value=None):
            ok, why = DockerBackend().available()
            self.assertFalse(ok)
            self.assertIn("not installed", why)
        with patch("sandbox.backends.shutil.which", return_value="docker"):
            _, down = self._docker(subprocess.CompletedProcess([], 1, stdout="", stderr="cannot connect"))
            ok, why = DockerBackend(exec_fn=down).available()
            self.assertFalse(ok)
            self.assertIn("not running", why)
            _, up = self._docker(subprocess.CompletedProcess([], 0, stdout="27.0", stderr=""))
            self.assertEqual(DockerBackend(exec_fn=up).available(), (True, ""))
            _, broken = self._docker(OSError("pipe"))
            self.assertFalse(DockerBackend(exec_fn=broken).available()[0])

    def test_an_unavailable_docker_never_falls_back_to_the_weaker_backend(self) -> None:
        from harness.errors import SandboxError
        from sandbox.backends import get_backend
        from sandbox.exec_sandbox import run_generated_agent

        ss.set_value("sandbox.backend", "docker", "root")
        with patch("sandbox.backends.shutil.which", return_value=None):
            with self.assertRaises(SandboxError):
                get_backend()
            with self.assertRaises(SandboxError):
                run_generated_agent(self.agent, "x")

    def test_unknown_backend_names_are_refused(self) -> None:
        from harness.errors import SandboxError
        from sandbox.backends import get_backend

        with self.assertRaises(SandboxError):
            get_backend("firecracker")

    def test_the_default_backend_is_the_limited_subprocess(self) -> None:
        from sandbox.backends import SubprocessBackend, get_backend
        from sandbox.exec_sandbox import run_generated_agent

        self.assertIsInstance(get_backend(), SubprocessBackend)
        self.assertEqual(run_generated_agent(self.agent, "world"), "hi world")

    def test_run_generated_agent_uses_the_configured_docker_backend(self) -> None:
        from sandbox.backends import Outcome
        from sandbox.exec_sandbox import run_generated_agent

        fake = SimpleNamespace(run=lambda *a, **k: Outcome(0, "from container\n", "", False))
        with patch("sandbox.backends.get_backend", return_value=fake):
            self.assertEqual(run_generated_agent(self.agent, "x"), "from container")

    def test_the_static_gate_still_runs_before_any_backend(self) -> None:
        from harness.errors import SandboxError
        from sandbox.exec_sandbox import run_generated_agent

        bad = self.root / "bad.py"
        bad.write_text("import os\ndef answer(task):\n    return os.getcwd()\n", encoding="utf-8")
        backend = SimpleNamespace(run=lambda *a, **k: self.fail("backend must not be reached"))
        with patch("sandbox.backends.get_backend", return_value=backend), self.assertRaises(SandboxError):
            run_generated_agent(bad, "x")

    def test_timeouts_and_crashes_are_translated(self) -> None:
        from harness.errors import SandboxError
        from sandbox.backends import Outcome
        from sandbox.exec_sandbox import run_generated_agent

        with patch("sandbox.backends.get_backend", return_value=SimpleNamespace(run=lambda *a, **k: Outcome(-9, "", "", True))):
            with self.assertRaisesRegex(SandboxError, "timed out"):
                run_generated_agent(self.agent, "x")
        with patch("sandbox.backends.get_backend", return_value=SimpleNamespace(run=lambda *a, **k: Outcome(1, "", "boom", False))):
            with self.assertRaisesRegex(SandboxError, "crashed"):
                run_generated_agent(self.agent, "x")

    def test_settings_expose_the_image(self) -> None:
        self.assertEqual(ss.effective("sandbox.docker_image"), "python:3.12-slim")


# --------------------------------------------------------------------------- OIDC
def _keypair():
    from cryptography.hazmat.primitives.asymmetric import rsa

    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


class OidcCase(TempState):
    ISSUER = "https://idp.example.com"
    AUDIENCE = "orion-client"

    @classmethod
    def setUpClass(cls) -> None:
        cls.key = _keypair()
        cls.other_key = _keypair()

    def setUp(self) -> None:
        super().setUp()
        from harness import oidc

        self.oidc = oidc
        self.cfg = oidc.OidcConfig(issuer=self.ISSUER, audience=self.AUDIENCE)
        self.resolver = lambda token: self.key.public_key()

    def token(self, *, key=None, alg: str = "RS256", **overrides) -> str:
        import jwt

        claims = {
            "iss": self.ISSUER, "aud": self.AUDIENCE, "sub": "sub-1", "preferred_username": "erin",
            "exp": int(time.time()) + 600, "iat": int(time.time()),
            "tenant": "acme", "roles": ["staff"],
        }
        claims.update(overrides)
        claims = {k: v for k, v in claims.items() if v is not None}
        return jwt.encode(claims, key or self.key, algorithm=alg)


class OidcVerifyTests(OidcCase):
    def test_a_valid_token_yields_its_claims(self) -> None:
        claims = self.oidc.verify(self.token(), self.cfg, self.resolver)
        self.assertEqual((claims["sub"], claims["tenant"]), ("sub-1", "acme"))

    def test_expired_wrong_audience_wrong_issuer_and_bad_signature_are_rejected(self) -> None:
        cases = {
            "expired": self.token(exp=int(time.time()) - 3600),
            "audience": self.token(aud="someone-else"),
            "issuer": self.token(iss="https://evil.example.com"),
            "signature": self.token(key=self.other_key),
            "no_sub": self.token(sub=None),
            "no_exp": self.token(exp=None),
        }
        for label, token in cases.items():
            with self.assertRaises(self.oidc.OidcError, msg=label):
                self.oidc.verify(token, self.cfg, self.resolver)

    def test_unsigned_and_shared_secret_tokens_are_refused_before_any_key_lookup(self) -> None:
        import jwt

        def must_not_resolve(token):
            raise AssertionError("key lookup reached")

        claims = {"iss": self.ISSUER, "aud": self.AUDIENCE, "sub": "s", "exp": int(time.time()) + 600}
        for label, token in (
            ("none", jwt.encode(claims, None, algorithm="none")),
            ("hs256", jwt.encode(claims, "x" * 40, algorithm="HS256")),
        ):
            with self.assertRaisesRegex(self.oidc.OidcError, "algorithm", msg=label):
                self.oidc.verify(token, self.cfg, must_not_resolve)

    def test_the_public_key_cannot_be_used_as_an_hmac_secret(self) -> None:
        import jwt
        from cryptography.hazmat.primitives import serialization

        pem = self.key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        claims = {"iss": self.ISSUER, "aud": self.AUDIENCE, "sub": "s", "exp": int(time.time()) + 600}
        with contextlib.suppress(Exception):
            forged = jwt.encode(claims, pem, algorithm="HS256")
            with self.assertRaises(self.oidc.OidcError):
                self.oidc.verify(forged, self.cfg, self.resolver)

    def test_malformed_tokens(self) -> None:
        for bad in ("", "abc", "a.b", "a.b.c", "....", " "):
            with self.assertRaises(self.oidc.OidcError, msg=repr(bad)):
                self.oidc.verify(bad, self.cfg, self.resolver)

    def test_errors_never_echo_the_token(self) -> None:
        token = self.token(key=self.other_key)
        with self.assertRaises(self.oidc.OidcError) as raised:
            self.oidc.verify(token, self.cfg, self.resolver)
        self.assertNotIn(token[:20], raised.exception.safe_message)

    def test_configuration_comes_from_the_environment(self) -> None:
        self.assertIsNone(self.oidc.config())
        env = {"ORION_OIDC_ISSUER": "https://idp.example.com/", "ORION_OIDC_CLIENT_ID": "cid", "ORION_OIDC_ADMIN_VALUES": "a, b"}
        with patch.dict(os.environ, env):
            cfg = self.oidc.config()
        self.assertEqual((cfg.issuer, cfg.audience, cfg.admin_values), ("https://idp.example.com", "cid", ("a", "b")))
        with patch.dict(os.environ, {"ORION_OIDC_ISSUER": "https://x"}):
            self.assertIsNone(self.oidc.config())  # an issuer without a client id is not "enabled"

    def test_no_symmetric_algorithm_is_ever_allowed(self) -> None:
        self.assertFalse({"HS256", "HS384", "HS512", "none"} & set(self.oidc.ASYMMETRIC))


class OidcClaimTests(OidcCase):
    def test_roles_map_to_super_user_only_for_the_admin_value(self) -> None:
        ident = lambda **c: self.oidc.identity_from_claims({"sub": "s", "preferred_username": "erin", **c}, self.cfg)  # noqa: E731
        self.assertEqual(ident(roles=["orion-admin"])[1], "super_user")
        self.assertEqual(ident(roles="staff orion-admin")[1], "super_user")
        self.assertEqual(ident(roles=["ORION-ADMIN"])[1], "super_user")
        self.assertEqual(ident(roles=["staff"])[1], "limited")
        self.assertEqual(ident()[1], "limited")
        self.assertEqual(ident(roles=["orion-administrator"])[1], "limited")  # no substring matching

    def test_tenant_claim(self) -> None:
        ident = lambda **c: self.oidc.identity_from_claims({"sub": "s", "preferred_username": "erin", **c}, self.cfg)  # noqa: E731
        self.assertEqual(ident(tenant="Acme")[2], "acme")
        self.assertEqual(ident()[2], "default")
        for bad in ("../x", "a b", "x" * 60):
            with self.assertRaises(self.oidc.OidcError, msg=bad) as raised:
                ident(tenant=bad)
            self.assertEqual(raised.exception.status, 403)

    def test_username_claim_falls_back_and_is_validated(self) -> None:
        self.assertEqual(self.oidc.identity_from_claims({"sub": "s1", "email": "e@x.io"}, self.cfg)[0], "e@x.io")
        self.assertEqual(self.oidc.identity_from_claims({"sub": "subject-9"}, self.cfg)[0], "subject-9")
        for bad in ("a b", "x/y", "é", "a" * 70, "x"):
            with self.assertRaises(self.oidc.OidcError, msg=bad):
                self.oidc.identity_from_claims({"sub": "s", "preferred_username": bad, "email": ""}, self.cfg)


class OidcLoginTests(OidcCase):
    def login(self, **kw):
        return self.oidc.login(self.token(**kw), self.cfg, self.resolver)

    def test_a_first_login_provisions_a_passwordless_account(self) -> None:
        from backend import database as db

        session = self.login()
        self.assertEqual((session["username"], session["role"], session["tenant"]), ("erin", "limited", "acme"))
        row = db.get_user_row("erin")
        self.assertEqual((row["tenant"], row["password_hash"]), ("acme", db.EXTERNAL_HASH))
        self.assertFalse(db.verify_user("erin", db.EXTERNAL_HASH))  # the sentinel is not a password
        self.assertFalse(db.verify_user("erin", ""))
        self.assertEqual(db.get_session(session["token"])["username"], "erin")

    def test_roles_follow_the_identity_provider_on_every_login(self) -> None:
        from backend import database as db

        self.assertEqual(self.login(roles=["staff"])["role"], "limited")
        self.assertEqual(self.login(roles=["orion-admin"])["role"], "super_user")
        self.assertEqual(db.get_user_role("erin"), "super_user")
        self.assertEqual(self.login(roles=["staff"])["role"], "limited")
        self.assertEqual(db.get_user_role("erin"), "limited")

    def test_a_token_can_never_take_over_a_local_password_account(self) -> None:
        from backend import database as db

        db.init_database()
        db.create_user("frank", "frank-password-1", "super_user", "acme")
        with self.assertRaises(self.oidc.OidcError) as raised:
            self.login(preferred_username="frank", roles=["orion-admin"])
        self.assertEqual(raised.exception.status, 403)
        self.assertTrue(db.verify_user("frank", "frank-password-1"))  # untouched

    def test_a_token_can_never_take_over_the_local_admin(self) -> None:
        from backend import database as db

        db.init_database()
        db.setup_default_user()
        with self.assertRaises(self.oidc.OidcError):
            self.login(preferred_username="admin", tenant="default", roles=["orion-admin"])

    def test_an_account_cannot_be_moved_to_another_tenant(self) -> None:
        self.login(tenant="acme")
        with self.assertRaises(self.oidc.OidcError) as raised:
            self.login(tenant="beta")
        self.assertEqual(raised.exception.status, 403)
        self.assertEqual(self.login(tenant="acme")["tenant"], "acme")

    def test_a_disabled_account_cannot_sign_in(self) -> None:
        from backend import database as db

        self.login()
        db.set_user_active("erin", False)
        with self.assertRaises(self.oidc.OidcError):
            self.login()

    def test_auto_provisioning_can_be_turned_off(self) -> None:
        from dataclasses import replace

        strict = replace(self.cfg, auto_provision=False)
        with self.assertRaises(self.oidc.OidcError):
            self.oidc.login(self.token(), strict, self.resolver)
        self.login()  # provisioned by an administrator or an earlier login
        self.assertEqual(self.oidc.login(self.token(), strict, self.resolver)["username"], "erin")

    def test_login_is_refused_when_oidc_is_not_configured(self) -> None:
        with self.assertRaises(self.oidc.OidcError) as raised:
            self.oidc.login(self.token())
        self.assertEqual(raised.exception.status, 404)

    def test_a_forged_or_expired_token_creates_no_account(self) -> None:
        from backend import database as db

        for token in (self.token(key=self.other_key, preferred_username="ghost"), self.token(exp=int(time.time()) - 3600, preferred_username="ghost")):
            with self.assertRaises(self.oidc.OidcError):
                self.oidc.login(token, self.cfg, self.resolver)
        db.init_database()
        self.assertIsNone(db.get_user_row("ghost"))

    def test_external_accounts_cannot_log_in_with_a_password(self) -> None:
        from backend import database as db

        self.login()
        for guess in ("", "!external", "password", "erin"):
            self.assertFalse(db.verify_user("erin", guess), guess)


class OidcHttpTests(ApiTestCase):
    ISSUER = "https://idp.example.com"
    AUDIENCE = "orion-client"

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.key = _keypair()

    def setUp(self) -> None:
        from backend.ratelimit import login_throttle

        login_throttle.reset()
        self._env2 = patch.dict(os.environ, {"ORION_OIDC_ISSUER": self.ISSUER, "ORION_OIDC_CLIENT_ID": self.AUDIENCE})
        self._env2.start()
        self._resolver = patch("harness.oidc.default_key_resolver", return_value=lambda token: self.key.public_key())
        self._resolver.start()

    def tearDown(self) -> None:
        self._resolver.stop()
        self._env2.stop()

    def token(self, **overrides) -> str:
        import jwt

        claims = {
            "iss": self.ISSUER, "aud": self.AUDIENCE, "sub": "s-http", "preferred_username": "gina",
            "exp": int(time.time()) + 600, "tenant": "acme", "roles": ["staff"],
        }
        claims.update(overrides)
        return jwt.encode(claims, self.key, algorithm="RS256")

    def test_config_endpoint_reports_enablement_without_secrets(self) -> None:
        body = self.client.get("/auth/oidc/config").json()
        self.assertEqual(body, {"enabled": True, "issuer": self.ISSUER, "client_id": self.AUDIENCE})
        with patch.dict(os.environ, {"ORION_OIDC_ISSUER": ""}):
            self.assertEqual(self.client.get("/auth/oidc/config").json()["enabled"], False)

    def test_a_valid_token_opens_a_working_session(self) -> None:
        res = self.client.post("/auth/oidc", json={"id_token": self.token()})
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertEqual((body["username"], body["role"], body["tenant"]), ("gina", "limited", "acme"))
        me = self.client.get("/auth/me", headers={"Authorization": f"Bearer {body['token']}"}).json()
        self.assertEqual((me["username"], me["tenant"]), ("gina", "acme"))

    def test_an_invalid_token_is_a_401_with_the_standard_envelope(self) -> None:
        res = self.client.post("/auth/oidc", json={"id_token": self.token(aud="other")})
        self.assertEqual(res.status_code, 401)
        self.assertEqual(res.json()["error"]["code"], "oidc_rejected")

    def test_it_is_a_404_when_oidc_is_off(self) -> None:
        with patch.dict(os.environ, {"ORION_OIDC_ISSUER": ""}):
            res = self.client.post("/auth/oidc", json={"id_token": self.token()})
        self.assertEqual(res.status_code, 404)

    def test_repeated_bad_tokens_are_rate_limited(self) -> None:
        codes = [self.client.post("/auth/oidc", json={"id_token": self.token(aud="x")}).status_code for _ in range(30)]
        self.assertIn(429, codes)

    def test_oidc_users_are_tenant_scoped_like_any_other(self) -> None:
        token = self.client.post("/auth/oidc", json={"id_token": self.token(preferred_username="hank", tenant="beta")}).json()["token"]
        headers = {"Authorization": f"Bearer {token}"}
        self.assertEqual(self.client.get("/admin/users", headers=headers).status_code, 403)  # limited
        self.assertEqual(self.client.get("/auth/me", headers=headers).json()["tenant"], "beta")


# --------------------------------------------------------------------------- CLI and deployment files
class CliTenantTests(TempState):
    def test_the_cli_refuses_an_invalid_tenant(self) -> None:
        import io

        from harness import cli

        err = io.StringIO()
        with patch.dict(os.environ, {"CLI_PW": "a-good-password"}), contextlib.redirect_stderr(err):
            code = cli.main(["users", "create", "ivy", "--tenant", "../x", "--password-env", "CLI_PW"])
        self.assertEqual(code, 1)
        self.assertIn("invalid tenant", err.getvalue())

    def test_the_cli_creates_a_user_in_a_valid_tenant(self) -> None:
        import io

        from backend import database as db
        from harness import cli

        with patch.dict(os.environ, {"CLI_PW": "a-good-password"}), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["users", "create", "jay", "--tenant", "acme", "--password-env", "CLI_PW"]), 0)
        self.assertEqual(db.get_user_tenant("jay"), "acme")


class DeploymentFileTests(unittest.TestCase):
    ROOT = Path(__file__).resolve().parent.parent

    def read(self, name: str) -> str:
        return (self.ROOT / name).read_text(encoding="utf-8")

    def test_the_container_runs_unprivileged_and_listens_on_the_documented_port(self) -> None:
        text = self.read("Dockerfile")
        self.assertIn("USER orion", text)
        self.assertIn("EXPOSE 8000", text)
        self.assertIn("HEALTHCHECK", text)
        self.assertIn('CMD ["orion", "serve"', text)
        self.assertNotIn("COPY . ", text)  # never bake .env, data or outputs into the image
        self.assertNotIn("ORION_ADMIN_PASSWORD=", text)  # no credential in the image

    def test_the_kb_directory_is_not_the_kb_python_package(self) -> None:
        for name in ("Dockerfile", "docker-compose.yml"):
            self.assertIn("ORION_KB_DIR: /app/outputs/kb" if name.endswith("yml") else "ORION_KB_DIR=/app/outputs/kb", self.read(name))
        self.assertNotIn(":/app/kb", self.read("docker-compose.yml"))  # a volume there would hide the code

    def test_compose_is_hardened_and_loopback_only(self) -> None:
        text = self.read("docker-compose.yml")
        for fragment in ("127.0.0.1:8000:8000", "read_only: true", "cap_drop:", "no-new-privileges", "ORION_ENV: production", ":/app/data:ro"):
            self.assertIn(fragment, text)
        self.assertNotIn("privileged", text)
        self.assertNotIn("network_mode: host", text)

    def test_the_dockerignore_keeps_secrets_and_the_corpus_out_of_the_build_context(self) -> None:
        entries = set(self.read(".dockerignore").split())
        for needed in (".env", ".env.*", "data/", "outputs/", "paper/", "bench/", "*.db", "config/*.json"):
            self.assertIn(needed, entries)
        self.assertIn("!.env.example", entries)

    def test_every_package_the_image_needs_is_copied(self) -> None:
        import tomllib

        meta = tomllib.loads(self.read("pyproject.toml"))
        packages = [p.rstrip("*") for p in meta["tool"]["setuptools"]["packages"]["find"]["include"]]
        dockerfile = self.read("Dockerfile")
        for package in packages:
            self.assertIn(f"COPY {package} {package}", dockerfile, package)

    def test_the_oidc_extra_exists_and_is_part_of_all(self) -> None:
        import tomllib

        extras = tomllib.loads(self.read("pyproject.toml"))["project"]["optional-dependencies"]
        self.assertTrue(any(d.lower().startswith("pyjwt") for d in extras["oidc"]))
        self.assertIn("oidc", " ".join(extras["all"]))

    def test_the_connector_example_stays_valid_json_with_tenants(self) -> None:
        data = json.loads(self.read("config/connectors.example.json"))
        self.assertTrue(any(c.get("tenants") for c in data["connectors"]))


if __name__ == "__main__":
    unittest.main()
