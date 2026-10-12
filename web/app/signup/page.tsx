"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useState } from "react";
import { saveSession, signup } from "@/lib/api";

export default function SignupPage() {
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setError("");
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
    <main className="auth">
      <section className="auth-card">
        <p className="kicker">Orion</p>
        <h1>Create account</h1>
        <p className="lead">New users are limited. The admin account is the one created from ORION_ADMIN_PASSWORD.</p>
        <form onSubmit={onSubmit} className="stack">
          <label className="label">
            Username
            <input className="field" value={username} onChange={(e) => setUsername(e.target.value)} required minLength={2} />
          </label>
          <label className="label">
            Password
            <input
              className="field"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              minLength={4}
            />
          </label>
          {error ? <p className="err">{error}</p> : null}
          <button type="submit" className="btn" disabled={busy}>
            {busy ? "Creating" : "Create account"}
          </button>
        </form>
        <p className="foot">
          Already have an account? <Link href="/">Sign in</Link>
        </p>
      </section>
    </main>
  );
}
