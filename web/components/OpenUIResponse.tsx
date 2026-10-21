"use client";

import React, { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Check, Copy, Terminal, ExternalLink, BarChart3, Download } from "lucide-react";

type Props = {
  content: string;
};

export function OpenUIResponse({ content }: Props) {
  const [copiedCodeId, setCopiedCodeId] = useState<string | null>(null);

  function copyCode(code: string, id: string) {
    navigator.clipboard.writeText(code);
    setCopiedCodeId(id);
    setTimeout(() => setCopiedCodeId(null), 2000);
  }

  return (
    <div className="openui-markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          h1({ children }) {
            return (
              <h1 style={{ fontSize: "1.45rem", fontWeight: 650, marginTop: "1.4rem", marginBottom: "0.6rem", color: "var(--text-primary)", letterSpacing: "-0.02em" }}>
                {children}
              </h1>
            );
          },
          h2({ children }) {
            return (
              <h2 style={{ fontSize: "1.25rem", fontWeight: 600, marginTop: "1.3rem", marginBottom: "0.5rem", color: "var(--text-primary)", letterSpacing: "-0.015em" }}>
                {children}
              </h2>
            );
          },
          h3({ children }) {
            return (
              <h3 style={{ fontSize: "1.08rem", fontWeight: 600, marginTop: "1.1rem", marginBottom: "0.4rem", color: "#60a5fa" }}>
                {children}
              </h3>
            );
          },
          p({ children }) {
            return <p style={{ marginBottom: "0.75rem", lineHeight: 1.68, color: "var(--text-primary)" }}>{children}</p>;
          },
          ul({ children }) {
            return <ul style={{ paddingLeft: "1.4rem", marginBottom: "0.85rem", display: "flex", flexDirection: "column", gap: "0.35rem" }}>{children}</ul>;
          },
          ol({ children }) {
            return <ol style={{ paddingLeft: "1.4rem", marginBottom: "0.85rem", display: "flex", flexDirection: "column", gap: "0.35rem" }}>{children}</ol>;
          },
          li({ children }) {
            return <li style={{ lineHeight: 1.62, color: "var(--text-primary)" }}>{children}</li>;
          },
          hr() {
            return <hr style={{ border: "none", borderTop: "1px solid var(--border-subtle)", margin: "1.25rem 0" }} />;
          },
          blockquote({ children }) {
            return (
              <blockquote
                style={{
                  borderLeft: "3px solid #3b82f6",
                  background: "rgba(59, 130, 246, 0.06)",
                  padding: "0.6rem 1rem",
                  borderRadius: "0 8px 8px 0",
                  margin: "0.9rem 0",
                  color: "var(--text-secondary)",
                  fontSize: "0.92rem",
                }}
              >
                {children}
              </blockquote>
            );
          },
          table({ children }) {
            return (
              <div style={{ overflowX: "auto", margin: "1rem 0", borderRadius: "8px", border: "1px solid var(--border-default)", background: "var(--bg-surface)" }}>
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.88rem", textAlign: "left" }}>
                  {children}
                </table>
              </div>
            );
          },
          thead({ children }) {
            return <thead style={{ background: "var(--bg-elevated)", borderBottom: "1px solid var(--border-default)" }}>{children}</thead>;
          },
          th({ children }) {
            return <th style={{ padding: "9px 14px", fontWeight: 600, color: "var(--text-muted)", textTransform: "uppercase", fontSize: "0.75rem", letterSpacing: "0.04em" }}>{children}</th>;
          },
          td({ children }) {
            return <td style={{ padding: "10px 14px", borderBottom: "1px solid var(--border-subtle)", color: "var(--text-primary)" }}>{children}</td>;
          },
          img({ src, alt }) {
            const rawSrc = String(src || "");
            const isLocalGraph = rawSrc.startsWith("/graphs/");
            // Proxy through /graphs/ or absolute backend fallback if loaded externally
            const resolvedSrc = isLocalGraph ? rawSrc : rawSrc;
            const fullBackendUrl = isLocalGraph ? `http://127.0.0.1:8000${rawSrc}` : rawSrc;

            return (
              <div
                style={{
                  margin: "1.2rem 0",
                  borderRadius: "12px",
                  overflow: "hidden",
                  border: "1px solid var(--border-default)",
                  background: "#0c0c10",
                  boxShadow: "0 8px 24px rgba(0, 0, 0, 0.35)",
                }}
              >
                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    padding: "8px 14px",
                    background: "rgba(255, 255, 255, 0.03)",
                    borderBottom: "1px solid var(--border-subtle)",
                  }}
                >
                  <div style={{ display: "flex", alignItems: "center", gap: 7, fontSize: "0.8rem", fontWeight: 600, color: "var(--text-primary)" }}>
                    <BarChart3 size={15} color="#60a5fa" />
                    <span>{alt || "Visualization Chart"}</span>
                  </div>
                  <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                    <a
                      href={fullBackendUrl}
                      download
                      style={{
                        fontSize: "0.75rem",
                        color: "var(--text-muted)",
                        display: "flex",
                        alignItems: "center",
                        gap: 4,
                        textDecoration: "none",
                        transition: "color 0.15s ease",
                      }}
                      title="Download full-resolution image"
                    >
                      <Download size={13} /> Save
                    </a>
                    <a
                      href={fullBackendUrl}
                      target="_blank"
                      rel="noreferrer"
                      style={{
                        fontSize: "0.75rem",
                        color: "#60a5fa",
                        display: "flex",
                        alignItems: "center",
                        gap: 4,
                        textDecoration: "none",
                      }}
                      title="Open full view in new tab"
                    >
                      Full View <ExternalLink size={12} />
                    </a>
                  </div>
                </div>
                <div
                  style={{
                    padding: 16,
                    display: "flex",
                    justifyContent: "center",
                    alignItems: "center",
                    background: "#08080c",
                    minHeight: 220,
                  }}
                >
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src={resolvedSrc}
                    alt={alt || "Generated Chart"}
                    style={{
                      maxWidth: "100%",
                      height: "auto",
                      borderRadius: "8px",
                      display: "block",
                      border: "1px solid rgba(255, 255, 255, 0.06)",
                    }}
                    onError={(e) => {
                      // Automatic fallback directly to backend URL if relative rewrite has latency
                      const target = e.currentTarget;
                      if (isLocalGraph && target.src !== fullBackendUrl) {
                        target.src = fullBackendUrl;
                      }
                    }}
                    loading="lazy"
                  />
                </div>
              </div>
            );
          },
          code({ className, children, ...props }) {
            const isInline = !className && typeof children === "string" && !children.includes("\n");
            if (isInline) {
              return (
                <code
                  style={{
                    background: "rgba(255, 255, 255, 0.08)",
                    border: "1px solid rgba(255, 255, 255, 0.08)",
                    padding: "2px 6px",
                    borderRadius: "4px",
                    fontSize: "0.85em",
                    fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
                    color: "#93c5fd",
                  }}
                  {...props}
                >
                  {children}
                </code>
              );
            }

            const codeStr = String(children).replace(/\n$/, "");
            const codeId = `code-${Math.random().toString(36).slice(2, 8)}`;
            const lang = className ? className.replace("language-", "") : "code";

            return (
              <div style={{ margin: "1rem 0", borderRadius: "8px", overflow: "hidden", border: "1px solid var(--border-default)", background: "#0c0c10" }}>
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "6px 12px", background: "rgba(255, 255, 255, 0.03)", borderBottom: "1px solid var(--border-subtle)" }}>
                  <div style={{ display: "flex", alignItems: "center", gap: "6px", fontSize: "0.75rem", color: "var(--text-muted)", fontFamily: "monospace", textTransform: "uppercase" }}>
                    <Terminal size={12} /> {lang}
                  </div>
                  <button
                    type="button"
                    onClick={() => copyCode(codeStr, codeId)}
                    style={{ fontSize: "0.75rem", color: "var(--text-secondary)", background: "transparent", border: "none", cursor: "pointer", display: "flex", alignItems: "center", gap: "4px" }}
                  >
                    {copiedCodeId === codeId ? (
                      <>
                        <Check size={12} color="#10b981" /> Copied
                      </>
                    ) : (
                      <>
                        <Copy size={12} /> Copy
                      </>
                    )}
                  </button>
                </div>
                <pre style={{ margin: 0, padding: "12px 16px", overflowX: "auto", fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace", fontSize: "0.85rem", lineHeight: 1.55, color: "#f3f4f6" }}>
                  <code>{codeStr}</code>
                </pre>
              </div>
            );
          },
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}
