"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useState } from "react";
import { Sparkles, ArrowRight, AlertCircle, Shield } from "lucide-react";
import { saveSession, signup } from "@/lib/api";

export default function SignupPage() {
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setError("");
    if (password !== confirmPassword) {
      setError("Passwords do not match");
      return;
    }
    setBusy(true);
    try {
      const session = await signup(username.trim(), password);
      saveSession(session);
      router.replace("/app");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not create account");
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

        <h1 style={{ fontSize: 22, marginTop: 0, marginBottom: 6 }}>Create Account</h1>
        <p style={{ fontSize: 13, color: "var(--text-secondary)", marginBottom: 24, lineHeight: 1.5 }}>
          Join the workspace with self-service limited role access.
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
              placeholder="e.g. analyst_john"
              required
              minLength={2}
            />
          </div>

          <div className="form-group" style={{ margin: 0 }}>
            <label className="form-label">Password</label>
            <input
              className="form-input"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="At least 8 characters"
              required
              minLength={8}
            />
          </div>

          <div className="form-group" style={{ margin: 0 }}>
            <label className="form-label">Confirm Password</label>
            <input
              className="form-input"
              type="password"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              placeholder="Repeat password"
              required
              minLength={8}
            />
          </div>

          <button
            type="submit"
            className="btn-primary"
            disabled={busy}
            style={{ marginTop: 8, width: "100%", height: 38, justifyContent: "center" }}
          >
            {busy ? "Registering..." : (
              <>
                Create Account <ArrowRight size={14} />
              </>
            )}
          </button>
        </form>

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
          <span>Already registered?</span>
          <Link href="/" style={{ color: "#60a5fa", fontWeight: 500 }}>
            Sign In
          </Link>
        </div>
      </div>
    </main>
  );
}
