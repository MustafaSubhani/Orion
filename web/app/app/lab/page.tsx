"use client";

import { FormEvent, KeyboardEvent, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Chrome } from "@/components/Chrome";
import { AuthError, me, sendLab } from "@/lib/api";
import type { Session } from "@/lib/types";

type Message = { role: "user" | "assistant"; content: string };

export default function LabPage() {
  const router = useRouter();
  const [session, setSession] = useState<Session | null>(null);
  const [messages, setMessages] = useState<Message[]>([
    {
      role: "assistant",
      content: "Lab synthesizes a new worker. It does not mix HR and finance into one agent. Type help, customize, or list.",
    },
  ]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [bootError, setBootError] = useState("");

  useEffect(() => {
    let cancelled = false;
    me()
      .then((current) => {
        if (cancelled) return;
        if (current.role !== "super_user") {
          router.replace("/app");
          return;
        }
        setSession(current);
      })
      .catch((err) => {
        if (cancelled) return;
        if (err instanceof AuthError) {
          router.replace("/");
          return;
        }
        setBootError(err instanceof Error ? err.message : "Could not reach the API");
      });
    return () => {
      cancelled = true;
    };
  }, [router]);

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      event.currentTarget.form?.requestSubmit();
    }
  }

  async function onSend(event: FormEvent) {
    event.preventDefault();
    const text = draft.trim();
    if (!text || busy) return;
    setDraft("");
    setBusy(true);
    setError("");
    setMessages((prev) => [...prev, { role: "user", content: text }]);
    try {
      const reply = await sendLab(text);
      setMessages((prev) => [...prev, { role: "assistant", content: reply }]);
    } catch (err) {
      if (err instanceof AuthError) {
        router.replace("/");
        return;
      }
      setError(err instanceof Error ? err.message : "Lab request failed");
    } finally {
      setBusy(false);
    }
  }

  if (bootError) {
    return (
      <main className="auth">
        <section className="auth-card">
          <p className="kicker">Orion</p>
          <h1>API unavailable</h1>
          <p className="lead">{bootError}</p>
        </section>
      </main>
    );
  }

  if (!session) {
    return (
      <main className="auth">
        <p className="muted">Loading</p>
      </main>
    );
  }

  return (
    <div className="shell">
      <Chrome session={session} page="lab" note="Lab. New agent cards only." />
      <section className="main">
        <div className="thread">
          {messages.map((msg, i) => (
            <article key={i} className={msg.role === "user" ? "msg" : "msg bot"}>
              <div className="role">{msg.role === "user" ? "You" : "Lab"}</div>
              <pre className="copy">{msg.content}</pre>
            </article>
          ))}
        </div>
        <form onSubmit={onSend} className="composer">
          {error ? <p className="err">{error}</p> : null}
          <div className="row" style={{ marginTop: 0, marginBottom: 10 }}>
            {["help", "customize", "list"].map((cmd) => (
              <button key={cmd} type="button" className="btn-chip" onClick={() => setDraft(cmd)} disabled={busy}>
                {cmd}
              </button>
            ))}
          </div>
          <textarea className="area" value={draft} onChange={(e) => setDraft(e.target.value)} onKeyDown={onKeyDown} rows={3} disabled={busy} />
          <button type="submit" className="send" disabled={busy} style={{ marginTop: 10, marginLeft: 0 }}>
            {busy ? "Sending" : "Send"}
          </button>
        </form>
      </section>
    </div>
  );
}
