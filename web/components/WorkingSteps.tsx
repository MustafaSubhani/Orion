"use client";

import React, { useState } from "react";
import {
  ChevronDown,
  ChevronRight,
  Sparkles,
  CheckCircle2,
  RotateCcw,
  RefreshCw,
  Cpu,
  Clock,
  Layers,
  Check,
} from "lucide-react";

export type WorkingStep = {
  id: string;
  type: string;
  title: string;
  agent?: string;
  description?: string;
  status: "pending" | "running" | "completed" | "healed";
  durationMs?: number;
};

type Props = {
  steps: WorkingStep[];
  isStreaming?: boolean;
  agentsUsed?: string[];
  requestId?: string;
  elapsedSeconds?: number;
};

// Agent visual identity mapping
const AGENT_COLORS: Record<string, { bg: string; text: string; border: string }> = {
  finance: { bg: "rgba(16, 185, 129, 0.12)", text: "#10b981", border: "rgba(16, 185, 129, 0.25)" },
  hr: { bg: "rgba(168, 85, 247, 0.12)", text: "#a855f7", border: "rgba(168, 85, 247, 0.25)" },
  scheduler: { bg: "rgba(245, 158, 11, 0.12)", text: "#f59e0b", border: "rgba(245, 158, 11, 0.25)" },
  chart: { bg: "rgba(59, 130, 246, 0.12)", text: "#3b82f6", border: "rgba(59, 130, 246, 0.25)" },
  sql: { bg: "rgba(239, 68, 68, 0.12)", text: "#ef4444", border: "rgba(239, 68, 68, 0.25)" },
  orchestrator: { bg: "rgba(99, 102, 241, 0.12)", text: "#818cf8", border: "rgba(99, 102, 241, 0.25)" },
};

export function WorkingSteps({
  steps,
  isStreaming = false,
  agentsUsed = [],
  requestId,
  elapsedSeconds,
}: Props) {
  // If streaming, expanded by default; once completed, collapsed by default just like Claude
  const [expanded, setExpanded] = useState<boolean>(isStreaming);

  if (!steps || steps.length === 0) return null;

  const completedSteps = steps.filter((s) => s.status === "completed" || s.status === "healed").length;
  const hasHealEvent = steps.some((s) => s.status === "healed" || s.type === "backtrack");

  return (
    <div
      style={{
        margin: "0 0 16px 0",
        borderRadius: "8px",
        border: "1px solid var(--border-default)",
        background: "rgba(24, 24, 30, 0.6)",
        overflow: "hidden",
        transition: "border-color 0.2s",
      }}
    >
      {/* Claude-style collapsible header bar */}
      <button
        type="button"
        onClick={() => setExpanded(!expanded)}
        style={{
          width: "100%",
          padding: "8px 14px",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          background: expanded ? "rgba(255, 255, 255, 0.03)" : "transparent",
          border: "none",
          borderBottom: expanded ? "1px solid var(--border-subtle)" : "none",
          cursor: "pointer",
          fontSize: "12.5px",
          color: "var(--text-secondary)",
          textAlign: "left",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          {isStreaming ? (
            <span
              style={{
                width: 8,
                height: 8,
                borderRadius: "50%",
                background: "#3b82f6",
                boxShadow: "0 0 8px #3b82f6",
                display: "inline-block",
                animation: "pulse 1.5s infinite",
              }}
            />
          ) : hasHealEvent ? (
            <RotateCcw size={13} color="#f59e0b" />
          ) : (
            <Sparkles size={13} color="#93c5fd" />
          )}

          <span style={{ fontWeight: 550, color: isStreaming ? "#93c5fd" : "var(--text-primary)" }}>
            {isStreaming
              ? "Thinking & coordinating specialists..."
              : `Thought for ${elapsedSeconds ? `${elapsedSeconds.toFixed(1)}s` : "a few seconds"} • ${steps.length} working steps`}
          </span>

          {agentsUsed.length > 0 && !isStreaming && (
            <span style={{ fontSize: "11px", color: "var(--text-muted)" }}>
              ({agentsUsed.join(", ")})
            </span>
          )}

          {hasHealEvent && (
            <span
              style={{
                fontSize: "10.5px",
                padding: "1px 6px",
                borderRadius: "4px",
                background: "rgba(245, 158, 11, 0.15)",
                color: "#f59e0b",
                fontWeight: 500,
              }}
            >
              Self-Healed
            </span>
          )}
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: "8px", color: "var(--text-muted)" }}>
          <span style={{ fontSize: "11px" }}>
            {expanded ? "Hide thinking" : "View steps"}
          </span>
          {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </div>
      </button>

      {/* Expanded Step-by-Step execution details */}
      {expanded && (
        <div style={{ padding: "12px 16px", display: "flex", flexDirection: "column", gap: "10px" }}>
          {steps.map((step, idx) => {
            const isLast = idx === steps.length - 1;
            const isCurrent = step.status === "running";
            const isHealed = step.status === "healed" || step.type === "backtrack";
            const agentStyle = step.agent ? AGENT_COLORS[step.agent.toLowerCase()] : null;

            return (
              <div
                key={step.id || idx}
                style={{
                  display: "flex",
                  alignItems: "flex-start",
                  gap: "10px",
                  fontSize: "12.5px",
                }}
              >
                {/* Status icon node */}
                <div
                  style={{
                    width: 20,
                    height: 20,
                    borderRadius: "50%",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    flexShrink: 0,
                    marginTop: "1px",
                    background: isCurrent
                      ? "rgba(59, 130, 246, 0.15)"
                      : isHealed
                      ? "rgba(245, 158, 11, 0.15)"
                      : "rgba(16, 185, 129, 0.15)",
                    color: isCurrent ? "#60a5fa" : isHealed ? "#f59e0b" : "#10b981",
                  }}
                >
                  {isCurrent ? (
                    <RefreshCw size={11} style={{ animation: "spin 1.2s linear infinite" }} />
                  ) : isHealed ? (
                    <RotateCcw size={11} />
                  ) : (
                    <Check size={11} />
                  )}
                </div>

                {/* Step content */}
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap" }}>
                    <span style={{ fontWeight: 550, color: "var(--text-primary)" }}>
                      {step.title}
                    </span>

                    {step.agent && (
                      <span
                        style={{
                          fontSize: "10.5px",
                          fontWeight: 600,
                          padding: "1px 7px",
                          borderRadius: "4px",
                          textTransform: "uppercase",
                          letterSpacing: "0.04em",
                          background: agentStyle?.bg || "rgba(255, 255, 255, 0.08)",
                          color: agentStyle?.text || "var(--text-secondary)",
                          border: `1px solid ${agentStyle?.border || "transparent"}`,
                        }}
                      >
                        {step.agent}
                      </span>
                    )}

                    {step.durationMs && (
                      <span className="tabular-nums" style={{ fontSize: "11px", color: "var(--text-muted)" }}>
                        {step.durationMs}ms
                      </span>
                    )}
                  </div>

                  {step.description && (
                    <p style={{ margin: "3px 0 0", color: "var(--text-secondary)", fontSize: "12px", lineHeight: 1.45 }}>
                      {step.description}
                    </p>
                  )}
                </div>
              </div>
            );
          })}

          {requestId && (
            <div
              style={{
                marginTop: "4px",
                paddingTop: "8px",
                borderTop: "1px solid var(--border-subtle)",
                fontSize: "11px",
                fontFamily: "monospace",
                color: "var(--text-muted)",
                display: "flex",
                justifyContent: "space-between",
              }}
            >
              <span>Request ID: {requestId}</span>
              <span>Self-Healing Engine: Active</span>
            </div>
          )}
        </div>
      )}

      <style jsx>{`
        @keyframes pulse {
          0%, 100% { opacity: 1; transform: scale(1); }
          50% { opacity: 0.5; transform: scale(0.85); }
        }
        @keyframes spin {
          from { transform: rotate(0deg); }
          to { transform: rotate(360deg); }
        }
      `}</style>
    </div>
  );
}
