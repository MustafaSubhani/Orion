"use client";

import { useEffect, useState, useRef } from "react";
import { useRouter } from "next/navigation";
import {
  Database,
  UploadCloud,
  Trash2,
  RefreshCw,
  Table as TableIcon,
  FileText,
  AlertCircle,
  CheckCircle2,
  HardDrive,
  Layers,
} from "lucide-react";
import { Chrome } from "@/components/Chrome";
import {
  AuthError,
  deleteKBResource,
  getKBCatalog,
  ingestFiles,
  me,
  reindexKB,
} from "@/lib/api";
import type { KBCatalog, Session } from "@/lib/types";

export default function KnowledgePage() {
  const router = useRouter();
  const [session, setSession] = useState<Session | null>(null);
  const [catalog, setCatalog] = useState<KBCatalog | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [uploadFiles, setUploadFiles] = useState<File[]>([]);
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    let cancelled = false;
    me()
      .then((curr) => {
        if (cancelled) return;
        setSession(curr);
        loadCatalog();
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

  async function loadCatalog() {
    setLoading(true);
    setError("");
    try {
      const data = await getKBCatalog();
      setCatalog(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load KB catalog");
    } finally {
      setLoading(false);
    }
  }

  async function onUpload() {
    if (!uploadFiles.length || !session) return;
    setBusy(true);
    setError("");
    setSuccess("");
    try {
      const report = await ingestFiles(uploadFiles, session.username);
      setSuccess(report);
      setUploadFiles([]);
      await loadCatalog();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setBusy(false);
    }
  }

  async function onDelete(resourceId: string) {
    if (!confirm("Are you sure you want to remove this resource from the live KB?")) return;
    setBusy(true);
    setError("");
    try {
      await deleteKBResource(resourceId);
      await loadCatalog();
      setSuccess("Resource deleted successfully");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete resource");
    } finally {
      setBusy(false);
    }
  }

  async function onReindex() {
    setBusy(true);
    setError("");
    setSuccess("");
    try {
      const res = await reindexKB();
      setSuccess(`Knowledge Base re-indexed successfully (${res.reindexed || 0} items)`);
      await loadCatalog();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Reindex failed");
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
      <Chrome session={session} note="Live KB • Vector & Tabular Lake" />

      <main style={{ padding: "32px var(--space-8)", maxWidth: 1200, margin: "0 auto", width: "100%" }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 24 }}>
          <div>
            <h1 style={{ margin: 0 }}>Knowledge Base Studio</h1>
            <p style={{ marginTop: 4, marginBottom: 0 }}>
              Manage documents, tabular data, and external vectors attached to the live lake.
            </p>
          </div>

          <div style={{ display: "flex", gap: 10 }}>
            {isAdmin && (
              <button
                type="button"
                className="btn-secondary"
                onClick={onReindex}
                disabled={busy}
                title="Rebuild vector indexes"
              >
                <RefreshCw size={14} className={busy ? "animate-spin" : ""} /> Reindex Lake
              </button>
            )}
            <button
              type="button"
              className="btn-secondary"
              onClick={loadCatalog}
              disabled={loading}
              title="Refresh resources list"
            >
              <RefreshCw size={14} /> Refresh
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

        {/* Upload Dropzone */}
        <section className="card-elevated" style={{ marginBottom: 32 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
            <UploadCloud size={18} color="#3b82f6" />
            <h3 style={{ margin: 0 }}>Ingest Documents & Datasets</h3>
          </div>
          <p style={{ fontSize: 13, color: "var(--text-secondary)", marginBottom: 16 }}>
            Upload PDF, DOCX, CSV, SQLite, JSON, or text files into the tenant&apos;s live retrieval index.
          </p>

          <input
            ref={fileInputRef}
            type="file"
            multiple
            hidden
            onChange={(e) => setUploadFiles(Array.from(e.target.files || []))}
          />

          <div
            onClick={() => fileInputRef.current?.click()}
            style={{
              border: "2px dashed var(--border-default)",
              borderRadius: "var(--radius-md)",
              padding: "28px 20px",
              textAlign: "center",
              cursor: "pointer",
              background: "var(--bg-surface)",
              transition: "border-color 0.2s",
            }}
          >
            <FileText size={28} style={{ color: "var(--text-muted)", margin: "0 auto 8px" }} />
            <p style={{ margin: 0, fontWeight: 500, color: "var(--text-primary)" }}>
              {uploadFiles.length
                ? `${uploadFiles.length} file(s) ready to ingest`
                : "Click or drag files here to upload"}
            </p>
            <p style={{ margin: "4px 0 0", fontSize: 12, color: "var(--text-muted)" }}>
              Max 20 files per request. Up to 10 MB per document.
            </p>
          </div>

          {uploadFiles.length > 0 && (
            <div style={{ marginTop: 16, display: "flex", alignItems: "center", justifyContent: "space-between" }}>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                {uploadFiles.map((f, i) => (
                  <span key={i} className="badge badge-neutral tabular-nums">
                    {f.name} ({(f.size / 1024).toFixed(0)} KB)
                  </span>
                ))}
              </div>

              <div style={{ display: "flex", gap: 8 }}>
                <button
                  type="button"
                  className="btn-ghost"
                  onClick={() => setUploadFiles([])}
                  disabled={busy}
                >
                  Cancel
                </button>
                <button
                  type="button"
                  className="btn-primary"
                  onClick={onUpload}
                  disabled={busy}
                >
                  {busy ? "Ingesting..." : "Start Ingestion"}
                </button>
              </div>
            </div>
          )}
        </section>

        {/* Resources & Tables Grid */}
        <div style={{ display: "grid", gridTemplateColumns: "1fr 340px", gap: 24 }}>
          {/* Main Table: Resources */}
          <section>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 12 }}>
              <h3 style={{ margin: 0, display: "flex", alignItems: "center", gap: 6 }}>
                <Layers size={16} /> Live Ingested Resources
              </h3>
              <span className="tabular-nums" style={{ fontSize: 12, color: "var(--text-muted)" }}>
                {catalog?.resources.length || 0} total
              </span>
            </div>

            <div className="table-container">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Filename</th>
                    <th>Kind</th>
                    <th>Chunks</th>
                    <th>Uploader</th>
                    <th style={{ textAlign: "right" }}>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {!catalog?.resources || catalog.resources.length === 0 ? (
                    <tr>
                      <td colSpan={5} style={{ textAlign: "center", padding: "32px 16px", color: "var(--text-muted)" }}>
                        No resources in live KB yet. Upload files above.
                      </td>
                    </tr>
                  ) : (
                    catalog.resources.map((item) => (
                      <tr key={item.id}>
                        <td style={{ fontWeight: 500 }}>{item.filename || item.id}</td>
                        <td>
                          <span className="badge badge-neutral" style={{ textTransform: "uppercase" }}>
                            {item.kind || "doc"}
                          </span>
                        </td>
                        <td className="tabular-nums">{item.chunks || 0}</td>
                        <td style={{ color: "var(--text-secondary)" }}>{item.uploaded_by || "system"}</td>
                        <td style={{ textAlign: "right" }}>
                          {(isAdmin || item.uploaded_by === session.username) && (
                            <button
                              type="button"
                              className="btn-ghost"
                              onClick={() => onDelete(item.id)}
                              disabled={busy}
                              style={{ color: "var(--status-danger)", padding: "4px 8px" }}
                              title="Delete resource"
                            >
                              <Trash2 size={13} />
                            </button>
                          )}
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </section>

          {/* Sidebar: Database Tables & Connectors */}
          <section style={{ display: "flex", flexDirection: "column", gap: 20 }}>
            {/* Tables Card */}
            <div className="card">
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
                <TableIcon size={16} color="#10b981" />
                <h3 style={{ margin: 0 }}>SQLite Lake Tables</h3>
              </div>
              <p style={{ fontSize: 12.5, color: "var(--text-secondary)", marginBottom: 12 }}>
                Relational tables queried automatically by the SQL agent.
              </p>
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                {!catalog?.tables || catalog.tables.length === 0 ? (
                  <p style={{ fontSize: 12, color: "var(--text-muted)" }}>No relational tables indexed.</p>
                ) : (
                  catalog.tables.map((tbl, i) => (
                    <div
                      key={i}
                      style={{
                        padding: "6px 10px",
                        borderRadius: "var(--radius-sm)",
                        background: "var(--bg-elevated)",
                        border: "1px solid var(--border-subtle)",
                        fontSize: 12.5,
                        fontFamily: "monospace",
                        color: "var(--text-primary)",
                      }}
                    >
                      {tbl}
                    </div>
                  ))
                )}
              </div>
            </div>

            {/* Connectors Card */}
            <div className="card">
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
                <HardDrive size={16} color="#f59e0b" />
                <h3 style={{ margin: 0 }}>Lake Connectors</h3>
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                {!catalog?.connectors || catalog.connectors.length === 0 ? (
                  <p style={{ fontSize: 12, color: "var(--text-muted)" }}>Default filesystem lake connector active.</p>
                ) : (
                  catalog.connectors.map((c) => (
                    <div
                      key={c.id}
                      style={{
                        padding: "8px 10px",
                        borderRadius: "var(--radius-sm)",
                        background: "var(--bg-elevated)",
                        border: "1px solid var(--border-subtle)",
                        fontSize: 12.5,
                      }}
                    >
                      <div style={{ fontWeight: 500, color: "var(--text-primary)" }}>{c.name}</div>
                      <div style={{ fontSize: 11, color: "var(--text-muted)" }}>Type: {c.type}</div>
                    </div>
                  ))
                )}
              </div>
            </div>
          </section>
        </div>
      </main>
    </div>
  );
}
