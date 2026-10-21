"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  CheckCircle2,
  XCircle,
  Clock,
  ShieldCheck,
  AlertCircle,
  RefreshCw,
  Wrench,
  User,
} from "lucide-react";
import { Chrome } from "@/components/Chrome";
import {
  AuthError,
  decideApproval,
  listApprovals,
  me,
} from "@/lib/api";
import type { Approval, Session } from "@/lib/types";

export default function ApprovalsPage() {
  const router = useRouter();
  const [session, setSession] = useState<Session | null>(null);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [filter, setFilter] = useState<"pending" | "approved" | "denied" | "all">("all");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  useEffect(() => {
    let cancelled = false;
    me()
      .then((curr) => {
        if (cancelled) return;
        setSession(curr);
        loadApprovals();
      })
      .catch((err) => {
        if (cancelled) return;
        if (err instanceof AuthError) router.replace("/");
        else setError("Could not authenticate");
      });
    return () => {
      cancelled = true;
    };
  }, [router]);

  async function loadApprovals(f = filter) {
    setLoading(true);
    setError("");
    try {
      const data = await listApprovals(f === "all" ? undefined : f);
      setApprovals(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load approvals");
    } finally {
      setLoading(false);
    }
  }

  async function onDecide(id: string, decision: "approve" | "deny") {
    setBusy(true);
    setError("");
    setSuccess("");
    try {
      await decideApproval(id, decision);
      setSuccess(`Request ${decision === "approve" ? "approved" : "denied"} successfully`);
      await loadApprovals();
    } catch (err) {
      setError(err instanceof Error ? err.message : `Failed to ${decision} request`);
    } finally {
      setBusy(false);
    }
  }

  if (!session) {
    return (
      <main className="modal-backdrop">
        <div style={{ color: "var(--text-muted)" }}>Loading workspace...</div>
      </main>
    );
  }

  const isAdmin = session.role === "super_user";

  return (
    <div className="app-shell">
      <Chrome session={session} note="Governance • Human-in-the-Loop Approvals" />

      <main style={{ padding: "32px var(--space-8)", maxWidth: 1100, margin: "0 auto", width: "100%" }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 24 }}>
          <div>
            <h1 style={{ margin: 0, display: "flex", alignItems: "center", gap: 10 }}>
              <ShieldCheck size={24} color="#3b82f6" /> Tool Execution Approvals
            </h1>
            <p style={{ marginTop: 4, marginBottom: 0 }}>
              Audit and authorize write actions, database mutations, and external API calls before execution.
            </p>
          </div>

          <div style={{ display: "flex", gap: 10 }}>
            <div style={{ display: "flex", background: "var(--bg-elevated)", borderRadius: "var(--radius-sm)", padding: 2 }}>
              {(["all", "pending", "approved", "denied"] as const).map((tab) => (
                <button
                  key={tab}
                  type="button"
                  className={`btn-chip ${filter === tab ? "active" : ""}`}
                  style={{ textTransform: "capitalize" }}
                  onClick={() => {
                    setFilter(tab);
                    loadApprovals(tab);
                  }}
                >
                  {tab}
                </button>
              ))}
            </div>

            <button type="button" className="btn-secondary" onClick={() => loadApprovals()} disabled={loading}>
              <RefreshCw size={14} className={loading ? "animate-spin" : ""} /> Refresh
            </button>
          </div>
        </div>

        {error && (
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              padding: "10px 14px",
              borderRadius: "var(--radius-sm)",
              background: "var(--status-danger-bg)",
              color: "var(--status-danger)",
              marginBottom: 20,
              fontSize: 13,
            }}
          >
            <AlertCircle size={15} />
            <span>{error}</span>
          </div>
        )}

        {success && (
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              padding: "10px 14px",
              borderRadius: "var(--radius-sm)",
              background: "var(--status-success-bg)",
              color: "var(--status-success)",
              marginBottom: 20,
              fontSize: 13,
            }}
          >
            <CheckCircle2 size={15} />
            <span>{success}</span>
          </div>
        )}

        {approvals.length === 0 ? (
          <div className="card" style={{ textAlign: "center", padding: "60px 20px" }}>
            <ShieldCheck size={36} style={{ color: "var(--text-muted)", margin: "0 auto 12px" }} />
            <h3 style={{ margin: 0 }}>No Approval Requests Found</h3>
            <p style={{ fontSize: 13, color: "var(--text-muted)", marginTop: 4 }}>
              Tool executions requiring human authorization will appear here in real-time.
            </p>
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            {approvals.map((req) => {
              const isPending = req.status === "pending";
              const canDecide = isAdmin && req.username !== session.username && isPending;

              return (
                <div key={req.id} className="card-elevated">
                  <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 12 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                      <div
                        style={{
                          width: 30,
                          height: 30,
                          borderRadius: 6,
                          background: "var(--bg-surface)",
                          display: "flex",
                          alignItems: "center",
                          justifyContent: "center",
                          color: "#3b82f6",
                        }}
                      >
                        <Wrench size={16} />
                      </div>
                      <div>
                        <div style={{ fontWeight: 600, fontSize: 14 }}>
                          Action: <span style={{ fontFamily: "monospace", color: "#60a5fa" }}>{req.tool}</span>
                        </div>
                        <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
                          Requested by {req.username} • {new Date(req.created_at).toLocaleString()}
                        </div>
                      </div>
                    </div>

                    <span
                      className={`badge ${
                        req.status === "approved"
                          ? "badge-success"
                          : req.status === "denied"
                          ? "badge-danger"
                          : "badge-warning"
                      }`}
                      style={{ textTransform: "capitalize" }}
                    >
                      {req.status}
                    </span>
                  </div>

                  {/* Arguments payload */}
                  <div style={{ marginBottom: 16 }}>
                    <div style={{ fontSize: 11.5, fontWeight: 600, color: "var(--text-muted)", textTransform: "uppercase", marginBottom: 6 }}>
                      Execution Arguments
                    </div>
                    <pre
                      style={{
                        margin: 0,
                        background: "var(--bg-canvas)",
                        padding: "10px 14px",
                        borderRadius: "var(--radius-sm)",
                        fontSize: 12.5,
                        fontFamily: "monospace",
                        color: "var(--text-primary)",
                        overflowX: "auto",
                      }}
                    >
                      {JSON.stringify(req.arguments, null, 2)}
                    </pre>
                  </div>

                  <div
                    style={{
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "space-between",
                      paddingTop: 12,
                      borderTop: "1px solid var(--border-subtle)",
                      fontSize: 12,
                      color: "var(--text-muted)",
                    }}
                  >
                    <span className="tabular-nums" style={{ fontFamily: "monospace" }}>
                      ID: {req.id}
                    </span>

                    {isPending ? (
                      canDecide ? (
                        <div style={{ display: "flex", gap: 8 }}>
                          <button
                            type="button"
                            className="btn-danger"
                            onClick={() => onDecide(req.id, "deny")}
                            disabled={busy}
                          >
                            <XCircle size={13} /> Deny
                          </button>
                          <button
                            type="button"
                            className="btn-primary"
                            onClick={() => onDecide(req.id, "approve")}
                            disabled={busy}
                          >
                            <CheckCircle2 size={13} /> Authorize Execution
                          </button>
                        </div>
                      ) : (
                        <span style={{ color: "var(--status-warning)", fontStyle: "italic" }}>
                          {req.username === session.username
                            ? "Awaiting authorization by another administrator (self-approval prohibited)"
                            : "Awaiting administrator decision"}
                        </span>
                      )
                    ) : (
                      <span>
                        Decided by {req.decided_by || "system"} on{" "}
                        {req.decided_at ? new Date(req.decided_at).toLocaleString() : ""}
                      </span>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </main>
    </div>
  );
}
