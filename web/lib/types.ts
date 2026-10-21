export type Session = {
  token: string;
  username: string;
  role: string;
  tenant?: string;
};

export type Agent = {
  id: string;
  name: string;
  description: string;
  status?: "active" | "paused" | "retired" | "draft" | "deployed";
  builtin?: boolean;
  tools?: string[];
  entities?: string[];
};

export type AgentVersion = {
  version: number;
  created_at: string;
  created_by: string;
  change_note?: string | null;
};

export type ThreadSummary = {
  id: string;
  title: string;
  summary?: string;
  created_at?: string;
  updated_at?: string;
  message_count?: number;
};

export type ThreadMessage = {
  id: number;
  role: "user" | "assistant" | "system";
  content: string;
  created_at?: string;
  metadata?: Record<string, unknown>;
};

export type ChatTask = {
  id: string;
  description: string;
  agent: string;
  status: string;
  result?: string | null;
  failure_class?: string | null;
};

export type ChatStreamEvent = {
  type: string;
  status: string;
  response: string;
  agents: string[];
  requestId: string;
  tasks?: ChatTask[];
  raw?: Record<string, unknown>;
};

export type KBResource = {
  id: string;
  filename?: string;
  kind?: string;
  table?: string;
  chunks?: number;
  uploaded_by?: string;
  created_at?: string;
};

export type KBCatalog = {
  resources: KBResource[];
  tables: string[];
  connectors: Array<{
    id: string;
    name: string;
    type: string;
    status: string;
  }>;
};

export type LabAgent = {
  id: string;
  name: string;
  description: string;
  status: string;
  version: number;
  entities: string[];
  tools: string[] | string;
  mode: string;
  evaluation?: {
    score?: number;
    passed?: boolean;
    tests?: Array<{ name: string; score: number; passed: boolean; details?: string }>;
  } | null;
};

export type LabEvaluation = {
  agent_id: string;
  version: number;
  timestamp: string;
  passed: boolean;
  score: number;
  min_score: number;
  tests: Array<{
    name: string;
    passed: boolean;
    score: number;
    detail?: string;
  }>;
};

export type TraceSummary = {
  request_id: string;
  user?: string;
  tenant?: string;
  query?: string;
  started_at?: number;
};

export type TraceSpan = {
  name: string;
  kind?: string;
  start_ns?: number;
  end_ns?: number;
  latency_ms?: number;
  extra?: Record<string, unknown>;
};

export type TraceDetail = {
  request_id: string;
  n: number;
  spans: TraceSpan[];
};

export type RequestGraph = {
  request_id: string;
  graph: {
    nodes?: Array<{ id: string; label?: string; agent?: string; status?: string }>;
    edges?: Array<{ from: string; to: string }>;
  };
  tasks: Array<Record<string, unknown>>;
  success?: boolean;
  heal_events: Array<{
    name?: string;
    latency_ms?: number;
    attempt?: number;
    agent?: string;
    error?: string;
    action?: string;
    reason?: string;
  }>;
};

export type Approval = {
  id: string;
  username: string;
  tool: string;
  arguments: Record<string, unknown>;
  status: "pending" | "approved" | "denied";
  created_at: string;
  decided_at?: string;
  decided_by?: string;
  reason?: string;
};

export type QualityQueueItem = {
  id: string;
  request_id: string;
  query: string;
  response: string;
  feedback?: string;
  agent?: string;
  status: "pending" | "dismissed" | "resolved";
  created_at: string;
  score?: number;
};

export type MCPServerStatus = {
  name: string;
  status: "connected" | "disconnected" | "error";
  tool_names?: string[];
  error?: string;
};

export type MCPBridgeStatus = {
  status: string;
  servers: MCPServerStatus[];
  tools_count: number;
};

export type SystemSetting = {
  key: string;
  value: unknown;
  source: string;
  default: unknown;
  type: "string" | "integer" | "float" | "boolean" | "list";
  description: string;
  choices: string[];
  minimum?: number | null;
  maximum?: number | null;
};

export type SystemUser = {
  username: string;
  role: "super_user" | "limited";
  tenant: string;
  is_active: boolean;
  created_at?: string;
};

export type HealthStatus = {
  status: "healthy" | "degraded";
  message: string;
  worker_provider: string;
  worker_model: string;
  router_model: string;
  judge_model: string;
  frozen_ok: boolean;
  guard: {
    available: boolean;
    [key: string]: unknown;
  };
  mcp: {
    available: boolean;
    servers?: MCPServerStatus[];
    [key: string]: unknown;
  };
};
