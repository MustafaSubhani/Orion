"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  Cpu,
  History,
  RotateCcw,
  CheckCircle2,
  AlertTriangle,
  Play,
  Pause,
  Archive,
  Layers,
  Wrench,
  Tag,
  ArrowRight,
} from "lucide-react";
import { Chrome } from "@/components/Chrome";
import {
  AuthError,
  listAgents,
  listAgentVersions,
  me,
  rollbackAgent,
  setAgentStatus,
} from "@/lib/api";
import type { Agent, AgentVersion, Session } from "@/lib/types";

export default function AgentsPage() {
  const router = useRouter();
  const [session, setSession] = useState<Session | null>(null);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [busy, setBusy] = useState(false);

  // Agent detail modal & versions
  const [selectedAgent, setSelectedAgent] = useState<Agent | null>(null);
  const [versions, setVersions] = useState<AgentVersion[]>([]);
  const [loadingVersions, setLoadingVersions] = useState(false);

  useEffect(() => {
    let cancelled = false;
    me()
      .then((curr) => {
        if (cancelled) return;
        setSession(curr);
        loadAgents();
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

  async function loadAgents() {
    setLoading(true);
    setError("");
    try {
      const data = await listAgents();
      setAgents(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load agents");
    } finally {
      setLoading(false);
    }
  }

  async function openAgentDetail(agent: Agent) {
    setSelectedAgent(agent);
    setVersions([]);
    if (!agent.builtin && session?.role === "super_user") {
      setLoadingVersions(true);
      try {
        const v = await listAgentVersions(agent.id);
        setVersions(v);
      } catch {
        // Versions might be empty for new agents
      } finally {
        setLoadingVersions(false);
      }
    }
  }

  async function onStatusChange(agentId: string, status: "active" | "paused" | "retired") {
    setBusy(true);
    setError("");
    setSuccess("");
    try {
      const updated = await setAgentStatus(agentId, status);
      setSuccess(`Agent ${updated.name} updated to ${status}`);
      await loadAgents();
      if (selectedAgent && selectedAgent.id === agentId) {
        setSelectedAgent(updated);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Status change failed");
    } finally {
      setBusy(false);
    }
  }

  async function onRollback(agentId: string, version: number) {
    if (!confirm(`Are you sure you want to rollback ${agentId} to version ${version}?`)) return;
    setBusy(true);
    setError("");
    setSuccess("");
    try {
      const rolled = await rollbackAgent(agentId, version);
      setSuccess(`Rolled back ${rolled.name} to version ${version}`);
      await loadAgents();
      setSelectedAgent(rolled);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Rollback failed");
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
      <Chrome session={session} note="Agent Registry • Lifecycles & Versions" />

      <main style={{ padding: "32px var(--space-8)", maxWidth: 1200, margin: "0 auto", width: "100%" }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 24 }}>
          <div>
            <h1 style={{ margin: 0 }}>Agent Registry & Studio</h1>
            <p style={{ marginTop: 4, marginBottom: 0 }}>
              Specialized domain workers, tools catalog, entities, and version histories.
            </p>
          </div>

          <div style={{ display: "flex", gap: 8 }}>
            <span className="badge badge-neutral tabular-nums">
              {agents.length} Agent{agents.length !== 1 ? "s" : ""}
            </span>
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

        {/* Agents Grid */}
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(340px, 1fr))", gap: 20 }}>
          {agents.map((agent) => {
            const status = agent.status || "active";
            const isBuiltin = agent.builtin ?? true;
            return (
              <div
                key={agent.id}
                className="card"
                onClick={() => openAgentDetail(agent)}
                style={{
                  cursor: "pointer",
                  display: "flex",
                  flexDirection: "column",
                  justifyContent: "space-between",
                }}
              >
                <div>
                  <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 10 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                      <div
                        style={{
                          width: 28,
                          height: 28,
                          borderRadius: 6,
                          background: "var(--bg-elevated)",
                          display: "flex",
                          alignItems: "center",
                          justifyContent: "center",
                          color: "#3b82f6",
                        }}
                      >
                        <Cpu size={15} />
                      </div>
                      <span style={{ fontWeight: 600, fontSize: 15 }}>{agent.name}</span>
                    </div>

                    <div style={{ display: "flex", gap: 6 }}>
                      {isBuiltin ? (
                        <span className="badge badge-neutral" style={{ fontSize: 10 }}>Built-in</span>
                      ) : (
                        <span className="badge badge-warning" style={{ fontSize: 10 }}>Custom</span>
                      )}
                      <span
                        className={`badge ${
                          status === "active" || status === "deployed"
                            ? "badge-success"
                            : status === "paused"
                            ? "badge-warning"
                            : "badge-danger"
                        }`}
                        style={{ fontSize: 10, textTransform: "capitalize" }}
                      >
                        <span
                          className={`status-dot ${
                            status === "active" || status === "deployed"
                              ? "success"
                              : status === "paused"
                              ? "warning"
                              : "danger"
                          }`}
                        />
                        {status}
                      </span>
                    </div>
                  </div>

                  <p style={{ fontSize: 13, color: "var(--text-secondary)", minHeight: 38, marginBottom: 14 }}>
                    {agent.description}
                  </p>

                  {/* Tools & Entities */}
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 12 }}>
                    {(agent.tools || []).slice(0, 3).map((tool, i) => (
                      <span
                        key={i}
                        className="badge badge-neutral"
                        style={{ fontSize: 11, fontFamily: "monospace" }}
                      >
                        <Wrench size={10} /> {tool}
                      </span>
                    ))}
                    {(agent.tools || []).length > 3 && (
                      <span className="badge badge-neutral" style={{ fontSize: 10 }}>
                        +{(agent.tools || []).length - 3} more
                      </span>
                    )}
                  </div>
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
                  <span style={{ fontFamily: "monospace" }}>ID: {agent.id}</span>
                  <span style={{ display: "flex", alignItems: "center", gap: 4, color: "var(--accent-primary)" }}>
                    Details <ArrowRight size={12} />
                  </span>
                </div>
              </div>
            );
          })}
        </div>

        {/* Modal: Agent Detail & Version History */}
        {selectedAgent && (
          <div className="modal-backdrop" onClick={() => setSelectedAgent(null)}>
            <div className="modal-dialog" onClick={(e) => e.stopPropagation()} style={{ maxWidth: 640 }}>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 16 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                  <Cpu size={22} color="#3b82f6" />
                  <div>
                    <h2 style={{ margin: 0, fontSize: 18 }}>{selectedAgent.name}</h2>
                    <span style={{ fontSize: 12, color: "var(--text-muted)", fontFamily: "monospace" }}>
                      {selectedAgent.id}
                    </span>
                  </div>
                </div>

                <button type="button" className="btn-ghost" onClick={() => setSelectedAgent(null)}>
                  ✕
                </button>
              </div>

              <p style={{ fontSize: 13.5, color: "var(--text-primary)", marginBottom: 18 }}>
                {selectedAgent.description}
              </p>

              {/* Tools List */}
              <div style={{ marginBottom: 16 }}>
                <div style={{ fontSize: 12, fontWeight: 600, color: "var(--text-muted)", textTransform: "uppercase", marginBottom: 6 }}>
                  Exposed Tools ({selectedAgent.tools?.length || 0})
                </div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                  {(selectedAgent.tools || []).map((t, i) => (
                    <span key={i} className="badge badge-neutral" style={{ fontFamily: "monospace" }}>
                      <Wrench size={11} /> {t}
                    </span>
                  ))}
                </div>
              </div>

              {/* Entities List */}
              <div style={{ marginBottom: 20 }}>
                <div style={{ fontSize: 12, fontWeight: 600, color: "var(--text-muted)", textTransform: "uppercase", marginBottom: 6 }}>
                  Associated Data Entities
                </div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                  {(selectedAgent.entities || []).length === 0 ? (
                    <span style={{ fontSize: 12, color: "var(--text-muted)" }}>None specified</span>
                  ) : (
                    (selectedAgent.entities || []).map((e, i) => (
                      <span key={i} className="badge badge-neutral">
                        <Tag size={11} /> {e}
                      </span>
                    ))
                  )}
                </div>
              </div>

              {/* Lifecycle Controls (Admins only for non-builtin) */}
              {isAdmin && !selectedAgent.builtin && (
                <div
                  style={{
                    padding: "14px",
                    borderRadius: "var(--radius-sm)",
                    background: "var(--bg-surface)",
                    border: "1px solid var(--border-subtle)",
                    marginBottom: 20,
                  }}
                >
                  <div style={{ fontSize: 12, fontWeight: 600, color: "var(--text-muted)", textTransform: "uppercase", marginBottom: 10 }}>
                    Lifecycle Control
                  </div>
                  <div style={{ display: "flex", gap: 8 }}>
                    <button
                      type="button"
                      className="btn-secondary"
                      onClick={() => onStatusChange(selectedAgent.id, "active")}
                      disabled={busy || selectedAgent.status === "active"}
                      style={{ fontSize: 12 }}
                    >
                      <Play size={12} /> Activate
                    </button>
                    <button
                      type="button"
                      className="btn-secondary"
                      onClick={() => onStatusChange(selectedAgent.id, "paused")}
                      disabled={busy || selectedAgent.status === "paused"}
                      style={{ fontSize: 12 }}
                    >
                      <Pause size={12} /> Pause
                    </button>
                    <button
                      type="button"
                      className="btn-danger"
                      onClick={() => onStatusChange(selectedAgent.id, "retired")}
                      disabled={busy || selectedAgent.status === "retired"}
                      style={{ fontSize: 12 }}
                    >
                      <Archive size={12} /> Retire
                    </button>
                  </div>
                </div>
              )}

              {/* Version History */}
              {isAdmin && !selectedAgent.builtin && (
                <div>
                  <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, fontWeight: 600, color: "var(--text-muted)", textTransform: "uppercase", marginBottom: 10 }}>
                    <History size={13} /> Version History
                  </div>

                  {loadingVersions ? (
                    <p style={{ fontSize: 12, color: "var(--text-muted)" }}>Loading version records...</p>
                  ) : versions.length === 0 ? (
                    <p style={{ fontSize: 12, color: "var(--text-muted)" }}>Initial version only.</p>
                  ) : (
                    <div style={{ display: "flex", flexDirection: "column", gap: 8, maxHeight: 180, overflowY: "auto" }}>
                      {versions.map((ver) => (
                        <div
                          key={ver.version}
                          style={{
                            display: "flex",
                            alignItems: "center",
                            justifyContent: "space-between",
                            padding: "8px 12px",
                            borderRadius: "var(--radius-sm)",
                            background: "var(--bg-surface)",
                            border: "1px solid var(--border-subtle)",
                            fontSize: 12.5,
                          }}
                        >
                          <div>
                            <span style={{ fontWeight: 600 }}>v{ver.version}</span>
                            <span style={{ color: "var(--text-muted)", marginLeft: 8 }}>
                              by {ver.created_by} • {ver.created_at}
                            </span>
                            {ver.change_note && (
                              <p style={{ margin: "2px 0 0", fontSize: 11.5, color: "var(--text-secondary)" }}>
                                {ver.change_note}
                              </p>
                            )}
                          </div>

                          <button
                            type="button"
                            className="btn-ghost"
                            onClick={() => onRollback(selectedAgent.id, ver.version)}
                            disabled={busy}
                            style={{ fontSize: 11, padding: "3px 8px" }}
                            title="Rollback agent code to this version"
                          >
                            <RotateCcw size={12} /> Rollback
                          </button>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
