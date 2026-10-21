"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  ShieldAlert,
  Wrench,
  XCircle,
  CheckCircle2,
  AlertTriangle,
  RefreshCw,
  Sparkles,
} from "lucide-react";
import { Chrome } from "@/components/Chrome";
import {
  AuthError,
  decideQualityQueue,
  listQualityQueue,
  me,
} from "@/lib/api";
import type { QualityQueueItem, Session } from "@/lib/types";

export default function QualityQueuePage() {
  const router = useRouter();
  const [session, setSession] = useState<Session | null>(null);
  const [items, setItems] = useState<QualityQueueItem[]>([]);
  const [statusFilter, setStatusFilter] = useState<"pending" | "dismissed" | "resolved">("pending");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  useEffect(() => {
    let cancelled = false;
    me()
      .then((curr) => {
        if (cancelled) return;
        if (curr.role !== "super_user") {
          router.replace("/app");
          return;
        }
        setSession(curr);
        loadQueue();
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

  async function loadQueue(s = statusFilter) {
    setLoading(true);
    setError("");
    try {
      const data = await listQualityQueue(s);
      setItems(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load quality queue");
    } finally {
      setLoading(false);
    }
  }

  async function onDecide(id: string, action: "dismiss" | "repair") {
    setBusy(true);
    setError("");
    setSuccess("");
    try {
      await decideQualityQueue(id, action);
      setSuccess(
        action === "repair"
          ? "Repair Agent triggered to patch and self-heal the agent"
          : "Item dismissed from queue"
      );
      await loadQueue();
    } catch (err) {
      setError(err instanceof Error ? err.message : `Failed to ${action} item`);
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

  return (
    <div className="app-shell">
      <Chrome session={session} note="Quality Guard • Background Judge & Self-Repair" />

      <main style={{ padding: "32px var(--space-8)", maxWidth: 1100, margin: "0 auto", width: "100%" }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 24 }}>
          <div>
            <h1 style={{ margin: 0, display: "flex", alignItems: "center", gap: 10 }}>
              <ShieldAlert size={24} color="#f59e0b" /> Quality Queue & Self-Repair
            </h1>
            <p style={{ marginTop: 4, marginBottom: 0 }}>
              Answers flagged by the automated judge as weak or incomplete. Trigger the Repair Agent to patch contracts.
            </p>
          </div>

          <div style={{ display: "flex", gap: 10 }}>
            <div style={{ display: "flex", background: "var(--bg-elevated)", borderRadius: "var(--radius-sm)", padding: 2 }}>
              {(["pending", "dismissed", "resolved"] as const).map((tab) => (
                <button
                  key={tab}
                  type="button"
                  className={`btn-chip ${statusFilter === tab ? "active" : ""}`}
                  style={{ textTransform: "capitalize" }}
                  onClick={() => {
                    setStatusFilter(tab);
                    loadQueue(tab);
                  }}
                >
                  {tab}
                </button>
              ))}
            </div>

            <button type="button" className="btn-secondary" onClick={() => loadQueue()} disabled={loading}>
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
            <AlertTriangle size={15} />
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

        {items.length === 0 ? (
          <div className="card" style={{ textAlign: "center", padding: "60px 20px" }}>
            <CheckCircle2 size={36} color="#10b981" style={{ margin: "0 auto 12px" }} />
            <h3 style={{ margin: 0 }}>Quality Queue Clear</h3>
            <p style={{ fontSize: 13, color: "var(--text-muted)", marginTop: 4 }}>
              No answers currently flagged for review in this category.
            </p>
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            {items.map((item) => (
              <div key={item.id} className="card-elevated">
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 10 }}>
                  <span style={{ fontSize: 12, color: "var(--text-muted)", fontFamily: "monospace" }}>
                    Request ID: {item.request_id}
                  </span>
                  <span
                    className={`badge ${
                      item.status === "resolved"
                        ? "badge-success"
                        : item.status === "dismissed"
                        ? "badge-neutral"
                        : "badge-warning"
                    }`}
                    style={{ textTransform: "capitalize" }}
                  >
                    {item.status}
                  </span>
                </div>

                <div style={{ marginBottom: 12 }}>
                  <div style={{ fontSize: 11.5, fontWeight: 600, color: "var(--text-muted)", textTransform: "uppercase", marginBottom: 4 }}>
                    User Query
                  </div>
                  <p style={{ margin: 0, fontWeight: 500, color: "var(--text-primary)" }}>{item.query}</p>
                </div>

                <div style={{ marginBottom: 12 }}>
                  <div style={{ fontSize: 11.5, fontWeight: 600, color: "var(--text-muted)", textTransform: "uppercase", marginBottom: 4 }}>
                    Model Response
                  </div>
                  <div
                    style={{
                      background: "var(--bg-canvas)",
                      padding: "10px 14px",
                      borderRadius: "var(--radius-sm)",
                      fontSize: 13,
                      lineHeight: 1.5,
                      color: "var(--text-secondary)",
                      maxHeight: 140,
                      overflowY: "auto",
                    }}
                  >
                    {item.response}
                  </div>
                </div>

                {item.feedback && (
                  <div
                    style={{
                      marginBottom: 16,
                      padding: "8px 12px",
                      borderRadius: "var(--radius-sm)",
                      background: "rgba(245, 158, 11, 0.08)",
                      border: "1px solid rgba(245, 158, 11, 0.25)",
                      fontSize: 12.5,
                      color: "#fbbf24",
                    }}
                  >
                    <strong>Judge Feedback: </strong>
                    {item.feedback}
                  </div>
                )}

                {item.status === "pending" && (
                  <div
                    style={{
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "flex-end",
                      gap: 10,
                      paddingTop: 12,
                      borderTop: "1px solid var(--border-subtle)",
                    }}
                  >
                    <button
                      type="button"
                      className="btn-ghost"
                      onClick={() => onDecide(item.id, "dismiss")}
                      disabled={busy}
                    >
                      <XCircle size={13} /> Dismiss
                    </button>

                    <button
                      type="button"
                      className="btn-primary"
                      onClick={() => onDecide(item.id, "repair")}
                      disabled={busy}
                    >
                      <Wrench size={13} /> Trigger Self-Repair Agent
                    </button>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </main>
    </div>
  );
}
