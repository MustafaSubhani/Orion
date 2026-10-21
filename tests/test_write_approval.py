"""Write tools: eval and limited roles never write; live chat follows tools.write_policy; grants are single use."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness import approvals
from harness import settings_store as ss
from harness.context import new_context
from harness.tool_registry import ToolRegistry
from harness.types import SideEffect, ToolManifest
from tests.apitest import ApiTestCase


class WriteCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._env = patch.dict(os.environ, {"ORION_STATE_DB": str(Path(self._tmp.name) / "s.db")})
        self._env.start()
        ss.clear_cache()
        self.runs: list[dict] = []
        self.tools = ToolRegistry()
        self.tools.register(
            ToolManifest(id="note", verb="write_note", resource_type="x", description="d", schema={"type": "object"},
                         side_effect=SideEffect.WRITE),
            lambda args, ctx: self.runs.append(args) or {"preview": f"saved {args.get('text')}"},
        )

    def tearDown(self) -> None:
        self._env.stop()
        ss.clear_cache()
        self._tmp.cleanup()

    def ctx(self, *, user="alice", role=None, product=True, tenant="default"):
        ctx = new_context(user=user, role=role)
        if product:
            ctx.extra["mode"] = "product"
        ctx.extra["tenant"] = tenant
        ctx.extra["approvals"] = []
        return ctx

    def call(self, ctx, text="hello"):
        return self.tools.call("write_note", {"text": text}, ctx)


class PolicyTests(WriteCase):
    def test_eval_mode_never_writes_whatever_the_policy(self) -> None:
        ss.set_value("tools.write_policy", "allow", "test")
        res = self.call(self.ctx(product=False))
        self.assertFalse(res.ok)
        self.assertEqual(res.error_class, "PolicyDenied")
        self.assertEqual(self.runs, [])

    def test_limited_role_never_writes(self) -> None:
        from harness.errors import PolicyDeniedError

        ss.set_value("tools.write_policy", "allow", "test")
        with self.assertRaises(PolicyDeniedError):  # the role allow-list rejects unknown pack verbs outright
            self.call(self.ctx(role="limited"))
        self.tools.register(  # platform-style tools pass the allow-list, so the write gate must stop them
            ToolManifest(id="mw", verb="mcp__fake__write", resource_type="mcp", description="d", schema={"type": "object"},
                         side_effect=SideEffect.WRITE),
            lambda args, ctx: self.runs.append(args) or {"preview": "saved"},
        )
        res = self.tools.call("mcp__fake__write", {"text": "x"}, self.ctx(role="limited"))
        self.assertEqual(res.error_class, "PolicyDenied")
        self.assertEqual(self.runs, [])

    def test_deny_policy_blocks(self) -> None:
        ss.set_value("tools.write_policy", "deny", "test")
        self.assertEqual(self.call(self.ctx()).error_class, "PolicyDenied")
        self.assertEqual(self.runs, [])

    def test_allow_policy_runs_the_write(self) -> None:
        ss.set_value("tools.write_policy", "allow", "test")
        res = self.call(self.ctx())
        self.assertTrue(res.ok, res.error)
        self.assertEqual(self.runs, [{"text": "hello"}])

    def test_confirm_is_the_default(self) -> None:
        self.assertEqual(ss.effective("tools.write_policy"), "confirm")
        res = self.call(self.ctx())
        self.assertEqual(res.error_class, "ApprovalRequired")
        self.assertEqual(self.runs, [])

    def test_internal_scratch_writers_are_exempt_in_every_mode(self) -> None:
        """Regression: skill saves and chart images write only harness-owned storage and must never need approval."""
        from harness.tool_registry import INTERNAL_WRITE_VERBS

        self.assertEqual(INTERNAL_WRITE_VERBS, frozenset({"render_chart", "skill_manage"}))
        for product in (False, True):
            res = self.tools.call("skill_manage", {"op": "list"}, self.ctx(product=product))
            self.assertTrue(res.ok, f"product={product}: {res.error}")
        builtin_writes = {v for v in self.tools.verbs() if self.tools.manifest(v).side_effect == SideEffect.WRITE}
        external = {v for v in builtin_writes if not v.startswith("mcp__") and v != "write_note"}
        self.assertEqual(external - INTERNAL_WRITE_VERBS, set(), "a new built-in write tool needs an explicit policy decision")

    def test_read_tools_are_unaffected(self) -> None:
        res = self.tools.call("join_check", {"entities": "MNC"}, self.ctx())
        self.assertTrue(res.ok)


class ApprovalFlowTests(WriteCase):
    def test_blocked_call_records_a_pending_approval_and_reports_it(self) -> None:
        ctx = self.ctx()
        res = self.call(ctx)
        pending = approvals.list_for(tenant="default", status="pending")
        self.assertEqual(len(pending), 1)
        self.assertIn(pending[0]["id"], res.error)
        self.assertEqual(ctx.extra["approvals"][0]["id"], pending[0]["id"])
        self.assertEqual(pending[0]["username"], "alice")

    def test_repeating_the_blocked_call_reuses_the_same_pending_approval(self) -> None:
        ctx = self.ctx()
        self.call(ctx)
        self.call(ctx)
        self.assertEqual(len(approvals.list_for(tenant="default", status="pending")), 1)
        self.assertEqual(len(ctx.extra["approvals"]), 1)

    def test_an_approval_is_a_single_use_grant_for_the_same_arguments(self) -> None:
        self.call(self.ctx())
        approval = approvals.list_for(tenant="default", status="pending")[0]
        approvals.decide(approval["id"], approver="boss", tenant="default", approve=True)
        first = self.call(self.ctx())
        self.assertTrue(first.ok, first.error)
        self.assertEqual(self.runs, [{"text": "hello"}])
        again = self.call(self.ctx())
        self.assertEqual(again.error_class, "ApprovalRequired")  # consumed
        self.assertEqual(len(self.runs), 1)

    def test_a_grant_does_not_cover_different_arguments_users_or_tenants(self) -> None:
        self.call(self.ctx())
        approvals.decide(approvals.list_for(tenant="default")[0]["id"], approver="boss", tenant="default", approve=True)
        self.assertEqual(self.call(self.ctx(), text="other").error_class, "ApprovalRequired")
        self.assertEqual(self.call(self.ctx(user="mallory")).error_class, "ApprovalRequired")
        self.assertEqual(self.call(self.ctx(tenant="acme")).error_class, "ApprovalRequired")
        self.assertEqual(self.runs, [])
        self.assertTrue(self.call(self.ctx()).ok)

    def test_denied_and_expired_approvals_do_not_grant(self) -> None:
        self.call(self.ctx())
        approval = approvals.list_for(tenant="default")[0]
        approvals.decide(approval["id"], approver="boss", tenant="default", approve=False)
        self.assertEqual(self.call(self.ctx()).error_class, "ApprovalRequired")
        self.call(self.ctx(), text="late")
        late = [a for a in approvals.list_for(tenant="default", status="pending")][0]
        approvals.decide(late["id"], approver="boss", tenant="default", approve=True)
        with patch.object(approvals, "GRANT_TTL_S", -1):
            self.assertEqual(self.call(self.ctx(), text="late").error_class, "ApprovalRequired")
        self.assertEqual(self.runs, [])

    def test_a_decision_can_only_be_made_once(self) -> None:
        self.call(self.ctx())
        approval_id = approvals.list_for(tenant="default")[0]["id"]
        self.assertIsNotNone(approvals.decide(approval_id, approver="boss", tenant="default", approve=True))
        self.assertIsNone(approvals.decide(approval_id, approver="boss", tenant="default", approve=False))
        self.assertIsNone(approvals.decide(approval_id, approver="boss", tenant="other", approve=True))

    def test_concurrent_callers_cannot_double_spend_one_grant(self) -> None:
        import threading

        self.call(self.ctx())
        approvals.decide(approvals.list_for(tenant="default")[0]["id"], approver="boss", tenant="default", approve=True)
        results: list = []
        barrier = threading.Barrier(8)

        def spend() -> None:
            barrier.wait()
            results.append(approvals.consume_grant(tenant="default", user="alice", verb="write_note", args={"text": "hello"}))

        threads = [threading.Thread(target=spend) for _ in range(8)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(len([r for r in results if r]), 1)

    def test_approval_required_is_not_retried_by_healing(self) -> None:
        from harness.healing import FailureClass, Healer
        from harness.types import WorkerResult

        fc = Healer().classify(result=WorkerResult(text="x", ok=False, error_class="ApprovalRequired"))
        self.assertEqual(fc, FailureClass.F7_POLICY)


class ApprovalApiTests(ApiTestCase):
    def setUp(self) -> None:
        from harness import approvals as store

        self.record = store.request(tenant="default", user="requester", request_id="R1", verb="write_note", args={"text": "x"})

    def test_users_see_only_their_own_and_cannot_decide(self) -> None:
        requester = self.make_user("requester")
        other = self.make_user("bystander")
        mine = self.client.get("/approvals", headers=requester).json()
        self.assertIn(self.record["id"], [a["id"] for a in mine])
        self.assertTrue(all(a["username"] == "requester" for a in mine))
        self.assertEqual(self.client.get("/approvals", headers=other).json(), [])
        res = self.client.post(f"/approvals/{self.record['id']}", headers=requester, json={"decision": "approve"})
        self.assertEqual(res.status_code, 403)

    def test_admin_decides_once_and_the_grant_then_works(self) -> None:
        admin = self.admin()
        listed = self.client.get("/approvals?status=pending", headers=admin).json()
        self.assertIn(self.record["id"], [a["id"] for a in listed])
        res = self.client.post(f"/approvals/{self.record['id']}", headers=admin, json={"decision": "approve"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["status"], "approved")
        self.assertEqual(res.json()["decided_by"], "admin")
        self.assertEqual(self.client.post(f"/approvals/{self.record['id']}", headers=admin, json={"decision": "deny"}).status_code, 409)
        self.assertEqual(approvals.consume_grant(tenant="default", user="requester", verb="write_note", args={"text": "x"}), self.record["id"])

    def test_unknown_approval_and_bad_decision(self) -> None:
        admin = self.admin()
        self.assertEqual(self.client.post("/approvals/apr_nope", headers=admin, json={"decision": "approve"}).status_code, 404)
        self.assertEqual(self.client.post(f"/approvals/{self.record['id']}", headers=admin, json={"decision": "maybe"}).status_code, 422)

    def test_an_admin_cannot_approve_their_own_request(self) -> None:
        mine = approvals.request(tenant="default", user="admin", request_id="R2", verb="write_note", args={"text": "mine"})
        res = self.client.post(f"/approvals/{mine['id']}", headers=self.admin(), json={"decision": "approve"})
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json()["error"]["code"], "self_approval")


if __name__ == "__main__":
    unittest.main()
