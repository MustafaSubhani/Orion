import type {
  Agent,
  AgentVersion,
  Approval,
  ChatStreamEvent,
  HealthStatus,
  KBCatalog,
  LabAgent,
  LabEvaluation,
  MCPBridgeStatus,
  QualityQueueItem,
  RequestGraph,
  Session,
  SystemSetting,
  SystemUser,
  ThreadMessage,
  ThreadSummary,
  TraceDetail,
  TraceSummary,
} from "./types";

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

export function cleanText(value: string): string {
  if (!value) return "";
  return value.replace(/\u2014/g, ", ").replace(/\u2013/g, ", ");
}

async function readError(res: Response): Promise<string> {
  try {
    const body = await res.json();
    const detail = body?.error?.message ?? body.detail ?? body.message;
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

// ----------------- AUTH -----------------

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
  const ident = await parse<{ username: string; role: string; tenant: string }>(res);
  const next = { token: current.token, username: ident.username, role: ident.role || current.role, tenant: ident.tenant || current.tenant };
  saveSession(next);
  return next;
}

export async function getOidcConfig(): Promise<{ enabled: boolean; issuer: string | null; client_id: string | null }> {
  const res = await fetch(`${API}/auth/oidc/config`);
  return parse(res);
}

export async function oidcLogin(idToken: string): Promise<Session> {
  const res = await fetch(`${API}/auth/oidc`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id_token: idToken }),
  });
  return parse<Session>(res);
}

// ----------------- SYSTEM & HEALTH -----------------

export async function getHealth(): Promise<HealthStatus> {
  const res = await fetch(`${API}/health`);
  return parse<HealthStatus>(res);
}

export async function getMetrics(): Promise<string> {
  const res = await fetch(`${API}/metrics`, { headers: authHeader() });
  if (res.status === 401) throw new AuthError("Session expired");
  return res.text();
}

// ----------------- THREADS -----------------

export async function listThreads(limit = 50): Promise<ThreadSummary[]> {
  const res = await fetch(`${API}/threads?limit=${limit}`, { headers: authHeader() });
  return parse<ThreadSummary[]>(res);
}

export async function createThread(title = "New Conversation"): Promise<ThreadSummary> {
  const res = await fetch(`${API}/threads`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeader() },
    body: JSON.stringify({ title }),
  });
  return parse<ThreadSummary>(res);
}

export async function getThreadMessages(threadId: string): Promise<{
  id: string;
  title: string;
  summary?: string;
  messages: ThreadMessage[];
}> {
  const res = await fetch(`${API}/threads/${encodeURIComponent(threadId)}/messages`, { headers: authHeader() });
  return parse(res);
}

export async function renameThread(threadId: string, title: string): Promise<ThreadSummary> {
  const res = await fetch(`${API}/threads/${encodeURIComponent(threadId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json", ...authHeader() },
    body: JSON.stringify({ title }),
  });
  return parse<ThreadSummary>(res);
}

export async function deleteThread(threadId: string): Promise<{ ok: boolean; id: string }> {
  const res = await fetch(`${API}/threads/${encodeURIComponent(threadId)}`, {
    method: "DELETE",
    headers: authHeader(),
  });
  return parse(res);
}

// ----------------- CHAT & STREAMING -----------------

function statusFor(ev: Record<string, unknown>, fallback: string): string {
  const type = String(ev.type || "");
  if (typeof ev.message === "string" && ev.message) return cleanText(ev.message);
  if (type === "tasks_identified") return "Synthesizing execution plan";
  if (type === "task_start") return ev.agent ? `Routing to ${ev.agent}` : "Processing task";
  if (type === "task_complete") return ev.agent ? `Completed ${ev.agent}` : "Finished step";
  if (type === "backtrack") return "Self-healing: rewinding failed execution path";
  if (type === "synthesizing") return "Synthesizing answer from agent outputs";
  if (type === "final") return "Complete";
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
  allowedAgents?: string[],
  pinAgent?: string,
  threadId?: string,
): AsyncGenerator<ChatStreamEvent> {
  const payload: Record<string, unknown> = {
    message,
    stream: true,
  };
  if (allowedAgents && allowedAgents.length > 0) payload.allowed_agents = allowedAgents;
  if (pinAgent) payload.pin_agent = pinAgent;
  if (threadId) payload.thread_id = threadId;

  const res = await fetch(`${API}/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeader() },
    body: JSON.stringify(payload),
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

  const apply = (ev: Record<string, unknown>): ChatStreamEvent => {
    const type = String(ev.type || "");
    if (type === "error") throw new Error(cleanText(String(ev.message || "Request failed")));
    status = statusFor(ev, status);
    if (typeof ev.response === "string") response = cleanText(ev.response);
    if (Array.isArray(ev.agents_used)) agents = ev.agents_used.map(String);
    if (Array.isArray(ev.agents) && !agents.length) agents = ev.agents.map(String);
    if (ev.request_id) requestId = String(ev.request_id);

    return {
      type,
      status,
      response,
      agents,
      requestId,
      raw: ev,
    };
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

// ----------------- AGENTS -----------------

export async function listAgents(): Promise<Agent[]> {
  const res = await fetch(`${API}/agents`, { headers: authHeader() });
  return parse<Agent[]>(res);
}

export async function getAgent(agentId: string): Promise<Agent> {
  const res = await fetch(`${API}/agents/${encodeURIComponent(agentId)}`, { headers: authHeader() });
  return parse<Agent>(res);
}

export async function setAgentStatus(agentId: string, status: "active" | "paused" | "retired"): Promise<Agent> {
  const res = await fetch(`${API}/agents/${encodeURIComponent(agentId)}/status`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeader() },
    body: JSON.stringify({ status }),
  });
  return parse<Agent>(res);
}

export async function listAgentVersions(agentId: string): Promise<AgentVersion[]> {
  const res = await fetch(`${API}/agents/${encodeURIComponent(agentId)}/versions`, { headers: authHeader() });
  return parse<AgentVersion[]>(res);
}

export async function rollbackAgent(agentId: string, version: number): Promise<Agent> {
  const res = await fetch(`${API}/agents/${encodeURIComponent(agentId)}/rollback`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeader() },
    body: JSON.stringify({ version }),
  });
  return parse<Agent>(res);
}

// ----------------- KNOWLEDGE BASE (KB) -----------------

export async function getKBCatalog(): Promise<KBCatalog> {
  const res = await fetch(`${API}/kb`, { headers: authHeader() });
  return parse<KBCatalog>(res);
}

export async function ingestFiles(files: File[], username: string): Promise<string> {
  const form = new FormData();
  form.append("user", username);
  for (const file of files) form.append("files", file);
  const res = await fetch(`${API}/ingest`, { method: "POST", headers: authHeader(), body: form });
  const data = await parse<{ ingested?: Array<{ filename?: string; error?: string; kind?: string; chunks?: number }> }>(res);
  const lines = ["Attached to live KB:"];
  for (const rec of data.ingested || []) {
    if (rec.error) lines.push(`${rec.filename}: ${rec.error}`);
    else lines.push(`${rec.filename} (${rec.kind || "file"}, ${rec.chunks || 0} chunks)`);
  }
  return lines.join("\n");
}

export async function deleteKBResource(resourceId: string): Promise<{ ok: boolean; id: string }> {
  const res = await fetch(`${API}/kb/${encodeURIComponent(resourceId)}`, {
    method: "DELETE",
    headers: authHeader(),
  });
  return parse(res);
}

export async function reindexKB(): Promise<{ reindexed: number }> {
  const res = await fetch(`${API}/kb/reindex`, {
    method: "POST",
    headers: authHeader(),
  });
  return parse(res);
}

// ----------------- LAB (SYNTHESIZER & EVALUATOR) -----------------

export async function sendLab(message: string): Promise<string> {
  const res = await fetch(`${API}/lab`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeader() },
    body: JSON.stringify({ message }),
  });
  const data = await parse<{ response?: string }>(res);
  return cleanText(String(data.response || ""));
}

export async function listLabAgents(): Promise<LabAgent[]> {
  const res = await fetch(`${API}/lab/agents`, { headers: authHeader() });
  return parse<LabAgent[]>(res);
}

export async function getLabAgent(agentId: string): Promise<LabAgent> {
  const res = await fetch(`${API}/lab/agents/${encodeURIComponent(agentId)}`, { headers: authHeader() });
  return parse<LabAgent>(res);
}

export async function createLabAgent(spec: {
  name: string;
  job: string;
  entities: string[];
  tools: string[] | string;
  mode: "card" | "code";
}): Promise<LabAgent> {
  const res = await fetch(`${API}/lab/agents`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeader() },
    body: JSON.stringify(spec),
  });
  return parse<LabAgent>(res);
}

export async function evaluateLabAgent(agentId: string): Promise<LabEvaluation> {
  const res = await fetch(`${API}/lab/agents/${encodeURIComponent(agentId)}/evaluate`, {
    method: "POST",
    headers: authHeader(),
  });
  return parse<LabEvaluation>(res);
}

export async function getLabEvaluation(agentId: string): Promise<LabEvaluation> {
  const res = await fetch(`${API}/lab/agents/${encodeURIComponent(agentId)}/evaluation`, { headers: authHeader() });
  return parse<LabEvaluation>(res);
}

export async function deployLabAgent(agentId: string): Promise<LabAgent> {
  const res = await fetch(`${API}/lab/agents/${encodeURIComponent(agentId)}/deploy`, {
    method: "POST",
    headers: authHeader(),
  });
  return parse<LabAgent>(res);
}

// ----------------- OBSERVABILITY & TRACES -----------------

export async function listTraces(limit = 50, user?: string): Promise<TraceSummary[]> {
  const query = user ? `?limit=${limit}&user=${encodeURIComponent(user)}` : `?limit=${limit}`;
  const res = await fetch(`${API}/traces${query}`, { headers: authHeader() });
  return parse<TraceSummary[]>(res);
}

export async function getTrace(requestId: string): Promise<TraceDetail> {
  const res = await fetch(`${API}/traces/${encodeURIComponent(requestId)}`, { headers: authHeader() });
  return parse<TraceDetail>(res);
}

export async function getRequestGraph(requestId: string): Promise<RequestGraph> {
  const res = await fetch(`${API}/requests/${encodeURIComponent(requestId)}/graph`, { headers: authHeader() });
  return parse<RequestGraph>(res);
}

export function exportTraceUrl(requestId: string): string {
  return `${API}/traces/${encodeURIComponent(requestId)}/export`;
}

// ----------------- HUMAN-IN-THE-LOOP APPROVALS -----------------

export async function listApprovals(status?: "pending" | "approved" | "denied"): Promise<Approval[]> {
  const query = status ? `?status=${status}` : "";
  const res = await fetch(`${API}/approvals${query}`, { headers: authHeader() });
  return parse<Approval[]>(res);
}

export async function decideApproval(approvalId: string, decision: "approve" | "deny"): Promise<Approval> {
  const res = await fetch(`${API}/approvals/${encodeURIComponent(approvalId)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeader() },
    body: JSON.stringify({ decision }),
  });
  return parse<Approval>(res);
}

// ----------------- QUALITY QUEUE & SELF-REPAIR -----------------

export async function listQualityQueue(status: "pending" | "dismissed" | "resolved" = "pending"): Promise<QualityQueueItem[]> {
  const res = await fetch(`${API}/admin/quality/queue?status=${status}`, { headers: authHeader() });
  return parse<QualityQueueItem[]>(res);
}

export async function decideQualityQueue(
  itemId: string,
  action: "dismiss" | "repair",
): Promise<{ id: string; status: string }> {
  const res = await fetch(`${API}/admin/quality/queue/${encodeURIComponent(itemId)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeader() },
    body: JSON.stringify({ action }),
  });
  return parse(res);
}

// ----------------- MCP & CONNECTORS -----------------

export async function getMcpStatus(): Promise<MCPBridgeStatus> {
  const res = await fetch(`${API}/admin/mcp`, { headers: authHeader() });
  return parse<MCPBridgeStatus>(res);
}

export async function reloadMcp(): Promise<Record<string, unknown>> {
  const res = await fetch(`${API}/admin/mcp/reload`, {
    method: "POST",
    headers: authHeader(),
  });
  return parse(res);
}

export async function listConnectors(): Promise<Array<{ id: string; name: string; type: string; config?: Record<string, unknown> }>> {
  const res = await fetch(`${API}/admin/connectors`, { headers: authHeader() });
  return parse(res);
}

export async function reloadConnectors(): Promise<{ loaded: number }> {
  const res = await fetch(`${API}/admin/connectors/reload`, {
    method: "POST",
    headers: authHeader(),
  });
  return parse(res);
}

export async function testConnector(connectorId: string): Promise<{ ok: boolean; message?: string }> {
  const res = await fetch(`${API}/admin/connectors/${encodeURIComponent(connectorId)}/test`, {
    method: "POST",
    headers: authHeader(),
  });
  return parse(res);
}

// ----------------- SETTINGS & USER MANAGEMENT -----------------

export async function listSettings(): Promise<SystemSetting[]> {
  const res = await fetch(`${API}/admin/settings`, { headers: authHeader() });
  return parse<SystemSetting[]>(res);
}

export async function updateSetting(key: string, value: unknown): Promise<SystemSetting> {
  const res = await fetch(`${API}/admin/settings/${encodeURIComponent(key)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json", ...authHeader() },
    body: JSON.stringify({ value }),
  });
  return parse<SystemSetting>(res);
}

export async function resetSetting(key: string): Promise<SystemSetting> {
  const res = await fetch(`${API}/admin/settings/${encodeURIComponent(key)}`, {
    method: "DELETE",
    headers: authHeader(),
  });
  return parse<SystemSetting>(res);
}

export async function listUsers(): Promise<SystemUser[]> {
  const res = await fetch(`${API}/admin/users`, { headers: authHeader() });
  return parse<SystemUser[]>(res);
}

export async function createUser(data: {
  username: string;
  password: string;
  role: "super_user" | "limited";
  tenant?: string;
}): Promise<SystemUser> {
  const res = await fetch(`${API}/admin/users`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeader() },
    body: JSON.stringify(data),
  });
  return parse<SystemUser>(res);
}

export async function updateUserRole(username: string, role: "super_user" | "limited"): Promise<SystemUser> {
  const res = await fetch(`${API}/admin/users/${encodeURIComponent(username)}/role`, {
    method: "PUT",
    headers: { "Content-Type": "application/json", ...authHeader() },
    body: JSON.stringify({ role }),
  });
  return parse<SystemUser>(res);
}

export async function deactivateUser(username: string): Promise<{ ok: boolean; username: string }> {
  const res = await fetch(`${API}/admin/users/${encodeURIComponent(username)}`, {
    method: "DELETE",
    headers: authHeader(),
  });
  return parse(res);
}
