"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { Sparkles, MessageSquare, FlaskConical } from "lucide-react";
import type { HealthStatus, Session } from "@/lib/types";
import { getHealth } from "@/lib/api";

type Props = {
  session: Session;
  note?: string;
};

export function Chrome({ session }: Props) {
  const pathname = usePathname();
  const [health, setHealth] = useState<HealthStatus | null>(null);

  useEffect(() => {
    getHealth()
      .then(setHealth)
      .catch(() => undefined);
  }, []);

  const isChatActive = pathname === "/app";
  const isLabActive = pathname === "/app/lab";

  return (
    <header
      style={{
        position: "sticky",
        top: 0,
        zIndex: 50,
        height: 48,
        padding: "0 20px",
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        background: "rgba(9, 9, 11, 0.95)",
        backdropFilter: "blur(16px)",
        borderBottom: "1px solid var(--border-subtle)",
      }}
    >
      {/* Left: Brand Monogram */}
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <Link
          href="/app"
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            fontWeight: 650,
            fontSize: 14,
            letterSpacing: "-0.015em",
            color: "var(--text-primary)",
          }}
        >
          <div
            style={{
              width: 24,
              height: 24,
              borderRadius: 6,
              background: "#181824",
              border: "1px solid rgba(59, 130, 246, 0.4)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              color: "#3b82f6",
            }}
          >
            <Sparkles size={13} strokeWidth={2} />
          </div>
          <span>Orion</span>
          <span
            style={{
              fontSize: 10,
              fontWeight: 600,
              padding: "1px 5px",
              borderRadius: 4,
              background: "rgba(255, 255, 255, 0.06)",
              color: "var(--text-muted)",
              letterSpacing: "0.06em",
              textTransform: "uppercase",
            }}
          >
            Harness
          </span>
        </Link>
      </div>

      {/* Center: Top bar has ONLY Chat and Lab (Lab only visible to super_user admin) */}
      <nav
        style={{
          display: "flex",
          alignItems: "center",
          background: "var(--bg-elevated)",
          padding: "2px",
          borderRadius: "8px",
          border: "1px solid var(--border-subtle)",
          gap: "2px",
        }}
      >
        <Link
          href="/app"
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 6,
            padding: "4px 14px",
            borderRadius: "6px",
            fontSize: "12.5px",
            fontWeight: 500,
            color: isChatActive ? "var(--text-primary)" : "var(--text-secondary)",
            background: isChatActive ? "rgba(255, 255, 255, 0.08)" : "transparent",
            boxShadow: isChatActive ? "0 1px 2px rgba(0,0,0,0.4)" : "none",
            transition: "all 0.15s",
          }}
        >
          <MessageSquare size={13} /> Chat
        </Link>

        {session.role === "super_user" && (
          <Link
            href="/app/lab"
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 6,
              padding: "4px 14px",
              borderRadius: "6px",
              fontSize: "12.5px",
              fontWeight: 500,
              color: isLabActive ? "var(--text-primary)" : "var(--text-secondary)",
              background: isLabActive ? "rgba(255, 255, 255, 0.08)" : "transparent",
              boxShadow: isLabActive ? "0 1px 2px rgba(0,0,0,0.4)" : "none",
              transition: "all 0.15s",
            }}
          >
            <FlaskConical size={13} /> Lab
          </Link>
        )}
      </nav>

      {/* Right: Engine Telemetry Badge */}
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 6,
            padding: "3px 9px",
            borderRadius: 9999,
            background: "rgba(16, 185, 129, 0.08)",
            border: "1px solid rgba(16, 185, 129, 0.22)",
            fontSize: 11,
            color: "#10b981",
          }}
          title={health ? `Worker: ${health.worker_model} • Router: ${health.router_model}` : "Orion Self-Healing Engine Active"}
        >
          <span style={{ width: 6, height: 6, borderRadius: "50%", background: "#10b981", boxShadow: "0 0 6px #10b981" }} />
          <span>{health?.router_model ? `${health.router_model}` : "Self-Healing Engine"}</span>
        </div>
      </div>
    </header>
  );
}
