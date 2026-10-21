"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useEffect, useState } from "react";
import { Sparkles, ArrowRight, AlertCircle, Lock, ShieldCheck } from "lucide-react";
import { getOidcConfig, loadSession, login, saveSession } from "@/lib/api";

export default function LoginPage() {
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [oidcConfig, setOidcConfig] = useState<{ enabled: boolean; issuer: string | null } | null>(null);

  useEffect(() => {
    if (loadSession()) {
      router.replace("/app");
      return;
    }
    getOidcConfig()
      .then(setOidcConfig)
      .catch(() => undefined);
  }, [router]);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setError("");
    setBusy(true);
    try {
      const session = await login(username.trim(), password);
      saveSession(session);
      router.replace("/app");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sign in failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: "24px",
        background: "radial-gradient(ellipse at 50% 20%, rgba(59, 130, 246, 0.08) 0%, var(--bg-canvas) 70%)",
      }}
    >
      <div
        className="card-elevated"
        style={{
          width: "100%",
          maxWidth: 420,
          padding: "36px 32px",
          border: "1px solid var(--border-default)",
          boxShadow: "0 20px 50px rgba(0, 0, 0, 0.6)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 20 }}>
          <div
            style={{
              width: 32,
              height: 32,
              borderRadius: 8,
              background: "#18181e",
              border: "1px solid rgba(255, 255, 255, 0.12)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              color: "#3b82f6",
            }}
          >
            <Sparkles size={18} strokeWidth={1.75} />
          </div>
          <div>
            <span style={{ fontSize: 16, fontWeight: 600, color: "var(--text-primary)" }}>Orion</span>
            <span className="brand-badge" style={{ marginLeft: 8 }}>Harness v5</span>
          </div>
        </div>

        <h1 style={{ fontSize: 22, marginTop: 0, marginBottom: 6 }}>Sign In</h1>
        <p style={{ fontSize: 13, color: "var(--text-secondary)", marginBottom: 24, lineHeight: 1.5 }}>
          Access the self-healing multi-agent control plane and live enterprise data lake.
        </p>

        {error && (
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              padding: "10px 12px",
              borderRadius: "var(--radius-sm)",
              background: "var(--status-danger-bg)",
              color: "var(--status-danger)",
              marginBottom: 18,
              fontSize: 12.5,
            }}
          >
            <AlertCircle size={15} />
            <span>{error}</span>
          </div>
        )}

        <form onSubmit={onSubmit} style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <div className="form-group" style={{ margin: 0 }}>
            <label className="form-label">Username</label>
            <input
              className="form-input"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
              placeholder="e.g. admin"
              required
            />
          </div>

          <div className="form-group" style={{ margin: 0 }}>
            <label className="form-label">Password</label>
            <input
              className="form-input"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              placeholder="••••••••"
              required
            />
          </div>

          <button
            type="submit"
            className="btn-primary"
            disabled={busy}
            style={{ marginTop: 8, width: "100%", height: 38, justifyContent: "center" }}
          >
            {busy ? "Authenticating..." : (
              <>
                Continue to Workspace <ArrowRight size={14} />
              </>
            )}
          </button>
        </form>

        {oidcConfig?.enabled && (
          <div style={{ marginTop: 18, paddingTop: 16, borderTop: "1px solid var(--border-subtle)" }}>
            <button
              type="button"
              className="btn-secondary"
              style={{ width: "100%", height: 38, justifyContent: "center" }}
            >
              <ShieldCheck size={14} /> Single Sign-On (OIDC)
            </button>
          </div>
        )}

        <div
          style={{
            marginTop: 24,
            paddingTop: 16,
            borderTop: "1px solid var(--border-subtle)",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            fontSize: 12.5,
            color: "var(--text-muted)",
          }}
        >
          <span>Need an account?</span>
          <Link href="/signup" style={{ color: "#60a5fa", fontWeight: 500 }}>
            Create Account
          </Link>
        </div>
      </div>
    </main>
  );
}
