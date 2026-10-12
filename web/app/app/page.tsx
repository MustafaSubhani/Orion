"use client";

import { FormEvent, KeyboardEvent, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Chrome } from "@/components/Chrome";
import { AuthError, ingestFiles, listAgents, me, streamChat } from "@/lib/api";
import type { Agent, Session } from "@/lib/types";

type Message = { role: "user" | "assistant"; content: string; meta?: string };

export default function ChatPage() {
  const router = useRouter();
  const [session, setSession] = useState<Session | null>(null);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [messages, setMessages] = useState<Message[]>([]);
  const [draft, setDraft] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [bootError, setBootError] = useState("");
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    me()
      .then((current) => {
        if (cancelled) return;
        setSession(current);
        return listAgents()
          .then((listed) => {
            if (!cancelled) setAgents(listed);
          })
          .catch((err) => {
            if (!cancelled) setError(err instanceof Error ? err.message : "Could not load agents");
          });
      })
      .catch((err) => {
        if (cancelled) return;
        if (err instanceof AuthError) {
          router.replace("/");
          return;
        }
        setBootError(err instanceof Error ? err.message : "Could not load workspace");
      });
    return () => {
      cancelled = true;
    };
  }, [router]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [messages, status]);

  function toggle(id: string) {
    setSelected((prev) => (prev.includes(id) ? prev.filter((item) => item !== id) : [...prev, id]));
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      event.currentTarget.form?.requestSubmit();
    }
  }

  async function onSend(event: FormEvent) {
    event.preventDefault();
    if (!session || busy) return;
    const text = draft.trim();
    if (!text && files.length === 0) return;
    setBusy(true);
    setError("");
    setDraft("");
    let outgoing = text;
    try {
      if (files.length) {
        const note = await ingestFiles(files, session.username);
        outgoing = outgoing ? `${note}\n\n${outgoing}` : `${note}\n\nSummarize the attached files using kb_search or kb_query.`;
        setFiles([]);
      }
      setMessages((prev) => [...prev, { role: "user", content: outgoing }]);
      setStatus("Working");
      let reply = "";
      let meta = "";
      for await (const ev of streamChat(outgoing, selected, session.username)) {
        setStatus(ev.status);
        if (ev.response) reply = ev.response;
        const bits = [ev.agents.join(", "), ev.requestId].filter(Boolean);
        if (bits.length) meta = bits.join("  ");
      }
      setMessages((prev) => [...prev, { role: "assistant", content: reply || "No answer returned.", meta }]);
    } catch (err) {
      if (err instanceof AuthError) {
        router.replace("/");
        return;
      }
      setError(err instanceof Error ? err.message : "Request failed");
    } finally {
      setStatus("");
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
          <p className="muted">Start uvicorn on port 8000, then reload.</p>
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
      <Chrome session={session} page="chat" note="Live KB chat. Frozen eval lake is not written from here." />
      <div className="body">
        <aside className="aside">
          <h2>Agents</h2>
          <p className="muted">Leave empty to let the router choose.</p>
          {agents.map((agent) => (
            <label key={agent.id} className="check">
              <input type="checkbox" checked={selected.includes(agent.id)} onChange={() => toggle(agent.id)} />
              <span>
                <strong>{agent.name}</strong>
                <span className="muted">{agent.description}</span>
              </span>
            </label>
          ))}
        </aside>
        <section className="main">
          <div className="thread">
            {messages.length === 0 ? (
              <p className="empty">Ask a payroll, filings, calendar, or uploaded KB question.</p>
            ) : (
              messages.map((msg, i) => (
                <article key={i} className={msg.role === "user" ? "msg" : "msg bot"}>
                  <div className="role">{msg.role === "user" ? "You" : "Orion"}</div>
                  <pre className="copy">{msg.content}</pre>
                  {msg.meta ? <div className="muted">{msg.meta}</div> : null}
                </article>
              ))
            )}
            {status ? <p className="muted">{status}</p> : null}
            <div ref={endRef} />
          </div>
          <form onSubmit={onSend} className="composer">
            {error ? <p className="err">{error}</p> : null}
            <textarea
              className="area"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={onKeyDown}
              placeholder="Message"
              rows={3}
              disabled={busy}
            />
            <div className="row">
              <label className="file">
                Attach
                <input
                  type="file"
                  multiple
                  hidden
                  onChange={(e) => setFiles(Array.from(e.target.files || []))}
                />
              </label>
              <span className="muted">
                {files.length ? files.map((file) => file.name).join(", ") : "Optional live KB upload"}
              </span>
              {files.length ? (
                <button type="button" className="btn-ghost" onClick={() => setFiles([])}>
                  Clear
                </button>
              ) : null}
              <button type="submit" className="send" disabled={busy}>
                {busy ? "Sending" : "Send"}
              </button>
            </div>
          </form>
        </section>
      </div>
    </div>
  );
}
