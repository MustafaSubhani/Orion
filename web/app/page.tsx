"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useEffect, useState } from "react";
import { loadSession, login, saveSession } from "@/lib/api";

export default function LoginPage() {
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (loadSession()) router.replace("/app");
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
    <main className="auth">
      <section className="auth-card">
        <p className="kicker">Orion</p>
        <h1>Sign in</h1>
        <p className="lead">Same control plane as the API. Chat uses the live KB. Bench stays off this path.</p>
        <form onSubmit={onSubmit} className="stack">
          <label className="label">
            Username
            <input
              className="field"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
              required
            />
          </label>
          <label className="label">
            Password
            <input
              className="field"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              required
            />
          </label>
          {error ? <p className="err">{error}</p> : null}
          <button type="submit" className="btn" disabled={busy}>
            {busy ? "Signing in" : "Continue"}
          </button>
        </form>
        <p className="foot">
          New account? <Link href="/signup">Create one</Link>
        </p>
      </section>
    </main>
  );
}
