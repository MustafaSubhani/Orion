"use client";

import { FormEvent, KeyboardEvent, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  FlaskConical,
  Play,
  Rocket,
  CheckCircle2,
  AlertTriangle,
  Bot,
  User,
  Plus,
  RefreshCw,
  Send,
  Sparkles,
} from "lucide-react";
import { Chrome } from "@/components/Chrome";
import {
  AuthError,
  createLabAgent,
  deployLabAgent,
  evaluateLabAgent,
  getLabEvaluation,
  listLabAgents,
  me,
  sendLab,
} from "@/lib/api";
import type { LabAgent, LabEvaluation, Session } from "@/lib/types";

type Message = { role: "user" | "assistant"; content: string };

export default function LabPage() {
  const router = useRouter();
  const [session, setSession] = useState<Session | null>(null);
  const [labAgents, setLabAgents] = useState<LabAgent[]>([]);
  const [messages, setMessages] = useState<Message[]>([
    {
      role: "assistant",
      content:
        "Welcome to Orion Lab. Synthesize and evaluate specialized agents without mixing cross-domain boundaries. Type 'help', 'list', or specify an agent job to begin.",
    },
  ]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  // Synthesis Form
  const [showBuilder, setShowBuilder] = useState(false);
  const [agentName, setAgentName] = useState("");
  const [agentJob, setAgentJob] = useState("");
  const [agentEntities, setAgentEntities] = useState("HR, LAKE");
  const [agentTools, setAgentTools] = useState("default");
  const [agentMode, setAgentMode] = useState<"card" | "code">("card");

  // Evaluation details
  const [evaluatingId, setEvaluatingId] = useState<string | null>(null);
  const [evalResult, setEvalResult] = useState<LabEvaluation | null>(null);

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
        refreshLabAgents();
      })
      .catch((err) => {
        if (cancelled) return;
        if (err instanceof AuthError) router.replace("/");
        else setError("Could not reach the API");
      });
    return () => {
      cancelled = true;
    };
  }, [router]);

  async function refreshLabAgents() {
    try {
      const data = await listLabAgents();
      setLabAgents(data);
    } catch {
      // Ignored
    }
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      event.currentTarget.form?.requestSubmit();
    }
  }

  async function onSend(event: FormEvent) {
    event.preventDefault();
    const text = draft.trim();
    if (!text || busy) return;
    setDraft("");
    setBusy(true);
    setError("");
    setMessages((prev) => [...prev, { role: "user", content: text }]);
    try {
      const reply = await sendLab(text);
      setMessages((prev) => [...prev, { role: "assistant", content: reply }]);
      await refreshLabAgents();
    } catch (err) {
      if (err instanceof AuthError) {
        router.replace("/");
        return;
      }
      setError(err instanceof Error ? err.message : "Lab synthesis request failed");
    } finally {
      setBusy(false);
    }
  }

  async function onCreateAgent(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    setSuccess("");
    try {
      const entitiesList = agentEntities.split(",").map((s) => s.trim()).filter(Boolean);
      const toolsList = agentTools === "default" ? "default" : agentTools.split(",").map((s) => s.trim()).filter(Boolean);
      const created = await createLabAgent({
        name: agentName.trim(),
        job: agentJob.trim(),
        entities: entitiesList,
        tools: toolsList,
        mode: agentMode,
      });
      setSuccess(`Synthesized new agent: ${created.name}`);
      setShowBuilder(false);
      setAgentName("");
      setAgentJob("");
      await refreshLabAgents();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Synthesis failed");
    } finally {
      setBusy(false);
    }
  }

  async function onEvaluate(agentId: string) {
    setEvaluatingId(agentId);
    setError("");
    setSuccess("");
    try {
      const ev = await evaluateLabAgent(agentId);
      setEvalResult(ev);
      setSuccess(`Evaluation completed: Score ${(ev.score * 100).toFixed(0)}% (${ev.passed ? "PASSED" : "FAILED"})`);
      await refreshLabAgents();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Evaluation failed");
    } finally {
      setEvaluatingId(null);
    }
  }

  async function onViewEvaluation(agentId: string) {
    setError("");
    try {
      const ev = await getLabEvaluation(agentId);
      setEvalResult(ev);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No evaluation recorded yet");
    }
  }

  async function onDeploy(agentId: string) {
    setBusy(true);
    setError("");
    setSuccess("");
    try {
      const deployed = await deployLabAgent(agentId);
      setSuccess(`Agent ${deployed.name} promoted to DEPLOYED in production`);
      await refreshLabAgents();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Deployment failed");
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
      <Chrome session={session} note="Lab • Agent Synthesizer & Contract Benchmark" />

      <main style={{ padding: "32px var(--space-8)", maxWidth: 1200, margin: "0 auto", width: "100%" }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 24 }}>
          <div>
            <h1 style={{ margin: 0, display: "flex", alignItems: "center", gap: 10 }}>
              <FlaskConical size={24} color="#3b82f6" /> Agent Synthesis & Evaluation Lab
            </h1>
            <p style={{ marginTop: 4, marginBottom: 0 }}>
              Generate custom domain workers, run automated contract tests, and promote to production safely.
            </p>
          </div>

          <button
            type="button"
            className="btn-primary"
            onClick={() => setShowBuilder(!showBuilder)}
          >
            <Plus size={14} /> {showBuilder ? "Close Builder" : "New Agent Builder"}
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

        {/* Builder Form (Toggleable) */}
        {showBuilder && (
          <form onSubmit={onCreateAgent} className="card-elevated" style={{ marginBottom: 28 }}>
            <h3 style={{ marginTop: 0, marginBottom: 12 }}>Synthesize Custom Worker Agent</h3>

            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
              <div className="form-group">
                <label className="form-label">Agent Name</label>
                <input
                  className="form-input"
                  placeholder="e.g. Compliance Auditor"
                  value={agentName}
                  onChange={(e) => setAgentName(e.target.value)}
                  required
                />
              </div>

              <div className="form-group">
                <label className="form-label">Mode</label>
                <select
                  className="form-select"
                  value={agentMode}
                  onChange={(e) => setAgentMode(e.target.value as "card" | "code")}
                >
                  <option value="card">Card (Declarative Schema)</option>
                  <option value="code">Code (Executable Python Spec)</option>
                </select>
              </div>
            </div>

            <div className="form-group">
              <label className="form-label">Job Specification & System Directives</label>
              <textarea
                className="form-textarea"
                placeholder="Describe exact responsibilities, guardrails, and what data sources this agent accesses..."
                value={agentJob}
                onChange={(e) => setAgentJob(e.target.value)}
                rows={3}
                required
              />
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
              <div className="form-group">
                <label className="form-label">Data Entities (comma separated)</label>
                <input
                  className="form-input"
                  placeholder="HR, LAKE, FINANCE, CALENDAR"
                  value={agentEntities}
                  onChange={(e) => setAgentEntities(e.target.value)}
                />
              </div>

              <div className="form-group">
                <label className="form-label">Tools Allowed (&apos;default&apos; or comma separated)</label>
                <input
                  className="form-input"
                  placeholder="default"
                  value={agentTools}
                  onChange={(e) => setAgentTools(e.target.value)}
                />
              </div>
            </div>

            <div style={{ display: "flex", justifyContent: "flex-end", gap: 10, marginTop: 12 }}>
              <button type="button" className="btn-ghost" onClick={() => setShowBuilder(false)}>
                Cancel
              </button>
              <button type="submit" className="btn-primary" disabled={busy}>
                <Sparkles size={14} /> Synthesize Agent
              </button>
            </div>
          </form>
        )}

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 24 }}>
          {/* Left Column: Interactive Synthesizer Chat */}
          <section className="card" style={{ display: "flex", flexDirection: "column", height: 600 }}>
            <div style={{ borderBottom: "1px solid var(--border-subtle)", paddingBottom: 12, marginBottom: 12 }}>
              <h3 style={{ margin: 0 }}>Interactive Synthesizer Assistant</h3>
              <p style={{ margin: "4px 0 0", fontSize: 12, color: "var(--text-muted)" }}>
                Chat with the meta-synthesizer to refine prompts and evaluate agent capabilities.
              </p>
            </div>

            <div style={{ flex: 1, overflowY: "auto", display: "flex", flexDirection: "column", gap: 12, paddingRight: 6 }}>
              {messages.map((msg, i) => (
                <div
                  key={i}
                  style={{
                    display: "flex",
                    gap: 10,
                    alignItems: "flex-start",
                    flexDirection: msg.role === "user" ? "row-reverse" : "row",
                  }}
                >
                  <div
                    style={{
                      width: 26,
                      height: 26,
                      borderRadius: 6,
                      background: msg.role === "user" ? "var(--bg-elevated)" : "#1e1e2d",
                      border: "1px solid var(--border-subtle)",
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      color: msg.role === "user" ? "var(--text-primary)" : "#3b82f6",
                      fontSize: 12,
                      flexShrink: 0,
                    }}
                  >
                    {msg.role === "user" ? <User size={13} /> : <Bot size={13} />}
                  </div>

                  <div
                    style={{
                      background: msg.role === "user" ? "var(--bg-elevated)" : "var(--bg-surface)",
                      border: "1px solid var(--border-subtle)",
                      borderRadius: "var(--radius-sm)",
                      padding: "10px 12px",
                      fontSize: 13,
                      lineHeight: 1.5,
                      maxWidth: "85%",
                      whiteSpace: "pre-wrap",
                    }}
                  >
                    {msg.content}
                  </div>
                </div>
              ))}
            </div>

            {/* Quick action chips */}
            <div style={{ display: "flex", gap: 6, margin: "10px 0" }}>
              {["help", "customize", "list"].map((cmd) => (
                <button
                  key={cmd}
                  type="button"
                  className="btn-chip"
                  onClick={() => setDraft(cmd)}
                  disabled={busy}
                >
                  {cmd}
                </button>
              ))}
            </div>

            <form onSubmit={onSend} style={{ display: "flex", gap: 8 }}>
              <textarea
                className="form-textarea"
                style={{ minHeight: 44, height: 44, resize: "none", fontSize: 13 }}
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={onKeyDown}
                placeholder="Ask Lab to synthesize or tune an agent..."
                disabled={busy}
              />
              <button type="submit" className="btn-primary" disabled={busy || !draft.trim()}>
                <Send size={13} />
              </button>
            </form>
          </section>

          {/* Right Column: Custom Lab Agents & Evaluation Bench */}
          <section style={{ display: "flex", flexDirection: "column", gap: 20 }}>
            <div className="card">
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 12 }}>
                <h3 style={{ margin: 0 }}>Custom Lab Agents ({labAgents.length})</h3>
                <button type="button" className="btn-ghost" onClick={refreshLabAgents} style={{ fontSize: 12 }}>
                  <RefreshCw size={12} /> Refresh
                </button>
              </div>

              {labAgents.length === 0 ? (
                <p style={{ fontSize: 12.5, color: "var(--text-muted)", padding: "16px 0" }}>
                  No custom agents created in Lab yet. Use the synthesizer or builder above.
                </p>
              ) : (
                <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                  {labAgents.map((ag) => (
                    <div
                      key={ag.id}
                      style={{
                        padding: "12px 14px",
                        borderRadius: "var(--radius-sm)",
                        background: "var(--bg-elevated)",
                        border: "1px solid var(--border-subtle)",
                      }}
                    >
                      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 6 }}>
                        <span style={{ fontWeight: 600, fontSize: 14 }}>{ag.name}</span>
                        <span className="badge badge-warning" style={{ fontSize: 10, textTransform: "capitalize" }}>
                          {ag.status} (v{ag.version})
                        </span>
                      </div>

                      <p style={{ fontSize: 12.5, color: "var(--text-secondary)", marginBottom: 10 }}>
                        {ag.description}
                      </p>

                      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                        <span style={{ fontSize: 11, fontFamily: "monospace", color: "var(--text-muted)" }}>
                          Mode: {ag.mode}
                        </span>

                        <div style={{ display: "flex", gap: 6 }}>
                          <button
                            type="button"
                            className="btn-secondary"
                            onClick={() => onEvaluate(ag.id)}
                            disabled={evaluatingId === ag.id}
                            style={{ fontSize: 11, padding: "4px 8px" }}
                          >
                            <Play size={11} className={evaluatingId === ag.id ? "animate-spin" : ""} />
                            {evaluatingId === ag.id ? "Testing..." : "Run Tests"}
                          </button>

                          <button
                            type="button"
                            className="btn-ghost"
                            onClick={() => onViewEvaluation(ag.id)}
                            style={{ fontSize: 11, padding: "4px 8px" }}
                          >
                            View Scores
                          </button>

                          <button
                            type="button"
                            className="btn-primary"
                            onClick={() => onDeploy(ag.id)}
                            disabled={busy}
                            style={{ fontSize: 11, padding: "4px 8px" }}
                            title="Promote to production DEPLOYED status"
                          >
                            <Rocket size={11} /> Deploy
                          </button>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Test Results Breakdown */}
            {evalResult && (
              <div className="card-elevated">
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 12 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <h3 style={{ margin: 0 }}>Contract Benchmark Results</h3>
                    <span
                      className={`badge ${evalResult.passed ? "badge-success" : "badge-danger"}`}
                    >
                      {evalResult.passed ? "PASSED" : "FAILED"}
                    </span>
                  </div>
                  <span className="tabular-nums" style={{ fontSize: 14, fontWeight: 600 }}>
                    Score: {(evalResult.score * 100).toFixed(0)}% (Min: {(evalResult.min_score * 100).toFixed(0)}%)
                  </span>
                </div>

                <div style={{ display: "flex", flexDirection: "column", gap: 6, maxHeight: 220, overflowY: "auto" }}>
                  {evalResult.tests.map((t, idx) => (
                    <div
                      key={idx}
                      style={{
                        padding: "8px 10px",
                        borderRadius: "var(--radius-sm)",
                        background: "var(--bg-surface)",
                        border: "1px solid var(--border-subtle)",
                        fontSize: 12,
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "space-between",
                      }}
                    >
                      <div>
                        <span style={{ fontWeight: 500 }}>{t.name}</span>
                        {t.detail && <p style={{ margin: "2px 0 0", color: "var(--text-muted)", fontSize: 11 }}>{t.detail}</p>}
                      </div>
                      <span className={`badge ${t.passed ? "badge-success" : "badge-danger"}`}>
                        {(t.score * 100).toFixed(0)}%
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </section>
        </div>
      </main>
    </div>
  );
}
