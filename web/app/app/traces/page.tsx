"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  Activity,
  Download,
  RotateCcw,
  RefreshCw,
  GitCommit,
  Clock,
  User,
  ArrowRight,
  AlertCircle,
  FileCode,
} from "lucide-react";
import { Chrome } from "@/components/Chrome";
import {
  AuthError,
  exportTraceUrl,
  getRequestGraph,
  getTrace,
  listTraces,
  me,
} from "@/lib/api";
import type { RequestGraph, Session, TraceDetail, TraceSummary } from "@/lib/types";

export default function TracesPage() {
  const router = useRouter();
  const [session, setSession] = useState<Session | null>(null);
  const [traces, setTraces] = useState<TraceSummary[]>([]);
  const [selectedTraceId, setSelectedTraceId] = useState<string | null>(null);
  const [traceDetail, setTraceDetail] = useState<TraceDetail | null>(null);
  const [requestGraph, setRequestGraph] = useState<RequestGraph | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    me()
      .then((curr) => {
        if (cancelled) return;
        setSession(curr);
        loadTraces();
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

  async function loadTraces() {
    setLoading(true);
    setError("");
    try {
      const data = await listTraces(40);
      setTraces(data);
      if (data.length > 0 && !selectedTraceId) {
        inspectTrace(data[0].request_id);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load traces");
    } finally {
      setLoading(false);
    }
  }

  async function inspectTrace(requestId: string) {
    setSelectedTraceId(requestId);
    setLoadingDetail(true);
    setError("");
    try {
      const [detail, graph] = await Promise.all([
        getTrace(requestId).catch(() => null),
        getRequestGraph(requestId).catch(() => null),
      ]);
      setTraceDetail(detail);
      setRequestGraph(graph);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to inspect trace");
    } finally {
      setLoadingDetail(false);
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
      <Chrome session={session} note="Observability • Flight Recorder & DAG Tracing" />

      <main style={{ padding: "32px var(--space-8)", maxWidth: 1300, margin: "0 auto", width: "100%" }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 24 }}>
          <div>
            <h1 style={{ margin: 0, display: "flex", alignItems: "center", gap: 10 }}>
              <Activity size={24} color="#3b82f6" /> Execution Flight Recorder & Traces
            </h1>
            <p style={{ marginTop: 4, marginBottom: 0 }}>
              Inspect multi-agent execution DAGs, span latencies, and self-healing repair events.
            </p>
          </div>

          <button type="button" className="btn-secondary" onClick={loadTraces} disabled={loading}>
            <RefreshCw size={14} className={loading ? "animate-spin" : ""} /> Refresh
          </button>
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

        <div style={{ display: "grid", gridTemplateColumns: "380px 1fr", gap: 24 }}>
          {/* Traces List */}
          <section className="card" style={{ display: "flex", flexDirection: "column", maxHeight: 780 }}>
            <div style={{ borderBottom: "1px solid var(--border-subtle)", paddingBottom: 10, marginBottom: 12 }}>
              <h3 style={{ margin: 0 }}>Recent Recorded Requests</h3>
              <span className="tabular-nums" style={{ fontSize: 11, color: "var(--text-muted)" }}>
                {traces.length} flight records found
              </span>
            </div>

            <div style={{ flex: 1, overflowY: "auto", display: "flex", flexDirection: "column", gap: 6 }}>
              {traces.length === 0 ? (
                <p style={{ fontSize: 12.5, color: "var(--text-muted)", padding: "16px 0" }}>
                  No execution traces recorded yet. Send queries in Chat to generate telemetry.
                </p>
              ) : (
                traces.map((t) => {
                  const isSelected = t.request_id === selectedTraceId;
                  const dateStr = t.started_at
                    ? new Date(t.started_at * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })
                    : "Unknown";
                  return (
                    <div
                      key={t.request_id}
                      onClick={() => inspectTrace(t.request_id)}
                      style={{
                        padding: "10px 12px",
                        borderRadius: "var(--radius-sm)",
                        background: isSelected ? "var(--bg-elevated)" : "var(--bg-surface)",
                        border: isSelected ? "1px solid #3b82f6" : "1px solid var(--border-subtle)",
                        cursor: "pointer",
                      }}
                    >
                      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 4 }}>
                        <span style={{ fontSize: 12, fontFamily: "monospace", color: isSelected ? "#60a5fa" : "var(--text-secondary)" }}>
                          {t.request_id.slice(0, 16)}...
                        </span>
                        <span className="tabular-nums" style={{ fontSize: 11, color: "var(--text-muted)" }}>
                          <Clock size={11} style={{ display: "inline", verticalAlign: "middle", marginRight: 3 }} />
                          {dateStr}
                        </span>
                      </div>

                      <p
                        style={{
                          margin: 0,
                          fontSize: 12.5,
                          color: "var(--text-primary)",
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                          whiteSpace: "nowrap",
                        }}
                      >
                        {t.query || "(No query text)"}
                      </p>

                      <div style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 6, fontSize: 11, color: "var(--text-muted)" }}>
                        <User size={11} /> {t.user || "anonymous"} • {t.tenant || "default"}
                      </div>
                    </div>
                  );
                })
              )}
            </div>
          </section>

          {/* Trace Details / Waterfall / DAG */}
          <section style={{ display: "flex", flexDirection: "column", gap: 20 }}>
            {selectedTraceId ? (
              <>
                {/* Header Card */}
                <div className="card-elevated">
                  <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                    <div>
                      <span style={{ fontSize: 12, color: "var(--text-muted)", textTransform: "uppercase" }}>
                        Flight Record
                      </span>
                      <h2 style={{ margin: "2px 0 0", fontFamily: "monospace", fontSize: 16 }}>
                        {selectedTraceId}
                      </h2>
                    </div>

                    <a
                      href={exportTraceUrl(selectedTraceId)}
                      target="_blank"
                      rel="noreferrer"
                      className="btn-secondary"
                      style={{ fontSize: 12.5 }}
                      title="Download raw flight recorder JSONL"
                    >
                      <Download size={13} /> Export JSONL
                    </a>
                  </div>
                </div>

                {/* Self-Healing Events Section */}
                {requestGraph?.heal_events && requestGraph.heal_events.length > 0 && (
                  <div className="card" style={{ borderColor: "rgba(245, 158, 11, 0.4)", background: "rgba(245, 158, 11, 0.05)" }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
                      <RotateCcw size={16} color="#f59e0b" />
                      <h3 style={{ margin: 0, color: "#f59e0b" }}>Self-Healing Repair Sequence Triggered</h3>
                    </div>
                    <p style={{ fontSize: 13, color: "var(--text-secondary)", marginBottom: 10 }}>
                      The harness detected a contract violation or failure and rewound execution:
                    </p>
                    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                      {requestGraph.heal_events.map((h, i) => (
                        <div
                          key={i}
                          style={{
                            padding: "8px 12px",
                            borderRadius: "var(--radius-sm)",
                            background: "var(--bg-elevated)",
                            border: "1px solid rgba(245, 158, 11, 0.2)",
                            fontSize: 12.5,
                          }}
                        >
                          <span style={{ fontWeight: 600, color: "#f59e0b" }}>Event #{i + 1}: </span>
                          <span>{h.name || "Heal"} • {h.reason || h.error || "Contract failure resolved"}</span>
                          {h.latency_ms && <span className="tabular-nums" style={{ marginLeft: 8, color: "var(--text-muted)" }}>({h.latency_ms.toFixed(1)} ms)</span>}
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* Execution Graph / Tasks */}
                {requestGraph?.tasks && requestGraph.tasks.length > 0 && (
                  <div className="card">
                    <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
                      <GitCommit size={16} color="#3b82f6" />
                      <h3 style={{ margin: 0 }}>Planner Execution Tasks</h3>
                    </div>

                    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                      {requestGraph.tasks.map((tsk, i) => {
                        const status = String(tsk.status || "pending");
                        return (
                          <div
                            key={i}
                            style={{
                              padding: "10px 14px",
                              borderRadius: "var(--radius-sm)",
                              background: "var(--bg-surface)",
                              border: "1px solid var(--border-subtle)",
                              display: "flex",
                              alignItems: "center",
                              justifyContent: "space-between",
                            }}
                          >
                            <div>
                              <div style={{ fontWeight: 500, fontSize: 13, color: "var(--text-primary)" }}>
                                {String(tsk.description || tsk.id)}
                              </div>
                              <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>
                                Assigned Agent: <span style={{ color: "#60a5fa" }}>{String(tsk.agent || "router")}</span>
                              </div>
                            </div>

                            <span
                              className={`badge ${
                                status === "completed" || status === "success"
                                  ? "badge-success"
                                  : status === "failed"
                                  ? "badge-danger"
                                  : "badge-warning"
                              }`}
                              style={{ textTransform: "capitalize" }}
                            >
                              {status}
                            </span>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                )}

                {/* Spans Waterfall */}
                <div className="card">
                  <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 12 }}>
                    <h3 style={{ margin: 0 }}>Span Telemetry & Latencies</h3>
                    <span className="tabular-nums" style={{ fontSize: 12, color: "var(--text-muted)" }}>
                      {traceDetail?.spans.length || 0} spans
                    </span>
                  </div>

                  {loadingDetail ? (
                    <p style={{ fontSize: 12.5, color: "var(--text-muted)" }}>Loading spans...</p>
                  ) : !traceDetail?.spans || traceDetail.spans.length === 0 ? (
                    <p style={{ fontSize: 12.5, color: "var(--text-muted)" }}>No span breakdown recorded for this trace.</p>
                  ) : (
                    <div style={{ display: "flex", flexDirection: "column", gap: 6, maxHeight: 380, overflowY: "auto" }}>
                      {traceDetail.spans.map((sp, i) => (
                        <div
                          key={i}
                          style={{
                            padding: "8px 12px",
                            borderRadius: "var(--radius-sm)",
                            background: "var(--bg-elevated)",
                            border: "1px solid var(--border-subtle)",
                            fontSize: 12.5,
                            display: "flex",
                            alignItems: "center",
                            justifyContent: "space-between",
                          }}
                        >
                          <div>
                            <span style={{ fontWeight: 500 }}>{sp.name}</span>
                            {sp.kind && (
                              <span className="badge badge-neutral" style={{ marginLeft: 8, fontSize: 10 }}>
                                {sp.kind}
                              </span>
                            )}
                          </div>

                          <span className="tabular-nums" style={{ fontWeight: 600, color: "var(--text-secondary)" }}>
                            {sp.latency_ms !== undefined ? `${sp.latency_ms.toFixed(1)} ms` : "—"}
                          </span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </>
            ) : (
              <div className="card" style={{ textAlign: "center", padding: "60px 20px" }}>
                <Activity size={32} style={{ color: "var(--text-muted)", margin: "0 auto 12px" }} />
                <h3 style={{ margin: 0 }}>Select an Execution Trace</h3>
                <p style={{ fontSize: 13, color: "var(--text-muted)", marginTop: 4 }}>
                  Choose a trace from the left panel to inspect the execution DAG and span timeline.
                </p>
              </div>
            )}
          </section>
        </div>
      </main>
    </div>
  );
}
