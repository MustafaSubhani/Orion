"use client";

import { FormEvent, KeyboardEvent, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  Send,
  Paperclip,
  Check,
  Copy,
  Plus,
  Trash2,
  Cpu,
  Bot,
  User,
  Search,
  Download,
  AlertTriangle,
  RotateCcw,
  Sparkles,
  Globe,
  Sliders,
  ChevronDown,
  ThumbsUp,
  ThumbsDown,
  RefreshCw,
  X,
  Zap,
} from "lucide-react";
import { Chrome } from "@/components/Chrome";
import { SidebarControlPlane } from "@/components/SidebarControlPlane";
import { OpenUIResponse } from "@/components/OpenUIResponse";
import { WorkingSteps, type WorkingStep } from "@/components/WorkingSteps";
import {
  AuthError,
  createThread,
  deleteThread,
  getThreadMessages,
  ingestFiles,
  listAgents,
  listThreads,
  me,
  streamChat,
} from "@/lib/api";
import type { Agent, ChatTask, Session, ThreadMessage, ThreadSummary } from "@/lib/types";

type DisplayMessage = {
  id?: number | string;
  role: "user" | "assistant";
  content: string;
  meta?: string;
  agentsUsed?: string[];
  requestId?: string;
  workingSteps?: WorkingStep[];
  elapsedSeconds?: number;
  feedback?: "up" | "down" | null;
};

// Agent visual colors for pill tags
const AGENT_TAG_STYLES: Record<string, { bg: string; text: string; border: string }> = {
  finance: { bg: "rgba(16, 185, 129, 0.12)", text: "#10b981", border: "rgba(16, 185, 129, 0.25)" },
  hr: { bg: "rgba(168, 85, 247, 0.12)", text: "#c084fc", border: "rgba(168, 85, 247, 0.25)" },
  scheduler: { bg: "rgba(245, 158, 11, 0.12)", text: "#fbbf24", border: "rgba(245, 158, 11, 0.25)" },
  chart: { bg: "rgba(59, 130, 246, 0.12)", text: "#60a5fa", border: "rgba(59, 130, 246, 0.25)" },
  sql: { bg: "rgba(239, 68, 68, 0.12)", text: "#f87171", border: "rgba(239, 68, 68, 0.25)" },
  orchestrator: { bg: "rgba(99, 102, 241, 0.12)", text: "#a5b4fc", border: "rgba(99, 102, 241, 0.25)" },
};

export default function ChatPage() {
  const router = useRouter();
  const [session, setSession] = useState<Session | null>(null);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [selectedAgents, setSelectedAgents] = useState<string[]>([]);
  const [pinnedAgent, setPinnedAgent] = useState<string>("");

  // Chat controls: Model, Effort, Web Search
  const [selectedModel, setSelectedModel] = useState("Orion 5.0 Auto");
  const [effortLevel, setEffortLevel] = useState<"high" | "medium" | "low">("high");
  const [webSearchEnabled, setWebSearchEnabled] = useState(false);
  const [agentMenuOpen, setAgentMenuOpen] = useState(false);
  const [modelMenuOpen, setModelMenuOpen] = useState(false);
  const [effortMenuOpen, setEffortMenuOpen] = useState(false);

  // Threads
  const [threads, setThreads] = useState<ThreadSummary[]>([]);
  const [activeThreadId, setActiveThreadId] = useState<string | null>(null);
  const [threadSearch, setThreadSearch] = useState("");

  // Messages & Draft
  const [messages, setMessages] = useState<DisplayMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [copiedIndex, setCopiedIndex] = useState<number | null>(null);

  // Live streaming states for Claude-style working steps
  const [streamingSteps, setStreamingSteps] = useState<WorkingStep[]>([]);
  const [streamingElapsed, setStreamingElapsed] = useState(0);

  const endRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const timerRef = useRef<NodeJS.Timeout | null>(null);
  const agentMenuRef = useRef<HTMLDivElement>(null);
  const modelMenuRef = useRef<HTMLDivElement>(null);
  const effortMenuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    me()
      .then((current) => {
        if (cancelled) return;
        setSession(current);

        Promise.all([
          listAgents().catch(() => [] as Agent[]),
          listThreads().catch(() => [] as ThreadSummary[]),
        ]).then(([listedAgents, listedThreads]) => {
          if (cancelled) return;
          setAgents(listedAgents);
          setThreads(listedThreads);
          if (listedThreads.length > 0) {
            selectThread(listedThreads[0].id);
          }
        });
      })
      .catch((err) => {
        if (cancelled) return;
        if (err instanceof AuthError) router.replace("/");
        else setError(err instanceof Error ? err.message : "Could not initialize session");
      });

    return () => {
      cancelled = true;
    };
  }, [router]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, streamingSteps]);

  // Click outside listener for composer popovers
  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (agentMenuRef.current && !agentMenuRef.current.contains(event.target as Node)) {
        setAgentMenuOpen(false);
      }
      if (modelMenuRef.current && !modelMenuRef.current.contains(event.target as Node)) {
        setModelMenuOpen(false);
      }
      if (effortMenuRef.current && !effortMenuRef.current.contains(event.target as Node)) {
        setEffortMenuOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  async function selectThread(threadId: string) {
    setActiveThreadId(threadId);
    setError("");
    try {
      const data = await getThreadMessages(threadId);
      const formatted: DisplayMessage[] = (data.messages || []).map((m: ThreadMessage) => ({
        id: m.id,
        role: m.role === "assistant" ? "assistant" : "user",
        content: m.content,
      }));
      setMessages(formatted);
    } catch {
      setMessages([]);
    }
  }

  async function onNewThread() {
    try {
      const created = await createThread(
        `Chat ${new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`
      );
      setThreads((prev) => [created, ...prev]);
      setActiveThreadId(created.id);
      setMessages([]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not create thread");
    }
  }

  async function onDeleteThread(e: React.MouseEvent, threadId: string) {
    e.stopPropagation();
    try {
      await deleteThread(threadId);
      const remaining = threads.filter((t) => t.id !== threadId);
      setThreads(remaining);
      if (activeThreadId === threadId) {
        if (remaining.length > 0) {
          selectThread(remaining[0].id);
        } else {
          setActiveThreadId(null);
          setMessages([]);
        }
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not delete thread");
    }
  }

  function toggleAgent(id: string) {
    setSelectedAgents((prev) =>
      prev.includes(id) ? prev.filter((item) => item !== id) : [...prev, id]
    );
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
        outgoing = outgoing
          ? `${note}\n\n${outgoing}`
          : `${note}\n\nPlease analyze and summarize the attached files.`;
        setFiles([]);
      }

      const userMsg: DisplayMessage = { role: "user", content: outgoing };
      setMessages((prev) => [...prev, userMsg]);

      let accumulatedSteps: WorkingStep[] = [
        {
          id: "step-init",
          type: "init",
          title: "Analyzing query & checking contracts",
          status: "running",
          description: `Evaluating against specialist boundaries (${webSearchEnabled ? "Web Search enabled" : "Lake only"})`,
        },
      ];
      setStreamingSteps(accumulatedSteps);

      const startTime = Date.now();
      timerRef.current = setInterval(() => {
        setStreamingElapsed(Number(((Date.now() - startTime) / 1000).toFixed(1)));
      }, 100);

      let reply = "";
      let requestId = "";
      let agentsUsedList: string[] = [];
      const discoveredGraphs: string[] = [];

      for await (const ev of streamChat(
        outgoing,
        selectedAgents.length > 0 ? selectedAgents : undefined,
        pinnedAgent || undefined,
        activeThreadId || undefined
      )) {
        if (ev.requestId) requestId = ev.requestId;
        if (ev.agents && ev.agents.length > 0) agentsUsedList = ev.agents;
        if (ev.response) reply = ev.response;

        const raw = ev.raw || {};
        const type = String(ev.type || "");

        // Detect any chart graph PNGs emitted in specialist task outputs or events
        const rawStr = JSON.stringify(raw) + " " + (ev.response || "");
        const graphMatches = rawStr.match(/\/graphs\/graph_[a-zA-Z0-9_]+\.png/g) || [];
        for (const g of graphMatches) {
          if (!discoveredGraphs.includes(g)) discoveredGraphs.push(g);
        }

        if (type === "tasks_identified") {
          const rawTasks = Array.isArray(raw.tasks) ? raw.tasks : [];
          const taskSteps: WorkingStep[] = rawTasks.map((t: Record<string, unknown>, idx: number) => ({
            id: `task-${idx}`,
            type: "task",
            title: `Task #${idx + 1}: ${String(t.description || "Execute subtask")}`,
            agent: String(t.agent || "router"),
            status: "pending",
            description: `Assigned to ${String(t.agent || "specialist").toUpperCase()} specialist`,
          }));

          accumulatedSteps = [
            { ...accumulatedSteps[0], status: "completed" },
            ...taskSteps,
          ];
          setStreamingSteps(accumulatedSteps);
        } else if (type === "task_start") {
          const agentName = String(raw.agent || "");
          const desc = String(raw.description || "");
          const copy = [...accumulatedSteps];
          const existingIdx = copy.findIndex((s) => s.agent === agentName && s.status === "pending");
          if (existingIdx !== -1) {
            copy[existingIdx] = {
              ...copy[existingIdx],
              status: "running",
              description: desc || copy[existingIdx].description,
            };
          } else {
            copy.push({
              id: `step-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
              type: "task_start",
              title: `Running ${agentName.toUpperCase()} Agent`,
              agent: agentName,
              status: "running",
              description: desc,
            });
          }
          accumulatedSteps = copy;
          setStreamingSteps(accumulatedSteps);
        } else if (type === "task_complete") {
          const agentName = String(raw.agent || "");
          accumulatedSteps = accumulatedSteps.map((s) => {
            if (s.agent === agentName && s.status === "running") {
              return { ...s, status: "completed" as const };
            }
            return s;
          });
          setStreamingSteps(accumulatedSteps);
        } else if (type === "backtrack") {
          const reason = String(raw.reason || ev.status || "Contract failure resolved");
          accumulatedSteps = [
            ...accumulatedSteps,
            {
              id: `backtrack-${Date.now()}`,
              type: "backtrack",
              title: "Self-Healing Rewind Sequence",
              status: "healed",
              description: reason,
              agent: String(raw.agent || "harness"),
            },
          ];
          setStreamingSteps(accumulatedSteps);
        } else if (type === "synthesizing") {
          accumulatedSteps = [
            ...accumulatedSteps.map((s) => (s.status === "running" ? { ...s, status: "completed" as const } : s)),
            {
              id: "step-synth",
              type: "synthesizing",
              title: "Synthesizing Final Structured Response",
              status: "completed",
              description: "Combining specialist outputs into unified answer",
            },
          ];
          setStreamingSteps(accumulatedSteps);
        }
      }

      if (timerRef.current) clearInterval(timerRef.current);
      const totalElapsed = Number(((Date.now() - startTime) / 1000).toFixed(1));

      const finalizedSteps = accumulatedSteps.map((s) => ({
        ...s,
        status: s.status === "running" ? ("completed" as const) : s.status,
      }));

      // Clear the live streaming indicator
      setStreamingSteps([]);

      // Auto-Heal Chart URLs if synthesizer dropped or substituted placeholders
      let cleanContent = reply || "No answer returned.";
      if (discoveredGraphs.length > 0) {
        const primaryGraph = discoveredGraphs[0];
        if (/https?:\/\/(?:via\.placeholder\.com|placeholder\.com)[^\)\s]+/i.test(cleanContent)) {
          cleanContent = cleanContent.replace(/https?:\/\/(?:via\.placeholder\.com|placeholder\.com)[^\)\s]+/gi, primaryGraph);
        }
        cleanContent = cleanContent.replace(/\s*\*\(Note: Replace placeholder with the actual image link[^\)]*\)\*/gi, "");
        if (!cleanContent.includes(primaryGraph)) {
          cleanContent = `${cleanContent}\n\n![Generated Visualization Chart](${primaryGraph})\n`;
        }
      }

      // Append exactly ONE assistant message with deduplication protection
      const finalAssistantMsg: DisplayMessage = {
        id: requestId || `asst-${Date.now()}`,
        role: "assistant",
        content: cleanContent,
        agentsUsed: agentsUsedList,
        requestId,
        workingSteps: finalizedSteps,
        elapsedSeconds: totalElapsed,
        feedback: null,
      };

      setMessages((prev) => {
        if (requestId && prev.some((m) => m.requestId === requestId && m.role === "assistant")) {
          return prev;
        }
        return [...prev, finalAssistantMsg];
      });
    } catch (err) {
      if (err instanceof AuthError) {
        router.replace("/");
        return;
      }
      setError(err instanceof Error ? err.message : "Request failed");
      setStreamingSteps([]);
    } finally {
      if (timerRef.current) clearInterval(timerRef.current);
      setBusy(false);
    }
  }

  function onFeedback(index: number, type: "up" | "down") {
    setMessages((prev) =>
      prev.map((m, i) => (i === index ? { ...m, feedback: m.feedback === type ? null : type } : m))
    );
  }

  function copyToClipboard(text: string, index: number) {
    navigator.clipboard.writeText(text);
    setCopiedIndex(index);
    setTimeout(() => setCopiedIndex(null), 2000);
  }

  function exportAsMarkdown(content: string) {
    const blob = new Blob([content], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `orion-response-${Date.now()}.md`;
    link.click();
    URL.revokeObjectURL(url);
  }

  async function onRetry() {
    const lastUserMessage = [...messages].reverse().find((m) => m.role === "user");
    if (!lastUserMessage) return;
    setDraft(lastUserMessage.content);
  }

  const filteredThreads = threads.filter((t) =>
    (t.title || "").toLowerCase().includes(threadSearch.toLowerCase())
  );

  if (!session) {
    return (
      <main className="modal-backdrop">
        <div style={{ color: "var(--text-muted)" }}>Loading workspace...</div>
      </main>
    );
  }

  return (
    <div className="app-shell">
      {/* Top Bar: Minimal, with ONLY Chat & Lab (super_user only) */}
      <Chrome session={session} />

      <div className="app-container">
        {/* Left Sidebar: Threads and Bottom-Left Control Plane */}
        <aside className="app-sidebar" style={{ display: "flex", flexDirection: "column", height: "calc(100vh - 48px)" }}>
          <div className="sidebar-header" style={{ padding: "14px 14px 10px 14px" }}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 12 }}>
              <span style={{ fontSize: 13, fontWeight: 650, color: "var(--text-primary)", letterSpacing: "-0.01em" }}>
                Conversations
              </span>
              <button
                type="button"
                className="btn-primary"
                onClick={onNewThread}
                style={{ padding: "4px 8px", fontSize: 12 }}
                title="Create new conversation thread"
              >
                <Plus size={14} /> New
              </button>
            </div>

            <div style={{ position: "relative" }}>
              <Search
                size={14}
                style={{ position: "absolute", left: 10, top: 10, color: "var(--text-muted)" }}
              />
              <input
                type="text"
                className="form-input"
                style={{ paddingLeft: 30, fontSize: 12.5 }}
                placeholder="Search threads..."
                value={threadSearch}
                onChange={(e) => setThreadSearch(e.target.value)}
              />
            </div>
          </div>

          {/* Threads List */}
          <div className="sidebar-content" style={{ flex: 1, overflowY: "auto", padding: "8px 10px" }}>
            {filteredThreads.length === 0 ? (
              <p style={{ fontSize: 12, color: "var(--text-muted)", padding: "16px 8px" }}>
                No conversations yet. Start a new chat!
              </p>
            ) : (
              filteredThreads.map((t) => {
                const isActive = t.id === activeThreadId;
                return (
                  <div
                    key={t.id}
                    onClick={() => selectThread(t.id)}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "space-between",
                      padding: "8px 10px",
                      borderRadius: "6px",
                      marginBottom: 3,
                      cursor: "pointer",
                      background: isActive ? "var(--bg-elevated)" : "transparent",
                      border: isActive ? "1px solid var(--border-subtle)" : "1px solid transparent",
                      color: isActive ? "var(--text-primary)" : "var(--text-secondary)",
                    }}
                  >
                    <span
                      style={{
                        fontSize: 13,
                        fontWeight: isActive ? 550 : 400,
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                        marginRight: 6,
                      }}
                    >
                      {t.title || "Untitled Conversation"}
                    </span>
                    <button
                      type="button"
                      className="btn-ghost"
                      style={{ padding: 2, opacity: isActive ? 1 : 0.6 }}
                      onClick={(e) => onDeleteThread(e, t.id)}
                      title="Delete thread"
                    >
                      <Trash2 size={13} />
                    </button>
                  </div>
                );
              })
            )}
          </div>

          {/* Bottom Left: Shifted Control Plane & User Profile! */}
          <SidebarControlPlane session={session} />
        </aside>

        {/* Main Conversation Stream */}
        <main className="app-main" style={{ display: "flex", flexDirection: "column", height: "calc(100vh - 48px)" }}>
          <div className="chat-thread" style={{ flex: 1, overflowY: "auto", padding: "24px 32px" }}>
            {messages.length === 0 ? (
              <div
                style={{
                  margin: "auto",
                  maxWidth: 540,
                  textAlign: "center",
                  padding: "60px 20px",
                }}
              >
                <div
                  style={{
                    width: 44,
                    height: 44,
                    borderRadius: 12,
                    background: "var(--bg-elevated)",
                    border: "1px solid var(--border-default)",
                    margin: "0 auto 16px",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    color: "var(--accent-primary)",
                  }}
                >
                  <Sparkles size={22} strokeWidth={1.75} />
                </div>
                <h2 style={{ fontSize: 20, marginBottom: 8, letterSpacing: "-0.015em", color: "var(--text-primary)" }}>
                  Orion Enterprise Multi-Agent Lake
                </h2>
                <p style={{ fontSize: 13.5, color: "var(--text-secondary)", lineHeight: 1.6 }}>
                  Query SEC financial filings, executive payroll, engineering calendars, and live KB data. The typed orchestrator coordinates specialists with contract guardrails.
                </p>

                <div style={{ display: "flex", flexWrap: "wrap", gap: 8, justifyContent: "center", marginTop: 24 }}>
                  {[
                    "Generate a bar chart of average salary by department",
                    "Summarize Q4 executive compensation filings",
                    "Which engineers have scheduled PTO next month?",
                    "Plot Apple quarterly NetSales across fiscal years",
                  ].map((prompt) => (
                    <button
                      key={prompt}
                      type="button"
                      className="btn-secondary"
                      style={{ fontSize: 12.5, padding: "6px 12px", textAlign: "left" }}
                      onClick={() => setDraft(prompt)}
                    >
                      {prompt}
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              messages.map((msg, index) => {
                const isUser = msg.role === "user";
                return (
                  <div
                    key={msg.id || `${msg.role}-${index}`}
                    style={{
                      display: "flex",
                      gap: 16,
                      marginBottom: 28,
                      alignItems: "flex-start",
                      flexDirection: isUser ? "row-reverse" : "row",
                    }}
                  >
                    {/* Avatar */}
                    <div
                      style={{
                        width: 30,
                        height: 30,
                        borderRadius: 8,
                        background: isUser ? "var(--bg-elevated)" : "#181824",
                        border: isUser ? "1px solid var(--border-subtle)" : "1px solid rgba(59, 130, 246, 0.4)",
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                        color: isUser ? "var(--text-primary)" : "#3b82f6",
                        flexShrink: 0,
                      }}
                    >
                      {isUser ? <User size={15} /> : <Sparkles size={15} />}
                    </div>

                    {/* Message Body (Claude Style: No harsh outer box borders around assistant!) */}
                    <div style={{ flex: 1, minWidth: 0, maxWidth: isUser ? 680 : "100%" }}>
                      {/* User message is a crisp elevated pill */}
                      {isUser ? (
                        <div
                          style={{
                            background: "var(--bg-elevated)",
                            border: "1px solid var(--border-default)",
                            borderRadius: "14px",
                            padding: "12px 18px",
                            color: "var(--text-primary)",
                            fontSize: "14.5px",
                            lineHeight: 1.55,
                            whiteSpace: "pre-wrap",
                          }}
                        >
                          {msg.content}
                        </div>
                      ) : (
                        <div style={{ display: "flex", flexDirection: "column" }}>
                          {/* Assistant Meta Header: Agent chips & Request ID */}
                          <div
                            style={{
                              display: "flex",
                              alignItems: "center",
                              gap: 8,
                              marginBottom: 8,
                              flexWrap: "wrap",
                            }}
                          >
                            <span style={{ fontWeight: 650, fontSize: "13px", color: "var(--text-primary)" }}>
                              Orion
                            </span>

                            {msg.agentsUsed && msg.agentsUsed.length > 0 && (
                              <div style={{ display: "flex", alignItems: "center", gap: 5 }}>
                                {msg.agentsUsed.map((agentKey) => {
                                  const tag = AGENT_TAG_STYLES[agentKey.toLowerCase()];
                                  return (
                                    <span
                                      key={agentKey}
                                      style={{
                                        fontSize: 10.5,
                                        fontWeight: 600,
                                        padding: "1px 7px",
                                        borderRadius: 4,
                                        textTransform: "uppercase",
                                        letterSpacing: "0.04em",
                                        background: tag?.bg || "rgba(255, 255, 255, 0.08)",
                                        color: tag?.text || "var(--text-secondary)",
                                        border: `1px solid ${tag?.border || "transparent"}`,
                                      }}
                                    >
                                      {agentKey}
                                    </span>
                                  );
                                })}
                              </div>
                            )}

                            {msg.requestId && (
                              <span className="tabular-nums" style={{ color: "var(--text-muted)", fontSize: 11, fontFamily: "monospace" }}>
                                • {msg.requestId}
                              </span>
                            )}
                          </div>

                          {/* Claude-style collapsible working steps / thinking accordion */}
                          {msg.workingSteps && msg.workingSteps.length > 0 && (
                            <WorkingSteps
                              steps={msg.workingSteps}
                              agentsUsed={msg.agentsUsed}
                              requestId={msg.requestId}
                              elapsedSeconds={msg.elapsedSeconds}
                            />
                          )}

                          {/* OpenUI Rich GFM Markdown Content */}
                          <OpenUIResponse content={msg.content} />

                          {/* Claude-style response generation feedback bar */}
                          <div
                            style={{
                              display: "flex",
                              alignItems: "center",
                              gap: 6,
                              marginTop: 12,
                              paddingTop: 8,
                              color: "var(--text-muted)",
                            }}
                          >
                            <button
                              type="button"
                              className="btn-ghost"
                              style={{ fontSize: 12, padding: "4px 8px" }}
                              onClick={() => copyToClipboard(msg.content, index)}
                              title="Copy response"
                            >
                              {copiedIndex === index ? (
                                <>
                                  <Check size={13} color="#10b981" /> Copied
                                </>
                              ) : (
                                <>
                                  <Copy size={13} /> Copy
                                </>
                              )}
                            </button>

                            <button
                              type="button"
                              className="btn-ghost"
                              style={{
                                fontSize: 12,
                                padding: "4px 8px",
                                color: msg.feedback === "up" ? "#60a5fa" : undefined,
                              }}
                              onClick={() => onFeedback(index, "up")}
                              title="Good response"
                            >
                              <ThumbsUp size={13} fill={msg.feedback === "up" ? "#60a5fa" : "none"} />
                            </button>

                            <button
                              type="button"
                              className="btn-ghost"
                              style={{
                                fontSize: 12,
                                padding: "4px 8px",
                                color: msg.feedback === "down" ? "#f87171" : undefined,
                              }}
                              onClick={() => onFeedback(index, "down")}
                              title="Bad response"
                            >
                              <ThumbsDown size={13} fill={msg.feedback === "down" ? "#f87171" : "none"} />
                            </button>

                            <button
                              type="button"
                              className="btn-ghost"
                              style={{ fontSize: 12, padding: "4px 8px" }}
                              onClick={onRetry}
                              title="Retry prompt"
                            >
                              <RefreshCw size={13} />
                            </button>

                            <button
                              type="button"
                              className="btn-ghost"
                              style={{ fontSize: 12, padding: "4px 8px" }}
                              onClick={() => exportAsMarkdown(msg.content)}
                              title="Export response as Markdown"
                            >
                              <Download size={13} /> Export .md
                            </button>
                          </div>
                        </div>
                      )}
                    </div>
                  </div>
                );
              })
            )}

            {/* Live Streaming Claude-style Working Steps block */}
            {busy && streamingSteps.length > 0 && (
              <div style={{ display: "flex", gap: 16, marginBottom: 28, alignItems: "flex-start" }}>
                <div
                  style={{
                    width: 30,
                    height: 30,
                    borderRadius: 8,
                    background: "#181824",
                    border: "1px solid rgba(59, 130, 246, 0.4)",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    color: "#3b82f6",
                    flexShrink: 0,
                  }}
                >
                  <Sparkles size={15} />
                </div>

                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
                    <span style={{ fontWeight: 650, fontSize: "13px", color: "var(--text-primary)" }}>Orion</span>
                    <span style={{ fontSize: 11, color: "var(--text-muted)" }}>• Processing multi-agent lake</span>
                  </div>

                  <WorkingSteps
                    steps={streamingSteps}
                    isStreaming={true}
                    elapsedSeconds={streamingElapsed}
                  />
                </div>
              </div>
            )}

            <div ref={endRef} />
          </div>

          {/* Modernized Chat Composer with Model, Effort, WebSearch & Agent Pin Popover */}
          <div className="chat-composer-wrap" style={{ padding: "12px 32px 18px 32px", background: "var(--bg-canvas)" }}>
            {error && (
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  marginBottom: 10,
                  padding: "8px 12px",
                  borderRadius: "var(--radius-sm)",
                  background: "var(--status-danger-bg)",
                  color: "var(--status-danger)",
                  fontSize: 13,
                }}
              >
                <AlertTriangle size={15} />
                <span>{error}</span>
              </div>
            )}

            <form
              onSubmit={onSend}
              style={{
                background: "var(--bg-surface)",
                border: "1px solid var(--border-default)",
                borderRadius: "14px",
                padding: "12px 16px",
                display: "flex",
                flexDirection: "column",
                gap: "10px",
                boxShadow: "0 8px 30px rgba(0, 0, 0, 0.45)",
              }}
            >
              {/* Active Pinned Agent / Attachments Chip Bar */}
              {(pinnedAgent || files.length > 0) && (
                <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", paddingBottom: 4 }}>
                  {pinnedAgent && (
                    <div
                      style={{
                        display: "inline-flex",
                        alignItems: "center",
                        gap: 6,
                        padding: "3px 8px",
                        borderRadius: 6,
                        background: "rgba(59, 130, 246, 0.15)",
                        border: "1px solid rgba(59, 130, 246, 0.3)",
                        fontSize: 11.5,
                        color: "#60a5fa",
                        fontWeight: 550,
                      }}
                    >
                      <Cpu size={12} />
                      <span>Pinned: {pinnedAgent.toUpperCase()}</span>
                      <button
                        type="button"
                        onClick={() => setPinnedAgent("")}
                        style={{ border: "none", background: "transparent", color: "inherit", cursor: "pointer", padding: 0 }}
                      >
                        <X size={12} />
                      </button>
                    </div>
                  )}

                  {files.length > 0 && (
                    <div
                      style={{
                        display: "inline-flex",
                        alignItems: "center",
                        gap: 6,
                        padding: "3px 8px",
                        borderRadius: 6,
                        background: "var(--bg-elevated)",
                        border: "1px solid var(--border-subtle)",
                        fontSize: 11.5,
                        color: "var(--text-secondary)",
                      }}
                    >
                      <Paperclip size={12} />
                      <span>{files.length} attached document(s)</span>
                      <button
                        type="button"
                        onClick={() => setFiles([])}
                        style={{ border: "none", background: "transparent", color: "inherit", cursor: "pointer", padding: 0 }}
                      >
                        <X size={12} />
                      </button>
                    </div>
                  )}
                </div>
              )}

              {/* Textarea */}
              <textarea
                className="composer-input"
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={onKeyDown}
                placeholder={
                  pinnedAgent
                    ? `Ask ${pinnedAgent.toUpperCase()} specialist directly...`
                    : "Ask Orion across enterprise lake filings, payroll, schedules..."
                }
                rows={2}
                disabled={busy}
                style={{ fontSize: "14.5px" }}
              />

              {/* Bottom Toolbar: Attach, Agent Menu Drawer, Model, Effort, WebSearch & Send */}
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10, flexWrap: "wrap", paddingTop: 4 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                  {/* File Upload */}
                  <input
                    ref={fileInputRef}
                    type="file"
                    multiple
                    hidden
                    onChange={(e) => setFiles(Array.from(e.target.files || []))}
                  />
                  <button
                    type="button"
                    className="btn-ghost"
                    style={{ padding: "5px 8px", fontSize: 12 }}
                    onClick={() => fileInputRef.current?.click()}
                    disabled={busy}
                    title="Attach live documents or data files"
                  >
                    <Paperclip size={14} />
                  </button>

                  {/* 1. AGENTS MENU DRAWER / POPOVER IN CHAT INPUT */}
                  <div ref={agentMenuRef} style={{ position: "relative" }}>
                    <button
                      type="button"
                      className={`btn-secondary ${pinnedAgent ? "active" : ""}`}
                      style={{ padding: "4px 10px", fontSize: 12 }}
                      onClick={() => setAgentMenuOpen(!agentMenuOpen)}
                    >
                      <Cpu size={13} color={pinnedAgent ? "#3b82f6" : undefined} />
                      <span>{pinnedAgent ? pinnedAgent.toUpperCase() : "Specialists"}</span>
                      <ChevronDown size={11} />
                    </button>

                    {agentMenuOpen && (
                      <div
                        style={{
                          position: "absolute",
                          bottom: "calc(100% + 8px)",
                          left: 0,
                          width: 280,
                          background: "var(--bg-elevated)",
                          border: "1px solid var(--border-default)",
                          borderRadius: "10px",
                          padding: "10px",
                          boxShadow: "0 12px 32px rgba(0, 0, 0, 0.65)",
                          zIndex: 100,
                        }}
                      >
                        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8, paddingBottom: 6, borderBottom: "1px solid var(--border-subtle)" }}>
                          <span style={{ fontSize: 11.5, fontWeight: 650, color: "var(--text-muted)", textTransform: "uppercase" }}>
                            Specialist Agents
                          </span>
                          {pinnedAgent && (
                            <button
                              type="button"
                              className="btn-chip"
                              style={{ fontSize: 10, padding: "1px 5px" }}
                              onClick={() => {
                                setPinnedAgent("");
                                setAgentMenuOpen(false);
                              }}
                            >
                              Clear Pin
                            </button>
                          )}
                        </div>

                        <div style={{ display: "flex", flexDirection: "column", gap: 6, maxHeight: 220, overflowY: "auto" }}>
                          {agents.map((ag) => {
                            const isPinned = pinnedAgent === ag.id;
                            const isAllowed = selectedAgents.length === 0 || selectedAgents.includes(ag.id);
                            const tag = AGENT_TAG_STYLES[ag.id.toLowerCase()];

                            return (
                              <div
                                key={ag.id}
                                style={{
                                  display: "flex",
                                  alignItems: "center",
                                  justifyContent: "space-between",
                                  padding: "6px 8px",
                                  borderRadius: "6px",
                                  background: isPinned ? "rgba(59, 130, 246, 0.12)" : "var(--bg-surface)",
                                  border: isPinned ? "1px solid #3b82f6" : "1px solid var(--border-subtle)",
                                }}
                              >
                                <label style={{ display: "flex", alignItems: "center", gap: 6, cursor: "pointer", minWidth: 0, flex: 1 }}>
                                  <input
                                    type="checkbox"
                                    checked={isAllowed}
                                    onChange={() => toggleAgent(ag.id)}
                                    style={{ accentColor: "#3b82f6" }}
                                  />
                                  <span style={{ fontSize: 12, fontWeight: 550, color: tag?.text || "var(--text-primary)" }}>
                                    {ag.name}
                                  </span>
                                </label>

                                <button
                                  type="button"
                                  className={`btn-chip ${isPinned ? "active" : ""}`}
                                  style={{ fontSize: 10, padding: "2px 6px" }}
                                  onClick={() => {
                                    setPinnedAgent(isPinned ? "" : ag.id);
                                    setAgentMenuOpen(false);
                                  }}
                                >
                                  {isPinned ? "Pinned" : "Pin"}
                                </button>
                              </div>
                            );
                          })}
                        </div>
                      </div>
                    )}
                  </div>

                  {/* 2. MODEL SELECTOR */}
                  <div ref={modelMenuRef} style={{ position: "relative" }}>
                    <button
                      type="button"
                      className="btn-secondary"
                      style={{ padding: "4px 10px", fontSize: 12 }}
                      onClick={() => setModelMenuOpen(!modelMenuOpen)}
                    >
                      <Sparkles size={12} color="#60a5fa" />
                      <span>{selectedModel}</span>
                      <ChevronDown size={11} />
                    </button>

                    {modelMenuOpen && (
                      <div
                        style={{
                          position: "absolute",
                          bottom: "calc(100% + 8px)",
                          left: 0,
                          width: 220,
                          background: "var(--bg-elevated)",
                          border: "1px solid var(--border-default)",
                          borderRadius: "10px",
                          padding: "6px",
                          boxShadow: "0 12px 32px rgba(0, 0, 0, 0.65)",
                          zIndex: 100,
                          display: "flex",
                          flexDirection: "column",
                          gap: 2,
                        }}
                      >
                        {[
                          { id: "Orion 5.0 Auto", label: "Orion 5.0 Auto", desc: "Dynamic multi-agent routing" },
                          { id: "Orion Deep Reasoner", label: "Orion Deep Reasoner", desc: "Complex cross-lake synthesis" },
                          { id: "Local Engine", label: "Local Engine (Ollama)", desc: "Private on-premise execution" },
                        ].map((m) => (
                          <div
                            key={m.id}
                            onClick={() => {
                              setSelectedModel(m.id);
                              setModelMenuOpen(false);
                            }}
                            style={{
                              padding: "6px 8px",
                              borderRadius: "6px",
                              cursor: "pointer",
                              background: selectedModel === m.id ? "rgba(255, 255, 255, 0.08)" : "transparent",
                            }}
                          >
                            <div style={{ fontSize: "12px", fontWeight: 550, color: "var(--text-primary)" }}>{m.label}</div>
                            <div style={{ fontSize: "10.5px", color: "var(--text-muted)" }}>{m.desc}</div>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>

                  {/* 3. REASONING EFFORT SELECTOR */}
                  <div ref={effortMenuRef} style={{ position: "relative" }}>
                    <button
                      type="button"
                      className="btn-secondary"
                      style={{ padding: "4px 10px", fontSize: 12 }}
                      onClick={() => setEffortMenuOpen(!effortMenuOpen)}
                    >
                      <Zap size={12} color="#f59e0b" />
                      <span style={{ textTransform: "capitalize" }}>Effort: {effortLevel}</span>
                      <ChevronDown size={11} />
                    </button>

                    {effortMenuOpen && (
                      <div
                        style={{
                          position: "absolute",
                          bottom: "calc(100% + 8px)",
                          left: 0,
                          width: 170,
                          background: "var(--bg-elevated)",
                          border: "1px solid var(--border-default)",
                          borderRadius: "8px",
                          padding: "4px",
                          boxShadow: "0 12px 32px rgba(0, 0, 0, 0.65)",
                          zIndex: 100,
                          display: "flex",
                          flexDirection: "column",
                          gap: 2,
                        }}
                      >
                        {(["high", "medium", "low"] as const).map((lvl) => (
                          <div
                            key={lvl}
                            onClick={() => {
                              setEffortLevel(lvl);
                              setEffortMenuOpen(false);
                            }}
                            style={{
                              padding: "6px 8px",
                              borderRadius: "4px",
                              cursor: "pointer",
                              fontSize: "12px",
                              textTransform: "capitalize",
                              fontWeight: effortLevel === lvl ? 600 : 400,
                              background: effortLevel === lvl ? "rgba(255, 255, 255, 0.08)" : "transparent",
                              color: effortLevel === lvl ? "#60a5fa" : "var(--text-primary)",
                            }}
                          >
                            {lvl} Reasoning
                          </div>
                        ))}
                      </div>
                    )}
                  </div>

                  {/* 4. WEB SEARCH ON / OFF TOGGLE */}
                  <button
                    type="button"
                    onClick={() => setWebSearchEnabled(!webSearchEnabled)}
                    className={`btn-chip ${webSearchEnabled ? "active" : ""}`}
                    style={{
                      padding: "4px 10px",
                      fontSize: 12,
                      display: "inline-flex",
                      alignItems: "center",
                      gap: 6,
                      background: webSearchEnabled ? "rgba(59, 130, 246, 0.18)" : undefined,
                      borderColor: webSearchEnabled ? "#3b82f6" : undefined,
                      color: webSearchEnabled ? "#60a5fa" : undefined,
                    }}
                    title="Toggle external Web Search tool"
                  >
                    <Globe size={13} />
                    <span>Search: {webSearchEnabled ? "On" : "Off"}</span>
                  </button>
                </div>

                {/* Send Button */}
                <button
                  type="submit"
                  className="btn-primary"
                  disabled={busy || (!draft.trim() && files.length === 0)}
                  style={{ minWidth: 80, height: 32, padding: "0 14px", borderRadius: "8px" }}
                >
                  {busy ? (
                    <RefreshCw size={14} className="animate-spin" />
                  ) : (
                    <>
                      Send <Send size={13} />
                    </>
                  )}
                </button>
              </div>
            </form>
          </div>
        </main>
      </div>

      <style jsx global>{`
        @keyframes spin {
          from { transform: rotate(0deg); }
          to { transform: rotate(360deg); }
        }
      `}</style>
    </div>
  );
}
