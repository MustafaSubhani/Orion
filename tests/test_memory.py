"""Threads, rolling summary, scoped and batched skills, semantic selection, curator, thread APIs."""

from __future__ import annotations

import os
import re
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from harness import settings_store as ss
from harness import summarizer, threads
from harness.context import new_context
from harness.skills import Skill, SkillStore, skill_from_markdown
from harness.tool_registry import ToolRegistry
from tests.apitest import ApiTestCase, FakeGateway, fake_stream, installed_runtime


class StateCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._env = patch.dict(os.environ, {"ORION_STATE_DB": str(Path(self._tmp.name) / "s.db")})
        self._env.start()
        ss.clear_cache()

    def tearDown(self) -> None:
        self._env.stop()
        ss.clear_cache()
        self._tmp.cleanup()


class ThreadStoreTests(StateCase):
    def test_threads_are_private_to_owner_and_tenant(self) -> None:
        mine = threads.create(tenant="t1", user="alice", title="Payroll   questions\nfor IT")
        self.assertEqual(mine["title"], "Payroll questions for IT")
        self.assertIsNotNone(threads.get(mine["id"], "t1", "alice"))
        self.assertIsNone(threads.get(mine["id"], "t1", "bob"))
        self.assertIsNone(threads.get(mine["id"], "t2", "alice"))
        self.assertEqual(threads.list_for(tenant="t1", user="bob"), [])
        self.assertEqual([t["id"] for t in threads.list_for(tenant="t1", user="alice")], [mine["id"]])
        self.assertFalse(threads.delete(mine["id"], "t1", "bob"))
        self.assertFalse(threads.rename(mine["id"], "t1", "bob", "hijack"))

    def test_messages_roundtrip_and_delete_cascades(self) -> None:
        t = threads.create(tenant="d", user="a")
        threads.append(t["id"], "user", "q1", "R1")
        threads.append(t["id"], "assistant", "a1", "R1")
        rows = threads.messages(t["id"])
        self.assertEqual([(m["role"], m["content"]) for m in rows], [("user", "q1"), ("assistant", "a1")])
        self.assertEqual(len(threads.messages(t["id"], after_id=rows[0]["id"])), 1)
        self.assertEqual(threads.list_for(tenant="d", user="a")[0]["messages"], 2)
        self.assertTrue(threads.delete(t["id"], "d", "a"))
        self.assertEqual(threads.messages(t["id"]), [])

    def test_history_text_is_summary_plus_turns_after_it(self) -> None:
        t = threads.create(tenant="d", user="a")
        first = threads.append(t["id"], "user", "old question", "R1")
        threads.append(t["id"], "assistant", "old answer", "R1")
        threads.append(t["id"], "user", "new question", "R2")
        threads.set_summary(t["id"], "They asked about payroll.", first + 1)
        text = threads.history_text(threads.get(t["id"], "d", "a"))
        self.assertIn("Summary of earlier turns:\nThey asked about payroll.", text)
        self.assertIn("user: new question", text)
        self.assertNotIn("old question", text)

    def test_history_prefers_the_newest_turns_when_tight(self) -> None:
        t = threads.create(tenant="d", user="a")
        for i in range(20):
            threads.append(t["id"], "user", f"message number {i} " + "x" * 200)
        text = threads.history_text(threads.get(t["id"], "d", "a"), max_tokens=200)
        self.assertIn("message number 19", text)
        self.assertNotIn("message number 0 ", text)


class FoldThreadTests(StateCase):
    def fill(self, turns: int = 12, size: int = 600):
        t = threads.create(tenant="d", user="a")
        for i in range(turns):
            threads.append(t["id"], "user" if i % 2 == 0 else "assistant", f"turn {i} " + "w" * size)
        return t

    def test_below_the_trigger_nothing_happens(self) -> None:
        t = self.fill(turns=2, size=10)
        self.assertEqual(summarizer.fold_thread(t["id"], "d", "a")["reason"], "below_trigger")
        self.assertEqual(threads.get(t["id"], "d", "a")["summary"], "")

    def test_above_the_trigger_old_turns_fold_and_the_tail_stays(self) -> None:
        ss.set_value("memory.summary_trigger_tokens", 500, "test")
        t = self.fill()
        gateway = FakeGateway(["Goal: x. Facts: turns 0 1 2 3 4 5 6 7."])
        with installed_runtime(gateway):
            out = summarizer.fold_thread(t["id"], "d", "a", keep_tail=4)
        self.assertEqual((out["folded"], out["reason"]), (8, "ok"))
        thread = threads.get(t["id"], "d", "a")
        self.assertIn("Goal: x.", thread["summary"])
        remaining = threads.messages(t["id"], after_id=thread["summary_upto"])
        self.assertEqual([m["content"][:7] for m in remaining], ["turn 8 ", "turn 9 ", "turn 10", "turn 11"])
        self.assertEqual(len(threads.messages(t["id"])), 12)  # the full transcript is never lost

    def test_a_failed_summary_falls_back_to_extractive_and_still_bounds_growth(self) -> None:
        from harness.errors import TimeoutError_

        ss.set_value("memory.summary_trigger_tokens", 500, "test")
        t = self.fill()
        with installed_runtime(FakeGateway(error=TimeoutError_("slow"))):
            out = summarizer.fold_thread(t["id"], "d", "a", keep_tail=4)
        self.assertTrue(out["reason"].startswith("extractive("))
        thread = threads.get(t["id"], "d", "a")
        self.assertIn("- user: turn 0", thread["summary"])
        self.assertLess(threads.unsummarized_tokens(thread), 1500)

    def test_the_rolling_summary_is_incremental_and_keeps_old_facts(self) -> None:
        ss.set_value("memory.summary_trigger_tokens", 500, "test")
        t = self.fill()
        gateway = FakeGateway(["First summary mentions 0 1 2 3 4 5 6 7.", "Second summary mentions 0 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15."])
        with installed_runtime(gateway):
            summarizer.fold_thread(t["id"], "d", "a", keep_tail=4)
            for i in range(12, 20):
                threads.append(t["id"], "user", f"turn {i} " + "w" * 600)
            summarizer.fold_thread(t["id"], "d", "a", keep_tail=4)
        second_input = gateway.calls[1]["messages"][0]["content"]
        self.assertIn("Earlier summary:\nFirst summary", second_input)  # new summary builds on the old one
        self.assertNotIn("turn 0 ", second_input)  # already-folded turns are not re-read
        self.assertIn("Second summary", threads.get(t["id"], "d", "a")["summary"])

    def test_it_never_raises_and_respects_the_memory_switch(self) -> None:
        ss.set_value("memory.enabled", False, "test")
        self.assertEqual(summarizer.fold_thread("thr_none", "d", "a")["reason"], "memory_disabled")
        ss.set_value("memory.enabled", True, "test")
        self.assertEqual(summarizer.fold_thread("thr_none", "d", "a")["reason"], "unknown_thread")


class SkillScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.store = SkillStore(Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def make(self, name: str, scope: str, text: str = "average salary by department uses workforce_slice") -> Skill:
        return self.store.create(name=name, description=text, procedure=text, scope=scope)

    def test_scope_roundtrips_through_markdown(self) -> None:
        skill = self.make("scoped", "user:alice")
        self.assertEqual(skill_from_markdown(skill.to_markdown()).scope, "user:alice")
        self.assertEqual(skill_from_markdown("---\nname: legacy\n---\nbody").scope, "global")  # old files stay global

    def test_visibility_rules(self) -> None:
        self.assertTrue(Skill("a", "d", "p", scope="global").visible_to(None))
        mine = Skill("a", "d", "p", scope="user:alice")
        self.assertTrue(mine.visible_to("alice"))
        self.assertFalse(mine.visible_to("bob"))
        self.assertFalse(mine.visible_to(None))
        agent = Skill("a", "d", "p", scope="agent:hr")
        self.assertTrue(agent.visible_to("anyone", "hr"))
        self.assertFalse(agent.visible_to("anyone", "finance"))
        self.assertFalse(Skill("a", "d", "p", scope="weird:thing").visible_to("alice"))

    def test_select_only_returns_skills_the_caller_may_see(self) -> None:
        self.make("global-one", "global")
        self.make("alice-one", "user:alice")
        names = lambda **kw: {s.slug for s in self.store.select("average salary by department", k=5, **kw)}  # noqa: E731
        self.assertEqual(names(user="alice"), {"global-one", "alice-one"})
        self.assertEqual(names(user="bob"), {"global-one"})
        self.assertEqual(names(), {"global-one"})  # eval mode: user is None, so only global skills

    def test_live_recoveries_are_user_scoped_and_eval_recoveries_global(self) -> None:
        kw = dict(verb="sql_spec", error="unknown column", fix_preview="use schema_lookup", agent_id="hr", entities=[], failure_class="F4")
        live = self.store.remember_recovery(user="alice", **kw)
        self.assertEqual(live.scope, "user:alice")
        other = self.store.remember_recovery(**{**kw, "verb": "date_at"})
        self.assertEqual(other.scope, "global")

    def test_promote_makes_a_skill_global(self) -> None:
        self.make("shared-later", "user:alice")
        self.assertTrue(self.store.promote("shared-later"))
        self.assertEqual(self.store.get("shared-later").scope, "global")
        self.assertFalse(self.store.promote("ghost"))


class SkillBatchingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.store = SkillStore(Path(self._tmp.name))
        self.store.create(name="alpha", description="alpha skill", procedure="alpha procedure")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_selection_never_writes_until_flushed(self) -> None:
        with patch.object(SkillStore, "save", wraps=self.store.save) as save:
            for _ in range(5):
                self.store.touch("alpha")
            self.assertEqual(save.call_count, 0)
        self.assertEqual(self.store.flush_hits(), 1)
        self.assertEqual(self.store.get("alpha").hits, 5)

    def test_it_flushes_by_itself_after_enough_touches(self) -> None:
        for _ in range(SkillStore.FLUSH_EVERY):
            self.store.touch("alpha")
        self.assertEqual(self.store.get("alpha").hits, SkillStore.FLUSH_EVERY)

    def test_concurrent_touches_are_not_lost(self) -> None:
        def go() -> None:
            for _ in range(20):
                self.store.touch("alpha")

        threads_ = [threading.Thread(target=go) for _ in range(8)]
        [t.start() for t in threads_]
        [t.join() for t in threads_]
        self.store.flush_hits()
        self.assertEqual(self.store.get("alpha").hits, 160)

    def test_concurrent_saves_never_leave_a_torn_file(self) -> None:
        errors: list[Exception] = []
        stop = threading.Event()

        def writer(n: int) -> None:
            for i in range(25):
                self.store.create(name="shared", description=f"writer {n} round {i}", procedure="x" * 200)

        def reader() -> None:
            while not stop.is_set():
                try:
                    skill = self.store.get("shared")
                    if skill is not None:
                        assert skill.procedure.startswith("x"), "torn read"
                except Exception as exc:  # noqa: BLE001 - the assertion above is the check
                    errors.append(exc)

        readers = [threading.Thread(target=reader) for _ in range(2)]
        writers = [threading.Thread(target=writer, args=(n,)) for n in range(4)]
        [t.start() for t in readers + writers]
        [t.join() for t in writers]
        stop.set()
        [t.join() for t in readers]
        self.assertEqual(errors, [])

    def test_a_missing_skill_is_ignored_on_flush(self) -> None:
        self.store.touch("ghost")
        self.assertEqual(self.store.flush_hits(), 0)


def bag_embedder(texts: list[str]) -> list[list[float]]:
    """Tiny deterministic embedder where synonyms share a dimension."""
    groups = [{"pay", "salary", "compensation", "wages"}, {"calendar", "meeting", "schedule"}, {"revenue", "sales"}]
    out = []
    for text in texts:
        words = set(re.findall(r"[a-z]+", text.lower()))
        out.append([float(len(words & g)) for g in groups] + [0.01])
    return out


class SemanticSelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.store = SkillStore(Path(self._tmp.name), embedder=bag_embedder)
        self.store.create(name="pay-skill", description="compensation lookup procedure", procedure="use wages tables")
        self.store.create(name="cal-skill", description="meeting schedule procedure", procedure="use the calendar")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_semantic_ranking_finds_synonyms_lexical_overlap_misses(self) -> None:
        query = "salary figures"  # shares no words with any skill text
        self.assertEqual(self.store.select(query, k=2), [])  # lexical overlap: nothing clears the bar
        picked = self.store.select(query, k=1, semantic=True)
        self.assertEqual([s.slug for s in picked], ["pay-skill"])

    def test_eval_selection_stays_lexical_even_if_an_embedder_exists(self) -> None:
        with patch.object(self.store, "_embed", side_effect=AssertionError("must not embed")):
            self.store.select("average compensation procedure", k=1, semantic=False)

    def test_embedding_failure_falls_back_to_lexical(self) -> None:
        def broken(texts):
            raise RuntimeError("model not installed")

        store = SkillStore(Path(self._tmp.name) / "other", embedder=broken)
        store.create(name="lex", description="lookup compensation procedure", procedure="steps")
        self.assertEqual([s.slug for s in store.select("compensation lookup procedure", k=1, semantic=True)], ["lex"])

    def test_embeddings_are_cached_per_skill_version(self) -> None:
        calls = []
        store = SkillStore(Path(self._tmp.name) / "c", embedder=lambda t: calls.append(len(t)) or bag_embedder(t))
        store.create(name="one", description="salary procedure", procedure="p")
        store.select("salary", semantic=True)
        store.select("wages", semantic=True)
        self.assertEqual(calls[0], 1)  # the skill is embedded once
        self.assertEqual(calls[1:], [1, 1])  # later calls embed only the query


class CuratorTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.store = SkillStore(Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_it_runs_in_the_background_after_enough_saves_and_only_once_at_a_time(self) -> None:
        started, release = threading.Event(), threading.Event()

        def slow_curate():
            started.set()
            release.wait(5)
            return {}

        with patch.object(self.store, "curate", side_effect=slow_curate) as curate:
            self.assertFalse(self.store.maybe_curate(3))  # no saves yet
            for i in range(3):
                self.store.create(name=f"s{i}", description=f"d{i}", procedure=f"p{i}")
            self.assertTrue(self.store.maybe_curate(3))
            self.assertTrue(started.wait(5))
            for i in range(3, 6):
                self.store.create(name=f"s{i}", description=f"d{i}", procedure=f"p{i}")
            self.assertFalse(self.store.maybe_curate(3))  # one curator at a time
            release.set()
        self.assertEqual(curate.call_count, 1)

    def test_live_recovery_triggers_the_curator_counter(self) -> None:
        kw = dict(verb="v", error="e", fix_preview="f", agent_id="a", entities=[], failure_class="F1", user="u")
        with patch.object(self.store, "maybe_curate") as maybe:
            self.store.remember_recovery(curate_every=7, **kw)
            self.store.remember_recovery(curate_every=0, **{**kw, "verb": "w"})
        maybe.assert_called_once_with(7)


class SkillToolTests(StateCase):
    def setUp(self) -> None:
        super().setUp()
        self._skills = tempfile.TemporaryDirectory()
        self.store = SkillStore(Path(self._skills.name))
        self._patch = patch("harness.skills.default_store", return_value=self.store)
        self._patch.start()
        self.tools = ToolRegistry()

    def tearDown(self) -> None:
        self._patch.stop()
        self._skills.cleanup()
        super().tearDown()

    def ctx(self, user="alice", product=True):
        ctx = new_context(user=user)
        if product:
            ctx.extra["mode"] = "product"
        return ctx

    def call(self, args, **kw):
        return self.tools.call("skill_manage", args, self.ctx(**kw))

    def test_live_skills_are_private_and_hidden_from_others_and_from_eval(self) -> None:
        created = self.call({"op": "create", "name": "mine", "description": "d", "procedure": "p steps"})
        self.assertTrue(created.ok, created.error)
        self.assertEqual(self.store.get("mine").scope, "user:alice")
        self.assertIn("mine", self.call({"op": "list"}).preview)
        self.assertNotIn("mine", self.call({"op": "list"}, user="bob").preview)
        self.assertNotIn("mine", self.call({"op": "list"}, product=False).preview)
        self.assertEqual(self.call({"op": "get", "name": "mine"}, user="bob").error_class, "SchemaInvalid")
        self.assertEqual(self.call({"op": "archive", "name": "mine"}, user="bob").error_class, "SchemaInvalid")
        self.assertTrue(self.call({"op": "archive", "name": "mine"}).ok)

    def test_eval_mode_still_creates_global_skills(self) -> None:
        self.assertTrue(self.call({"op": "create", "name": "evalskill", "description": "d", "procedure": "p steps"}, product=False).ok)
        self.assertEqual(self.store.get("evalskill").scope, "global")

    def test_the_memory_switch_disables_skills_in_live_chat_only(self) -> None:
        ss.set_value("memory.enabled", False, "test")
        res = self.call({"op": "list"})
        self.assertEqual(res.error_class, "PolicyDenied")
        self.assertTrue(self.call({"op": "list"}, product=False).ok)


class ThreadApiTests(ApiTestCase):
    def setUp(self) -> None:
        self._fold = patch("backend.service.summarizer.fold_thread")
        self.fold = self._fold.start()

    def tearDown(self) -> None:
        self._fold.stop()
        self.client.delete("/admin/settings/memory.enabled", headers=self.admin())

    def chat(self, headers, message="how many people?", thread_id=None):
        with patch("backend.service.coordinate_stream", side_effect=fake_stream()) as spy:
            res = self.client.post("/chat", headers=headers, json={"message": message, "thread_id": thread_id})
        return res, spy

    def test_a_new_thread_is_created_titled_and_populated(self) -> None:
        headers = self.make_user("threader")
        res, spy = self.chat(headers, thread_id="new")
        self.assertEqual(res.status_code, 200, res.text)
        thread_id = res.json()["thread_id"]
        self.assertTrue(thread_id.startswith("thr_"))
        self.assertEqual(spy.call_args.kwargs["history"], "")
        body = self.client.get(f"/threads/{thread_id}/messages", headers=headers).json()
        self.assertEqual(body["title"], "how many people?")
        self.assertEqual([(m["role"], m["content"]) for m in body["messages"]], [("user", "how many people?"), ("assistant", "42 employees")])
        self.fold.assert_called()

    def test_the_second_turn_receives_the_conversation_as_history(self) -> None:
        headers = self.make_user("threader2")
        thread_id = self.chat(headers, thread_id="new")[0].json()["thread_id"]
        res, spy = self.chat(headers, "and for sales?", thread_id=thread_id)
        history = spy.call_args.kwargs["history"]
        self.assertIn("user: how many people?", history)
        self.assertIn("assistant: 42 employees", history)
        self.assertEqual(res.json()["thread_id"], thread_id)

    def test_threads_are_private_between_users_and_admins_cannot_read_them(self) -> None:
        owner, other = self.make_user("owner1"), self.make_user("other1")
        thread_id = self.chat(owner, thread_id="new")[0].json()["thread_id"]
        for headers in (other, self.admin()):
            self.assertEqual(self.client.get(f"/threads/{thread_id}/messages", headers=headers).status_code, 404)
            self.assertEqual(self.client.delete(f"/threads/{thread_id}", headers=headers).status_code, 404)
            self.assertEqual(self.chat(headers, thread_id=thread_id)[0].status_code, 404)
        self.assertEqual(self.client.get("/threads", headers=other).json(), [])

    def test_list_rename_and_delete(self) -> None:
        headers = self.make_user("manager1")
        thread_id = self.client.post("/threads", headers=headers, json={"title": "Budget"}).json()["id"]
        listed = self.client.get("/threads", headers=headers).json()
        self.assertEqual([t["title"] for t in listed], ["Budget"])
        renamed = self.client.patch(f"/threads/{thread_id}", headers=headers, json={"title": "Budget 2027"}).json()
        self.assertEqual(renamed["title"], "Budget 2027")
        self.assertEqual(self.client.delete(f"/threads/{thread_id}", headers=headers).status_code, 200)
        self.assertEqual(self.client.get(f"/threads/{thread_id}/messages", headers=headers).status_code, 404)

    def test_disabling_memory_makes_chat_stateless_and_threads_unavailable(self) -> None:
        headers = self.make_user("stateless1")
        self.client.put("/admin/settings/memory.enabled", headers=self.admin(), json={"value": False})
        res, spy = self.chat(headers, thread_id="new")
        self.assertEqual(res.status_code, 200)
        self.assertIsNone(res.json()["thread_id"])
        self.assertEqual(spy.call_args.kwargs["history"], "")
        listed = self.client.get("/threads", headers=headers)
        self.assertEqual((listed.status_code, listed.json()["error"]["code"]), (409, "memory_disabled"))

    def test_streaming_marks_the_thread_on_init_and_final(self) -> None:
        headers = self.make_user("streamer1")
        with patch("backend.service.coordinate_stream", side_effect=fake_stream()):
            res = self.client.post("/chat/stream", headers=headers, json={"message": "hello there", "thread_id": "new"})
        events = [__import__("json").loads(b.split("data:", 1)[1]) for b in res.text.split("\n\n") if b.startswith("data:")]
        ids = {e["thread_id"] for e in events if "thread_id" in e}
        self.assertEqual(len(ids), 1)
        self.assertEqual(events[-1]["type"], "final")
        self.assertIn("thread_id", events[0])

    def test_the_openai_endpoint_accepts_a_thread_id(self) -> None:
        headers = self.make_user("oaiuser1")
        with patch("backend.service.coordinate_stream", side_effect=fake_stream("Hi there")):
            res = self.client.post(
                "/v1/chat/completions", headers=headers,
                json={"messages": [{"role": "user", "content": "hi"}], "thread_id": "new"},
            )
        self.assertTrue(res.json()["orion"]["thread_id"].startswith("thr_"))

    def test_chat_without_a_thread_id_is_unchanged(self) -> None:
        res, spy = self.chat(self.make_user("plain1"))
        self.assertIsNone(res.json()["thread_id"])
        self.assertEqual(spy.call_args.kwargs["history"], "")


if __name__ == "__main__":
    unittest.main()
