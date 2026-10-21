"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  Server,
  RefreshCw,
  HardDrive,
  CheckCircle2,
  AlertTriangle,
  Play,
  Wrench,
  Radio,
} from "lucide-react";
import { Chrome } from "@/components/Chrome";
import {
  AuthError,
  getMcpStatus,
  listConnectors,
  me,
  reloadConnectors,
  reloadMcp,
  testConnector,
} from "@/lib/api";
import type { MCPBridgeStatus, Session } from "@/lib/types";

export default function ConnectorsPage() {
  const router = useRouter();
  const [session, setSession] = useState<Session | null>(null);
  const [mcpStatus, setMcpStatus] = useState<MCPBridgeStatus | null>(null);
  const [connectors, setConnectors] = useState<Array<{ id: string; name: string; type: string; config?: Record<string, unknown> }>>([]);
  const [testResults, setTestResults] = useState<Record<string, { ok: boolean; message?: string }>>({});
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
        loadData();
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

  async function loadData() {
    setLoading(true);
    setError("");
    try {
      const [mcp, conn] = await Promise.all([
        getMcpStatus().catch(() => null),
        listConnectors().catch(() => []),
      ]);
      setMcpStatus(mcp);
      setConnectors(conn);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load connectors");
    } finally {
      setLoading(false);
    }
  }

  async function onReloadMcp() {
    setBusy(true);
    setError("");
    setSuccess("");
    try {
      await reloadMcp();
      setSuccess("MCP servers reconnected and tools refreshed");
      await loadData();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to reload MCP");
    } finally {
      setBusy(false);
    }
  }

  async function onReloadConnectors() {
    setBusy(true);
    setError("");
    setSuccess("");
    try {
      const res = await reloadConnectors();
      setSuccess(`Reloaded ${res.loaded || 0} external data connectors`);
      await loadData();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to reload connectors");
    } finally {
      setBusy(false);
    }
  }

  async function onTestConnector(id: string) {
    try {
      const res = await testConnector(id);
      setTestResults((prev) => ({ ...prev, [id]: res }));
    } catch (err) {
      setTestResults((prev) => ({
        ...prev,
        [id]: { ok: false, message: err instanceof Error ? err.message : "Test failed" },
      }));
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
      <Chrome session={session} note="Model Context Protocol & Lake Connectors" />

      <main style={{ padding: "32px var(--space-8)", maxWidth: 1100, margin: "0 auto", width: "100%" }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 24 }}>
          <div>
            <h1 style={{ margin: 0, display: "flex", alignItems: "center", gap: 10 }}>
              <Server size={24} color="#3b82f6" /> MCP Bridge & Data Connectors
            </h1>
            <p style={{ marginTop: 4, marginBottom: 0 }}>
              Live Model Context Protocol server links, dynamically registered tools, and external lake connectors.
            </p>
          </div>

          <div style={{ display: "flex", gap: 10 }}>
            <button
              type="button"
              className="btn-secondary"
              onClick={onReloadMcp}
              disabled={busy}
              title="Reconnect all MCP servers"
            >
              <RefreshCw size={14} className={busy ? "animate-spin" : ""} /> Reload MCP
            </button>

            <button
              type="button"
              className="btn-secondary"
              onClick={onReloadConnectors}
              disabled={busy}
              title="Reload configured data connectors"
            >
              <RefreshCw size={14} /> Reload Connectors
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

        {/* MCP Servers Section */}
        <section style={{ marginBottom: 32 }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 12 }}>
            <h3 style={{ margin: 0, display: "flex", alignItems: "center", gap: 8 }}>
              <Radio size={16} color="#3b82f6" /> Model Context Protocol (MCP) Bridge
            </h3>
            <span className="badge badge-neutral tabular-nums">
              {mcpStatus?.servers.length || 0} server{mcpStatus?.servers.length !== 1 ? "s" : ""}
            </span>
          </div>

          {!mcpStatus || mcpStatus.servers.length === 0 ? (
            <div className="card" style={{ padding: "32px 20px", textAlign: "center", color: "var(--text-muted)" }}>
              No external MCP servers configured in config/mcp_servers.json.
            </div>
          ) : (
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(320px, 1fr))", gap: 16 }}>
              {mcpStatus.servers.map((srv) => {
                const isOnline = srv.status === "connected";
                return (
                  <div key={srv.name} className="card-elevated">
                    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 10 }}>
                      <span style={{ fontWeight: 600, fontSize: 14 }}>{srv.name}</span>
                      <span
                        className={`badge ${isOnline ? "badge-success" : "badge-danger"}`}
                        style={{ textTransform: "capitalize" }}
                      >
                        <span className={`status-dot ${isOnline ? "success" : "danger"}`} />
                        {srv.status}
                      </span>
                    </div>

                    {srv.error && (
                      <p style={{ fontSize: 12, color: "var(--status-danger)", marginBottom: 8 }}>
                        {srv.error}
                      </p>
                    )}

                    <div style={{ fontSize: 11.5, fontWeight: 600, color: "var(--text-muted)", textTransform: "uppercase", marginBottom: 6 }}>
                      Exposed Tools ({srv.tool_names?.length || 0})
                    </div>
                    <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                      {(!srv.tool_names || srv.tool_names.length === 0) ? (
                        <span style={{ fontSize: 12, color: "var(--text-muted)" }}>None exposed</span>
                      ) : (
                        srv.tool_names.map((t, idx) => (
                          <span key={idx} className="badge badge-neutral" style={{ fontFamily: "monospace", fontSize: 11 }}>
                            <Wrench size={10} /> {t}
                          </span>
                        ))
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </section>

        {/* Data Lake Connectors Section */}
        <section>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 12 }}>
            <h3 style={{ margin: 0, display: "flex", alignItems: "center", gap: 8 }}>
              <HardDrive size={16} color="#10b981" /> Data Lake Connectors
            </h3>
            <span className="badge badge-neutral tabular-nums">
              {connectors.length} configured
            </span>
          </div>

          {connectors.length === 0 ? (
            <div className="card" style={{ padding: "32px 20px", textAlign: "center", color: "var(--text-muted)" }}>
              Standard filesystem lake connector active.
            </div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              {connectors.map((c) => {
                const test = testResults[c.id];
                return (
                  <div
                    key={c.id}
                    className="card"
                    style={{
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "space-between",
                    }}
                  >
                    <div>
                      <div style={{ fontWeight: 600, fontSize: 14 }}>{c.name}</div>
                      <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
                        Type: <span style={{ fontFamily: "monospace" }}>{c.type}</span> • ID: {c.id}
                      </div>

                      {test && (
                        <div
                          style={{
                            marginTop: 6,
                            fontSize: 12,
                            display: "flex",
                            alignItems: "center",
                            gap: 6,
                            color: test.ok ? "var(--status-success)" : "var(--status-danger)",
                          }}
                        >
                          {test.ok ? <CheckCircle2 size={13} /> : <AlertTriangle size={13} />}
                          <span>{test.message || (test.ok ? "Connection test passed" : "Connection failed")}</span>
                        </div>
                      )}
                    </div>

                    <button
                      type="button"
                      className="btn-secondary"
                      onClick={() => onTestConnector(c.id)}
                      style={{ fontSize: 12 }}
                    >
                      <Play size={12} /> Test Connectivity
                    </button>
                  </div>
                );
              })}
            </div>
          )}
        </section>
      </main>
    </div>
  );
}
