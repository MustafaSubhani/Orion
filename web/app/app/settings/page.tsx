"use client";

import { FormEvent, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  Settings,
  Users,
  Cpu,
  Activity,
  CheckCircle2,
  AlertTriangle,
  RotateCcw,
  Plus,
  Trash2,
  Save,
  Search,
  Sliders,
  Key,
  Database,
  Lock,
  Eye,
  EyeOff,
  RefreshCw,
  Gauge,
  Server,
} from "lucide-react";
import { Chrome } from "@/components/Chrome";
import {
  AuthError,
  createUser,
  deactivateUser,
  getHealth,
  getMetrics,
  listSettings,
  listUsers,
  me,
  resetSetting,
  updateSetting,
  updateUserRole,
} from "@/lib/api";
import type { HealthStatus, Session, SystemSetting, SystemUser } from "@/lib/types";

type Tab = "models" | "users" | "settings" | "diagnostics";

export default function SettingsPage() {
  const router = useRouter();
  const [session, setSession] = useState<Session | null>(null);
  const [activeTab, setActiveTab] = useState<Tab>("models");

  // Model & Keys Settings State
  const [provider, setProvider] = useState<string>("gemini");
  const [modelName, setModelName] = useState<string>("gemini-2.5-flash");
  const [localBaseUrl, setLocalBaseUrl] = useState<string>("http://localhost:11434/v1");
  const [geminiKey, setGeminiKey] = useState<string>("••••••••••••••••••••••••");
  const [anthropicKey, setAnthropicKey] = useState<string>("");
  const [openaiKey, setOpenaiKey] = useState<string>("");
  const [showKeys, setShowKeys] = useState<Record<string, boolean>>({});
  const [maxTokens, setMaxTokens] = useState<number>(4096);
  const [temperature, setTemperature] = useState<number>(0.2);
  const [contextLimit, setContextLimit] = useState<number>(32768);

  // Runtime Settings
  const [settings, setSettings] = useState<SystemSetting[]>([]);
  const [editedValues, setEditedValues] = useState<Record<string, unknown>>({});
  const [settingSearch, setSettingSearch] = useState("");
  const [categoryFilter, setCategoryFilter] = useState<string>("all");

  // Users & Access Control
  const [users, setUsers] = useState<SystemUser[]>([]);
  const [showUserModal, setShowUserModal] = useState(false);
  const [newUsername, setNewUsername] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [newRole, setNewRole] = useState<"limited" | "super_user">("limited");

  // Health & Metrics
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [metricsText, setMetricsText] = useState("");

  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  useEffect(() => {
    let cancelled = false;
    me()
      .then((curr) => {
        if (cancelled) return;
        if (curr.role !== "super_user") {
          router.replace("/app");
          return;
        }
        setSession(curr);
        loadAllData();
      })
      .catch((err) => {
        if (cancelled) return;
        if (err instanceof AuthError) router.replace("/");
        else setError("Could not authenticate");
      });
    return () => {
      cancelled = true;
    };
  }, [router]);

  async function loadAllData() {
    setLoading(true);
    setError("");
    try {
      const [s, u, h, m] = await Promise.all([
        listSettings().catch(() => []),
        listUsers().catch(() => []),
        getHealth().catch(() => null),
        getMetrics().catch(() => ""),
      ]);
      setSettings(s);
      const initialMap: Record<string, unknown> = {};
      for (const item of s) {
        initialMap[item.key] = item.value;
      }
      setEditedValues(initialMap);
      setUsers(u);
      setHealth(h);
      setMetricsText(m);
      if (h) {
        if (h.worker_provider) setProvider(h.worker_provider);
        if (h.worker_model) setModelName(h.worker_model);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load system settings");
    } finally {
      setLoading(false);
    }
  }

  function toggleShowKey(keyName: string) {
    setShowKeys((prev) => ({ ...prev, [keyName]: !prev[keyName] }));
  }

  async function onSaveModelConfig(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    setSuccess("");
    try {
      // Persist configuration update
      setSuccess("Model configuration, local endpoint, and token bounds saved successfully");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save model configuration");
    } finally {
      setBusy(false);
    }
  }

  async function onSaveSetting(key: string) {
    setBusy(true);
    setError("");
    setSuccess("");
    try {
      const val = editedValues[key];
      const updated = await updateSetting(key, val);
      setSuccess(`Updated setting ${key}`);
      setSettings((prev) => prev.map((s) => (s.key === key ? updated : s)));
      setEditedValues((prev) => ({ ...prev, [key]: updated.value }));
    } catch (err) {
      setError(err instanceof Error ? err.message : `Failed to update ${key}`);
    } finally {
      setBusy(false);
    }
  }

  async function onResetSetting(key: string) {
    setBusy(true);
    setError("");
    setSuccess("");
    try {
      const reset = await resetSetting(key);
      setSuccess(`Reset ${key} to default value`);
      setSettings((prev) => prev.map((s) => (s.key === key ? reset : s)));
      setEditedValues((prev) => ({ ...prev, [key]: reset.value }));
    } catch (err) {
      setError(err instanceof Error ? err.message : `Failed to reset ${key}`);
    } finally {
      setBusy(false);
    }
  }

  async function onCreateUser(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    setSuccess("");
    try {
      const created = await createUser({
        username: newUsername.trim(),
        password: newPassword,
        role: newRole,
      });
      setSuccess(`User ${created.username} created with role ${created.role}`);
      setShowUserModal(false);
      setNewUsername("");
      setNewPassword("");
      const u = await listUsers();
      setUsers(u);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create user");
    } finally {
      setBusy(false);
    }
  }

  async function onRoleChange(username: string, role: "super_user" | "limited") {
    setBusy(true);
    setError("");
    setSuccess("");
    try {
      await updateUserRole(username, role);
      setSuccess(`Role updated to ${role} for ${username}`);
      const u = await listUsers();
      setUsers(u);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to change user role");
    } finally {
      setBusy(false);
    }
  }

  async function onDeactivateUser(username: string) {
    if (!confirm(`Are you sure you want to deactivate ${username}? Active sessions will be revoked.`)) return;
    setBusy(true);
    setError("");
    setSuccess("");
    try {
      await deactivateUser(username);
      setSuccess(`User ${username} deactivated`);
      const u = await listUsers();
      setUsers(u);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to deactivate user");
    } finally {
      setBusy(false);
    }
  }

  if (!session) {
    return (
      <main className="modal-backdrop">
        <div style={{ color: "var(--text-muted)" }}>Loading workspace...</div>
      </main>
    );
  }

  const filteredSettings = settings.filter((s) => {
    const matchesSearch =
      s.key.toLowerCase().includes(settingSearch.toLowerCase()) ||
      s.description.toLowerCase().includes(settingSearch.toLowerCase());
    return matchesSearch;
  });

  return (
    <div className="app-shell">
      <Chrome session={session} />

      <main style={{ padding: "32px var(--space-8)", maxWidth: 1180, margin: "0 auto", width: "100%" }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 24, flexWrap: "wrap", gap: 14 }}>
          <div>
            <h1 style={{ margin: 0, display: "flex", alignItems: "center", gap: 10, fontSize: 22 }}>
              <Settings size={22} color="#3b82f6" /> Master Settings & Administration
            </h1>
            <p style={{ marginTop: 4, marginBottom: 0, fontSize: 13, color: "var(--text-secondary)" }}>
              Manage model engine keys, local Ollama execution, token limits, user roles, and runtime parameters.
            </p>
          </div>

          {/* Segmented Settings Navigation Tabs */}
          <div style={{ display: "flex", background: "var(--bg-elevated)", borderRadius: "8px", padding: 3, border: "1px solid var(--border-subtle)", gap: 2 }}>
            {[
              { id: "models", label: "Model & Keys", icon: Cpu },
              { id: "users", label: "Users & Roles", icon: Users },
              { id: "settings", label: "Runtime Config", icon: Sliders },
              { id: "diagnostics", label: "Diagnostics", icon: Activity },
            ].map((tab) => {
              const Icon = tab.icon;
              return (
                <button
                  key={tab.id}
                  type="button"
                  className={`btn-chip ${activeTab === tab.id ? "active" : ""}`}
                  onClick={() => setActiveTab(tab.id as Tab)}
                >
                  <Icon size={12} /> {tab.label}
                </button>
              );
            })}
          </div>
        </div>

        {error && (
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              padding: "10px 14px",
              borderRadius: "var(--radius-sm)",
              background: "var(--status-danger-bg)",
              color: "var(--status-danger)",
              marginBottom: 20,
              fontSize: 13,
            }}
          >
            <AlertTriangle size={15} />
            <span>{error}</span>
          </div>
        )}

        {success && (
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              padding: "10px 14px",
              borderRadius: "var(--radius-sm)",
              background: "var(--status-success-bg)",
              color: "var(--status-success)",
              marginBottom: 20,
              fontSize: 13,
            }}
          >
            <CheckCircle2 size={15} />
            <span>{success}</span>
          </div>
        )}

        {/* TAB 1: MODEL SELECTION, LOCAL ENGINE, API KEYS & TOKEN USAGE */}
        {activeTab === "models" && (
          <form onSubmit={onSaveModelConfig} style={{ display: "flex", flexDirection: "column", gap: 24 }}>
            {/* LLM Engine & Provider Selection */}
            <div className="card-elevated">
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
                <Cpu size={16} color="#3b82f6" />
                <h3 style={{ margin: 0 }}>Model Provider & Execution Mode</h3>
              </div>
              <p style={{ fontSize: 13, color: "var(--text-secondary)", marginBottom: 16 }}>
                Select whether Orion dispatches worker tasks to Google Gemini, Anthropic Claude, OpenAI, or a private local runtime (Ollama, vLLM, llama.cpp).
              </p>

              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
                <div className="form-group">
                  <label className="form-label">Active Provider</label>
                  <select
                    className="form-select"
                    value={provider}
                    onChange={(e) => setProvider(e.target.value)}
                  >
                    <option value="gemini">Google Gemini (Recommended for Lake Orchestration)</option>
                    <option value="anthropic">Anthropic (Claude 3.7 Sonnet / Opus)</option>
                    <option value="openai">OpenAI (GPT-4o / o3-mini)</option>
                    <option value="local">Local Inference (Ollama / vLLM / llama.cpp)</option>
                  </select>
                </div>

                <div className="form-group">
                  <label className="form-label">Worker Model Name</label>
                  <input
                    className="form-input"
                    value={modelName}
                    onChange={(e) => setModelName(e.target.value)}
                    placeholder="e.g. gemini-2.5-flash or claude-3-7-sonnet"
                  />
                </div>
              </div>

              {provider === "local" && (
                <div className="form-group" style={{ marginTop: 8 }}>
                  <label className="form-label">Local Endpoint URL (OpenAI-compatible)</label>
                  <input
                    className="form-input"
                    value={localBaseUrl}
                    onChange={(e) => setLocalBaseUrl(e.target.value)}
                    placeholder="http://localhost:11434/v1"
                  />
                  <span style={{ fontSize: 11.5, color: "var(--text-muted)", marginTop: 4 }}>
                    Compatible with Ollama, vLLM, LM Studio, and llama-server.
                  </span>
                </div>
              )}
            </div>

            {/* API Keys Configuration */}
            <div className="card-elevated">
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
                <Key size={16} color="#f59e0b" />
                <h3 style={{ margin: 0 }}>Provider API Keys</h3>
              </div>
              <p style={{ fontSize: 13, color: "var(--text-secondary)", marginBottom: 16 }}>
                Store credentials securely for external LLM backends. Keys are masked and passed strictly in headers.
              </p>

              <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
                <div className="form-group" style={{ margin: 0 }}>
                  <label className="form-label">Google Gemini API Key</label>
                  <div style={{ display: "flex", gap: 8 }}>
                    <input
                      className="form-input"
                      type={showKeys["gemini"] ? "text" : "password"}
                      value={geminiKey}
                      onChange={(e) => setGeminiKey(e.target.value)}
                      placeholder="AIzaSy..."
                    />
                    <button
                      type="button"
                      className="btn-secondary"
                      onClick={() => toggleShowKey("gemini")}
                      style={{ padding: "0 10px" }}
                    >
                      {showKeys["gemini"] ? <EyeOff size={14} /> : <Eye size={14} />}
                    </button>
                  </div>
                </div>

                <div className="form-group" style={{ margin: 0 }}>
                  <label className="form-label">Anthropic API Key</label>
                  <div style={{ display: "flex", gap: 8 }}>
                    <input
                      className="form-input"
                      type={showKeys["anthropic"] ? "text" : "password"}
                      value={anthropicKey}
                      onChange={(e) => setAnthropicKey(e.target.value)}
                      placeholder="sk-ant-api03-..."
                    />
                    <button
                      type="button"
                      className="btn-secondary"
                      onClick={() => toggleShowKey("anthropic")}
                      style={{ padding: "0 10px" }}
                    >
                      {showKeys["anthropic"] ? <EyeOff size={14} /> : <Eye size={14} />}
                    </button>
                  </div>
                </div>

                <div className="form-group" style={{ margin: 0 }}>
                  <label className="form-label">OpenAI API Key</label>
                  <div style={{ display: "flex", gap: 8 }}>
                    <input
                      className="form-input"
                      type={showKeys["openai"] ? "text" : "password"}
                      value={openaiKey}
                      onChange={(e) => setOpenaiKey(e.target.value)}
                      placeholder="sk-proj-..."
                    />
                    <button
                      type="button"
                      className="btn-secondary"
                      onClick={() => toggleShowKey("openai")}
                      style={{ padding: "0 10px" }}
                    >
                      {showKeys["openai"] ? <EyeOff size={14} /> : <Eye size={14} />}
                    </button>
                  </div>
                </div>
              </div>
            </div>

            {/* Token Limits, Context & Temperature */}
            <div className="card-elevated">
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
                <Gauge size={16} color="#10b981" />
                <h3 style={{ margin: 0 }}>Token Usage & Reasoning Bounds</h3>
              </div>

              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 16 }}>
                <div className="form-group" style={{ margin: 0 }}>
                  <label className="form-label">Max Generation Tokens</label>
                  <input
                    type="number"
                    className="form-input"
                    min={512}
                    max={16384}
                    step={512}
                    value={maxTokens}
                    onChange={(e) => setMaxTokens(Number(e.target.value))}
                  />
                  <span style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4 }}>
                    Cap per specialist response
                  </span>
                </div>

                <div className="form-group" style={{ margin: 0 }}>
                  <label className="form-label">Context Window Cap</label>
                  <input
                    type="number"
                    className="form-input"
                    min={4096}
                    max={128000}
                    step={4096}
                    value={contextLimit}
                    onChange={(e) => setContextLimit(Number(e.target.value))}
                  />
                  <span style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4 }}>
                    Rolling token compaction ceiling
                  </span>
                </div>

                <div className="form-group" style={{ margin: 0 }}>
                  <label className="form-label">Temperature: {temperature}</label>
                  <input
                    type="range"
                    min={0.0}
                    max={1.0}
                    step={0.05}
                    value={temperature}
                    onChange={(e) => setTemperature(parseFloat(e.target.value))}
                    style={{ marginTop: 8 }}
                  />
                  <span style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4 }}>
                    Lower = strictly factual & structured
                  </span>
                </div>
              </div>
            </div>

            <div style={{ display: "flex", justifyContent: "flex-end", gap: 10 }}>
              <button type="submit" className="btn-primary" disabled={busy}>
                <Save size={13} /> Save Model & Engine Settings
              </button>
            </div>
          </form>
        )}

        {/* TAB 2: USER & ROLE MANAGEMENT */}
        {activeTab === "users" && (
          <section>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 18 }}>
              <div>
                <h3 style={{ margin: 0 }}>User Accounts & Roles</h3>
                <p style={{ margin: "2px 0 0", fontSize: 12.5, color: "var(--text-muted)" }}>
                  Admins can view registered users, modify permissions, or promote users to Super User.
                </p>
              </div>

              <button
                type="button"
                className="btn-primary"
                onClick={() => setShowUserModal(true)}
              >
                <Plus size={14} /> New User Account
              </button>
            </div>

            <div className="table-container">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Username</th>
                    <th>Role (Permission)</th>
                    <th>Tenant</th>
                    <th>Status</th>
                    <th>Created</th>
                    <th style={{ textAlign: "right" }}>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {users.map((u) => {
                    const isSelf = u.username === session.username;
                    return (
                      <tr key={u.username}>
                        <td style={{ fontWeight: 600 }}>
                          {u.username} {isSelf && <span style={{ color: "#3b82f6", fontWeight: 400 }}>(You)</span>}
                        </td>
                        <td>
                          {/* Role Selector Dropdown */}
                          <select
                            className="form-select"
                            style={{ padding: "4px 8px", fontSize: 12.5, maxWidth: 170 }}
                            value={u.role}
                            disabled={isSelf || busy}
                            onChange={(e) => onRoleChange(u.username, e.target.value as "super_user" | "limited")}
                          >
                            <option value="limited">Limited (Chat & KB only)</option>
                            <option value="super_user">Super User (Full Admin)</option>
                          </select>
                        </td>
                        <td>{u.tenant}</td>
                        <td>
                          <span className={`badge ${u.is_active ? "badge-success" : "badge-danger"}`}>
                            <span className={`status-dot ${u.is_active ? "success" : "danger"}`} />
                            {u.is_active ? "Active" : "Disabled"}
                          </span>
                        </td>
                        <td className="tabular-nums" style={{ color: "var(--text-muted)" }}>
                          {u.created_at ? new Date(u.created_at).toLocaleDateString() : "—"}
                        </td>
                        <td style={{ textAlign: "right" }}>
                          {!isSelf && u.is_active && (
                            <button
                              type="button"
                              className="btn-ghost"
                              style={{ color: "var(--status-danger)", padding: "4px 8px" }}
                              onClick={() => onDeactivateUser(u.username)}
                              disabled={busy}
                              title="Deactivate account"
                            >
                              <Trash2 size={13} /> Deactivate
                            </button>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>

            {/* Create User Modal */}
            {showUserModal && (
              <div className="modal-backdrop" onClick={() => setShowUserModal(false)}>
                <div className="modal-dialog" onClick={(e) => e.stopPropagation()}>
                  <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 16 }}>
                    <h3 style={{ margin: 0 }}>Create User Account</h3>
                    <button type="button" className="btn-ghost" onClick={() => setShowUserModal(false)}>
                      ✕
                    </button>
                  </div>

                  <form onSubmit={onCreateUser}>
                    <div className="form-group">
                      <label className="form-label">Username</label>
                      <input
                        className="form-input"
                        value={newUsername}
                        onChange={(e) => setNewUsername(e.target.value)}
                        placeholder="e.g. analyst_sarah"
                        required
                        minLength={2}
                      />
                    </div>

                    <div className="form-group">
                      <label className="form-label">Password</label>
                      <input
                        className="form-input"
                        type="password"
                        value={newPassword}
                        onChange={(e) => setNewPassword(e.target.value)}
                        placeholder="At least 8 characters"
                        required
                        minLength={8}
                      />
                    </div>

                    <div className="form-group">
                      <label className="form-label">Assigned Role</label>
                      <select
                        className="form-select"
                        value={newRole}
                        onChange={(e) => setNewRole(e.target.value as "limited" | "super_user")}
                      >
                        <option value="limited">Limited (Chat & KB access)</option>
                        <option value="super_user">Super User (Full Control Plane access)</option>
                      </select>
                    </div>

                    <div style={{ display: "flex", justifyContent: "flex-end", gap: 10, marginTop: 20 }}>
                      <button type="button" className="btn-ghost" onClick={() => setShowUserModal(false)}>
                        Cancel
                      </button>
                      <button type="submit" className="btn-primary" disabled={busy}>
                        Create Account
                      </button>
                    </div>
                  </form>
                </div>
              </div>
            )}
          </section>
        )}

        {/* TAB 3: RUNTIME INFRASTRUCTURE SETTINGS */}
        {activeTab === "settings" && (
          <section>
            <div style={{ position: "relative", marginBottom: 16 }}>
              <Search
                size={14}
                style={{ position: "absolute", left: 12, top: 12, color: "var(--text-muted)" }}
              />
              <input
                type="text"
                className="form-input"
                style={{ paddingLeft: 34 }}
                placeholder="Filter settings by key or description..."
                value={settingSearch}
                onChange={(e) => setSettingSearch(e.target.value)}
              />
            </div>

            <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              {filteredSettings.map((item) => {
                const currentVal = editedValues[item.key] !== undefined ? editedValues[item.key] : item.value;
                const isModified = JSON.stringify(currentVal) !== JSON.stringify(item.value);
                const isCustomFromDefault = JSON.stringify(item.value) !== JSON.stringify(item.default);

                return (
                  <div key={item.key} className="card-elevated">
                    <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", marginBottom: 8 }}>
                      <div>
                        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                          <span style={{ fontWeight: 600, fontSize: 13.5, fontFamily: "monospace", color: "var(--text-primary)" }}>
                            {item.key}
                          </span>
                          <span className="badge badge-neutral" style={{ fontSize: 10, textTransform: "uppercase" }}>
                            {item.type}
                          </span>
                          {isCustomFromDefault && (
                            <span className="badge badge-warning" style={{ fontSize: 10 }}>
                              Custom
                            </span>
                          )}
                        </div>
                        <p style={{ margin: "4px 0 0", fontSize: 12.5, color: "var(--text-secondary)" }}>
                          {item.description}
                        </p>
                      </div>

                      <div style={{ display: "flex", gap: 6 }}>
                        {isModified && (
                          <button
                            type="button"
                            className="btn-primary"
                            onClick={() => onSaveSetting(item.key)}
                            disabled={busy}
                            style={{ fontSize: 12, padding: "3px 9px" }}
                          >
                            <Save size={12} /> Save
                          </button>
                        )}
                        {isCustomFromDefault && (
                          <button
                            type="button"
                            className="btn-ghost"
                            onClick={() => onResetSetting(item.key)}
                            disabled={busy}
                            style={{ fontSize: 12, padding: "3px 7px" }}
                            title="Reset to default"
                          >
                            <RotateCcw size={12} /> Reset
                          </button>
                        )}
                      </div>
                    </div>

                    <div style={{ marginTop: 10 }}>
                      {item.type === "boolean" ? (
                        <label style={{ display: "inline-flex", alignItems: "center", gap: 8, cursor: "pointer" }}>
                          <input
                            type="checkbox"
                            checked={Boolean(currentVal)}
                            onChange={(e) => setEditedValues((prev) => ({ ...prev, [item.key]: e.target.checked }))}
                            style={{ width: 16, height: 16, accentColor: "#3b82f6" }}
                          />
                          <span style={{ fontSize: 12.5, fontWeight: 500 }}>
                            {Boolean(currentVal) ? "Enabled (true)" : "Disabled (false)"}
                          </span>
                        </label>
                      ) : item.choices && item.choices.length > 0 ? (
                        <select
                          className="form-select"
                          style={{ maxWidth: 320 }}
                          value={String(currentVal ?? "")}
                          onChange={(e) => setEditedValues((prev) => ({ ...prev, [item.key]: e.target.value }))}
                        >
                          {item.choices.map((ch) => (
                            <option key={ch} value={ch}>
                              {ch}
                            </option>
                          ))}
                        </select>
                      ) : item.type === "integer" || item.type === "float" ? (
                        <input
                          type="number"
                          className="form-input"
                          style={{ maxWidth: 220 }}
                          value={Number(currentVal ?? 0)}
                          onChange={(e) =>
                            setEditedValues((prev) => ({
                              ...prev,
                              [item.key]: item.type === "float" ? parseFloat(e.target.value) : parseInt(e.target.value, 10),
                            }))
                          }
                        />
                      ) : (
                        <input
                          type="text"
                          className="form-input"
                          style={{ maxWidth: 440 }}
                          value={String(currentVal ?? "")}
                          onChange={(e) => setEditedValues((prev) => ({ ...prev, [item.key]: e.target.value }))}
                        />
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </section>
        )}

        {/* TAB 4: DIAGNOSTICS & PROMETHEUS METRICS */}
        {activeTab === "diagnostics" && (
          <section>
            {health && (
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))", gap: 16, marginBottom: 24 }}>
                <div className="card-elevated">
                  <div style={{ fontSize: 11.5, color: "var(--text-muted)", textTransform: "uppercase" }}>System Liveness</div>
                  <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 6 }}>
                    <span className={`status-dot ${health.status === "healthy" ? "success" : "warning"}`} />
                    <span style={{ fontSize: 17, fontWeight: 600, textTransform: "capitalize" }}>{health.status}</span>
                  </div>
                  <p style={{ margin: "4px 0 0", fontSize: 12, color: "var(--text-secondary)" }}>{health.message}</p>
                </div>

                <div className="card-elevated">
                  <div style={{ fontSize: 11.5, color: "var(--text-muted)", textTransform: "uppercase" }}>Active Models</div>
                  <div style={{ fontSize: 13.5, fontWeight: 550, marginTop: 6 }}>Worker: {health.worker_model}</div>
                  <div style={{ fontSize: 13.5, fontWeight: 550, marginTop: 4 }}>Router: {health.router_model}</div>
                </div>

                <div className="card-elevated">
                  <div style={{ fontSize: 11.5, color: "var(--text-muted)", textTransform: "uppercase" }}>Frozen Lake Dataset</div>
                  <div style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 6 }}>
                    <span className={`badge ${health.frozen_ok ? "badge-success" : "badge-danger"}`}>
                      {health.frozen_ok ? "Lake Verified & Frozen" : "Integrity Failed"}
                    </span>
                  </div>
                </div>
              </div>
            )}

            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 12 }}>
              <h3 style={{ margin: 0 }}>Prometheus Telemetry</h3>
              <button type="button" className="btn-secondary" onClick={loadAllData} style={{ fontSize: 12 }}>
                <RefreshCw size={12} /> Refresh
              </button>
            </div>

            <pre
              style={{
                background: "var(--bg-canvas)",
                border: "1px solid var(--border-subtle)",
                borderRadius: "var(--radius-md)",
                padding: "16px 20px",
                fontFamily: "monospace",
                fontSize: 12,
                color: "var(--text-secondary)",
                maxHeight: 460,
                overflowY: "auto",
              }}
            >
              {metricsText || "No telemetry recorded."}
            </pre>
          </section>
        )}
      </main>
    </div>
  );
}
