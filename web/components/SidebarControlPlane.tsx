"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import {
  Cpu,
  Database,
  Activity,
  CheckCircle2,
  ShieldAlert,
  Server,
  Settings,
  SlidersHorizontal,
  ChevronRight,
  LogOut,
  Sparkles,
} from "lucide-react";
import type { Session } from "@/lib/types";
import { logout } from "@/lib/api";

type Props = {
  session: Session;
};

export function SidebarControlPlane({ session }: Props) {
  const router = useRouter();
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  async function onSignOut() {
    await logout();
    router.replace("/");
  }

  const controlPlaneLinks = [
    { href: "/app/agents", label: "Agents Studio", desc: "Manage domain specialists & lifecycles", icon: Cpu },
    { href: "/app/kb", label: "Knowledge Lake", desc: "Documents, SQLite tables & ingest", icon: Database },
    { href: "/app/traces", label: "Observability & Traces", desc: "Execution DAGs & flight recorder", icon: Activity },
    ...(session.role === "super_user"
      ? [
          { href: "/app/approvals", label: "Tool Approvals", desc: "Human-in-the-loop governance", icon: CheckCircle2 },
          { href: "/app/quality", label: "Quality & Self-Repair", desc: "Judge review & prompt repair", icon: ShieldAlert },
          { href: "/app/connectors", label: "MCP & Connectors", desc: "Model Context Protocol bridge", icon: Server },
          { href: "/app/settings", label: "Master Settings", desc: "Model keys, tokens, user roles", icon: Settings },
        ]
      : [
          { href: "/app/approvals", label: "My Approvals", desc: "Authorizations for your queries", icon: CheckCircle2 },
        ]),
  ];

  const isAnyControlActive = controlPlaneLinks.some((l) => pathname.startsWith(l.href));

  return (
    <div
      ref={containerRef}
      style={{
        position: "relative",
        padding: "10px 12px",
        borderTop: "1px solid var(--border-subtle)",
        background: "var(--bg-surface)",
        display: "flex",
        flexDirection: "column",
        gap: "8px",
      }}
    >
      {/* Upward-opening Popover for Control Plane Menu */}
      {open && (
        <div
          style={{
            position: "absolute",
            bottom: "calc(100% + 6px)",
            left: 8,
            width: 290,
            background: "var(--bg-elevated)",
            border: "1px solid var(--border-default)",
            borderRadius: "10px",
            padding: "8px",
            boxShadow: "0 16px 40px rgba(0, 0, 0, 0.7)",
            zIndex: 100,
            animation: "slideUp 0.16s cubic-bezier(0.16, 1, 0.3, 1)",
          }}
        >
          <div
            style={{
              padding: "6px 8px 8px 8px",
              fontSize: "11px",
              fontWeight: 650,
              color: "var(--text-muted)",
              textTransform: "uppercase",
              letterSpacing: "0.06em",
              borderBottom: "1px solid var(--border-subtle)",
              marginBottom: 4,
            }}
          >
            Harness Control Plane
          </div>

          <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
            {controlPlaneLinks.map((link) => {
              const Icon = link.icon;
              const active = pathname.startsWith(link.href);
              return (
                <Link
                  key={link.href}
                  href={link.href}
                  onClick={() => setOpen(false)}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: "10px",
                    padding: "8px 10px",
                    borderRadius: "6px",
                    background: active ? "rgba(255, 255, 255, 0.08)" : "transparent",
                    color: active ? "var(--text-primary)" : "var(--text-secondary)",
                    transition: "background 0.12s",
                  }}
                >
                  <Icon size={15} style={{ color: active ? "#60a5fa" : "var(--text-muted)", flexShrink: 0 }} />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: "12.5px", fontWeight: 550, color: active ? "var(--text-primary)" : "var(--text-secondary)" }}>
                      {link.label}
                    </div>
                    <div style={{ fontSize: "11px", color: "var(--text-muted)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {link.desc}
                    </div>
                  </div>
                </Link>
              );
            })}
          </div>
        </div>
      )}

      {/* Control Plane Button (Shifted to Left Bottom) */}
      <button
        type="button"
        onClick={() => setOpen(!open)}
        style={{
          width: "100%",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "7px 10px",
          borderRadius: "6px",
          fontSize: "12.5px",
          fontWeight: 500,
          background: isAnyControlActive || open ? "var(--bg-elevated)" : "transparent",
          color: isAnyControlActive || open ? "#60a5fa" : "var(--text-secondary)",
          border: isAnyControlActive || open ? "1px solid var(--border-default)" : "1px solid transparent",
          cursor: "pointer",
          transition: "all 0.15s",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          <SlidersHorizontal size={14} />
          <span>Control Plane</span>
        </div>
        <ChevronRight size={13} style={{ transform: open ? "rotate(-90deg)" : "none", transition: "transform 0.15s" }} />
      </button>

      {/* User Profile Pill */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "6px 10px",
          borderRadius: "8px",
          background: "var(--bg-canvas)",
          border: "1px solid var(--border-subtle)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "8px", minWidth: 0 }}>
          <div
            style={{
              width: 22,
              height: 22,
              borderRadius: "50%",
              background: session.role === "super_user" ? "#2563eb" : "#475569",
              color: "white",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              fontSize: "11px",
              fontWeight: 650,
              flexShrink: 0,
            }}
          >
            {session.username.slice(0, 1).toUpperCase()}
          </div>
          <div style={{ overflow: "hidden" }}>
            <div style={{ fontSize: "12.5px", fontWeight: 550, color: "var(--text-primary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {session.username}
            </div>
          </div>
          <span
            style={{
              fontSize: "9.5px",
              fontWeight: 600,
              padding: "1px 5px",
              borderRadius: "4px",
              background: session.role === "super_user" ? "rgba(59, 130, 246, 0.15)" : "rgba(255, 255, 255, 0.08)",
              color: session.role === "super_user" ? "#60a5fa" : "var(--text-muted)",
              letterSpacing: "0.04em",
              textTransform: "uppercase",
            }}
          >
            {session.role === "super_user" ? "Admin" : "Limited"}
          </span>
        </div>

        <button
          type="button"
          onClick={onSignOut}
          title="Sign out of harness session"
          style={{
            padding: "4px 6px",
            borderRadius: "4px",
            color: "var(--text-muted)",
            background: "transparent",
            border: "none",
            cursor: "pointer",
          }}
        >
          <LogOut size={13} />
        </button>
      </div>

      <style jsx>{`
        @keyframes slideUp {
          from { opacity: 0; transform: translateY(6px); }
          to { opacity: 1; transform: translateY(0); }
        }
      `}</style>
    </div>
  );
}
