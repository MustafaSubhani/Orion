import type { Agent, Session } from "./types";

const API = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000";
const KEY = "orion.session";

export class AuthError extends Error {
  constructor(message = "Session expired") {
    super(message);
    this.name = "AuthError";
  }
}

export function apiUrl(): string {
  return API;
}

export function loadSession(): Session | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Session;
    if (!parsed.token || !parsed.username) return null;
    return parsed;
  } catch {
    return null;
  }
}

export function saveSession(session: Session): void {
  localStorage.setItem(KEY, JSON.stringify(session));
}

export function clearSession(): void {
  localStorage.removeItem(KEY);
}

export function authHeader(token?: string): HeadersInit {
  const session = token || loadSession()?.token;
  if (!session) return {};
  return { Authorization: `Bearer ${session}` };
}

function cleanText(value: string): string {
  return value.replace(/\u2014/g, ", ").replace(/\u2013/g, ", ");
}

async function readError(res: Response): Promise<string> {
  try {
    const body = await res.json();
    const detail = body.detail ?? body.message;
    if (Array.isArray(detail)) {
      return detail.map((item) => item.msg || JSON.stringify(item)).join("; ");
    }
    return cleanText(String(detail || res.statusText));
  } catch {
    return res.statusText || `HTTP ${res.status}`;
  }
}

async function parse<T>(res: Response): Promise<T> {
  if (res.status === 401) {
    clearSession();
    throw new AuthError(await readError(res));
  }
  if (!res.ok) throw new Error(await readError(res));
  return res.json() as Promise<T>;
}

export async function login(username: string, password: string): Promise<Session> {
  const res = await fetch(`${API}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  return parse<Session>(res);
}

export async function signup(username: string, password: string): Promise<Session> {
  const res = await fetch(`${API}/auth/signup`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  return parse<Session>(res);
}

export async function logout(): Promise<void> {
  const session = loadSession();
  if (session) {
    await fetch(`${API}/auth/logout`, { method: "POST", headers: authHeader(session.token) }).catch(() => undefined);
  }
  clearSession();
}

export async function me(): Promise<Session> {
  const current = loadSession();
  if (!current) throw new AuthError("Not signed in");
  const res = await fetch(`${API}/auth/me`, { headers: authHeader(current.token) });
  const ident = await parse<{ username: string; role: string }>(res);
  const next = { token: current.token, username: ident.username, role: ident.role || current.role };
  saveSession(next);
  return next;
}

export async function listAgents(): Promise<Agent[]> {
  const res = await fetch(`${API}/agents`, { headers: authHeader() });
  return parse<Agent[]>(res);
}

export async function ingestFiles(files: File[], username: string): Promise<string> {
  const form = new FormData();
  form.append("user", username);
  for (const file of files) form.append("files", file);
  const res = await fetch(`${API}/ingest`, { method: "POST", headers: authHeader(), body: form });
  const data = await parse<{ ingested?: Array<{ filename?: string; error?: string; kind?: string; chunks?: number }> }>(res);
  const lines = ["Attached to live KB (not the paper lake):"];
  for (const rec of data.ingested || []) {
    if (rec.error) lines.push(`${rec.filename}: ${rec.error}`);
    else lines.push(`${rec.filename} (${rec.kind || "file"}, ${rec.chunks || 0} chunks)`);
  }
  return lines.join("\n");
}

export async function sendLab(message: string): Promise<string> {
  const res = await fetch(`${API}/lab`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeader() },
    body: JSON.stringify({ message }),
  });
  const data = await parse<{ response?: string }>(res);
  return cleanText(String(data.response || ""));
}

function statusFor(ev: Record<string, unknown>, fallback: string): string {
  const type = String(ev.type || "");
  if (typeof ev.message === "string" && ev.message) return cleanText(ev.message);
  if (type === "tasks_identified") return "Planning";
  if (type === "task_start") return ev.agent ? `Running ${ev.agent}` : "Running";
  if (type === "task_complete") return ev.agent ? `Finished ${ev.agent}` : "Finished step";
  if (type === "backtrack") return "Rewinding";
  if (type === "synthesizing") return "Combining results";
  if (type === "final") return "Done";
  return fallback;
}

function parseSseBlock(part: string): Record<string, unknown> | null {
  const line = part.split("\n").find((row) => row.startsWith("data:"));
  if (!line) return null;
  try {
    return JSON.parse(line.slice(5).trim()) as Record<string, unknown>;
  } catch {
    return null;
  }
}

export async function* streamChat(
  message: string,
  allowedAgents: string[],
  username: string,
): AsyncGenerator<{ status: string; response: string; agents: string[]; requestId: string }> {
  const res = await fetch(`${API}/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeader() },
    body: JSON.stringify({
      message,
      stream: true,
      allowed_agents: allowedAgents,
      user: username,
    }),
  });
  if (res.status === 401) {
    clearSession();
    throw new AuthError(await readError(res));
  }
  if (!res.ok) throw new Error(await readError(res));
  if (!res.body) throw new Error("No response stream");
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let status = "Working";
  let response = "";
  let agents: string[] = [];
  let requestId = "";

  const apply = (ev: Record<string, unknown>) => {
    const type = String(ev.type || "");
    if (type === "error") throw new Error(cleanText(String(ev.message || "Request failed")));
    status = statusFor(ev, status);
    if (typeof ev.response === "string") response = cleanText(ev.response);
    if (Array.isArray(ev.agents_used)) agents = ev.agents_used.map(String);
    if (Array.isArray(ev.agents) && !agents.length) agents = ev.agents.map(String);
    if (ev.request_id) requestId = String(ev.request_id);
    return { status, response, agents, requestId };
  };

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split("\n\n");
    buffer = parts.pop() || "";
    for (const part of parts) {
      const ev = parseSseBlock(part);
      if (ev) yield apply(ev);
    }
  }
  const tail = parseSseBlock(buffer);
  if (tail) yield apply(tail);
}
