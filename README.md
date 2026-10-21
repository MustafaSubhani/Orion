# OrionHarness

An open-source, local, plugin-based **harness for enterprise data**: typed multi-agent planning, self-healing, memory, a Lab that creates and tests new agents, MCP in both directions, guardrails, and an OpenAI-compatible API. It runs on your machine against your data. Nothing is sent anywhere you did not configure.

The bundled domain pack (`packs/enterprise_lake`) is the HR / filings / calendar lake the research results were measured on. Your own data goes in through connectors and your own domain through a pack. See [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md), [DATA_CARD.md](DATA_CARD.md) and [REVAMP_AND_PAPER_PLAN.md](REVAMP_AND_PAPER_PLAN.md).

## What you get

| Area | What it does |
| --- | --- |
| Planner | Routes a request to agents, runs independent ones in parallel waves, enforces token / time budgets, cancels cleanly |
| Self-healing | Eight declared failure classes (F1–F8) with a fixed precedence; each has one deterministic recovery action |
| Memory | Conversation threads with a rolling, fact-checked summary; per-user skills with a background curator |
| Lab | Describe an agent in words; it is synthesized as a card, contract-tested against your live schema, scored, and only then promoted |
| MCP | Serve the harness to other tools (`orion mcp`), and bridge external MCP servers in as tools |
| Guardrails | Optional Hugging Face prompt-injection classifier on input and untrusted tool output, plus a write-approval gate |
| Web search | OpenAI hosted search when the provider is OpenAI, otherwise DuckDuckGo or an MCP search server |
| Data connectors | Read-only SQL (SQLAlchemy) and file sources, with live schema introspection and role masking |
| Control plane | Auth, roles, tenants, admin settings, approvals, traces, metrics |

Every runtime behaviour above is a **setting** an admin can change without a restart. See [Settings](#settings).

## Install

```
py -3 -m venv venv
.\venv\Scripts\Activate.ps1
pip install -e ".[api]"
```

Extras: `api` (FastAPI server), `guard` (Hugging Face classifier), `mcp`, `search` (DuckDuckGo), `rag` (embeddings and chroma), `sandbox` (Windows job objects), `dev` (tests, lint), `all`.

## Run

```
orion serve                  # HTTP API on 127.0.0.1:8000
orion mcp                    # MCP server over stdio
orion check                  # doctor: frozen data, models, config
orion settings list          # every setting, its value and where it comes from
orion settings set guard.enabled true
orion users create dana --role super_user --password-env DANA_PW
orion connectors list
orion connectors test <id>
```

Passwords can only be supplied by prompt or an environment variable, never as an argument. `orion serve` binds to loopback by default.

`.env` (copy [`.env.example`](.env.example)):

```
MISTRAL_API_KEY=...        # or OPENAI_API_KEY with ORION_WORKER_PROVIDER=openai
ORION_ADMIN_PASSWORD=...   # required when ORION_ENV=production
```

Sign in as `admin` with `ORION_ADMIN_PASSWORD` (development default `admin`). Authentication is required unless `ORION_AUTH_REQUIRED=false`.

### API surface

- `POST /v1/chat/completions` — OpenAI-compatible (streaming included), usable by OpenUI and any OpenAI client
- `POST /chat`, `POST /chat/stream`, `/threads`, `/traces/{id}` — native chat, memory and traces
- `/agents` (status, versions, rollback), `/lab/*` (create, evaluate, deploy) — agents and the Lab
- `POST /ingest`, `/kb` — knowledge base uploads
- `GET/POST /approvals` — pending write approvals
- `GET/PUT/DELETE /admin/settings`, `/admin/users`, `/admin/connectors`, `/admin/mcp`, `/admin/quality/queue` — admin (super user only)
- `POST /mcp` — MCP over streamable HTTP
- `GET /health`, `GET /metrics` (Prometheus text)

Every error uses one envelope: `{"error": {"code", "message", "request_id"}}`.

## Models

Hosted or local; the control plane does not change. Providers: `mistral`, `openai`, `openai_compat` (Ollama `:11434/v1`, LM Studio `:1234/v1`, vLLM `:8000/v1`).

```
ORION_WORKER_PROVIDER=openai_compat
ORION_WORKER_BASE_URL=http://127.0.0.1:11434/v1
ORION_WORKER_MODEL=llama3.1
ORION_ROUTER_MODEL=llama3.1
ORION_JUDGE_MODEL=llama3.1
ORION_CONTEXT_WINDOW=8192
```

Small local models often omit OpenAI `tool_calls`; the gateway also parses JSON and Hermes `<tool_call>` in the text. Research runs should keep a **different judge family** (`ORION_JUDGE_PROVIDER` / `ORION_JUDGE_MODEL`).

## Settings

Resolution order: admin override in the state database, then environment `ORION_<KEY>` (dots become underscores), then default. All are tenant-aware and every change is audited.

| Key | Default | Meaning |
| --- | --- | --- |
| `guard.enabled` | `false` | Hugging Face prompt-injection classifier on live chat |
| `guard.model_id` | `protectai/deberta-v3-base-prompt-injection-v2` | Any text-classification model |
| `guard.threshold` / `guard.fail_mode` | `0.9` / `open` | Block score; whether a missing model blocks (`closed`) or continues |
| `guard.scan_tool_outputs` | `true` | Also scan web, MCP, KB and fetched content |
| `web_search.enabled` / `.backend` | `false` / `auto` | `auto` uses OpenAI hosted search on the OpenAI provider, else DuckDuckGo; or `mcp:<server>` |
| `web_search.allowed_domains` | empty | Restrict results to these domains |
| `mcp.enabled` | `true` | Connect servers from `config/mcp_servers.json` |
| `tools.write_policy` | `confirm` | `deny`, `confirm` (needs an approval) or `allow` for external write / egress tools |
| `memory.enabled`, `memory.summary_*` | on | Threads, rolling summary budget |
| `context.summarize` | `abstractive` | `off`, `extractive`, `abstractive` (always fact-checked, falls back to extractive) |
| `parallel.max_workers` | `4` | Concurrent agents per wave |
| `quality.sample_rate` | `0.0` | Fraction of live answers judged in the background |
| `lab.auto_eval` / `lab.min_score` | `true` / `0.8` | Promotion gate for new agents |
| `sandbox.*` | 512 MB, 10 s CPU, 30 s wall | Limits for generated code |
| `auth.open_signup` | `false` | Allow self-service limited accounts |

Trace hygiene: `ORION_TRACE_SCRUB` (default on; masks emails, phones and key-like tokens **on disk** only), `ORION_TRACE_MAX_MB` (audit log rotation, three backups), `ORION_TRACE_RETENTION_DAYS` (default 30; pruned at startup).

## Extending

- **Domain pack** — an entry point in the `orion.packs` group returning a `Pack` (agents, tools, policy, vocabularies). The bundled one is the reference; a parity test pins it.
- **Connector** — add an entry to `config/connectors.json` (see `config/connectors.example.json`). SQL sources are read-only and introspected live.
- **MCP server** — add an entry to `config/mcp_servers.json` (see the example), stdio or HTTP.
- **Agent** — describe it in the Lab, or register an `AgentCard`. The router needs no change.
- **Tool** — a `ToolManifest` plus handler registered through `harness/tool_registry.py`.

## Evaluation (research harness)

The benchmark path (`kb_enabled=False`) is deliberately unchanged by the product features and is guarded by parity tests.

```
python -m bench.run --split dev --heal
python -m unittest discover -s tests
```

Held-out `--split test` was scored once. Do not score it again. `paper/`, `bench/` and `data/frozen/` are not modified by product work.

## Layout

- `harness/` — gateway, router, planner, healing, faults, traces, settings, auth, guard, MCP, memory, registries, CLI
- `backend/` — FastAPI app, routes, service layer, auth database
- `packs/` — domain packs (`enterprise_lake`)
- `sandbox/` — spec-only SQL, AST-gated generated code, resource limits
- `workers/` — the generic ReAct worker
- `lab/` — synthesizer, contract tests, evaluation
- `repair/` — guarded repair with rollback
- `kb/` — live product knowledge store (uploads)
- `retrieval/` — embeddings, chroma, frozen tables
- `bench/` — oracles and runner (research)
- `tests/` — unit and integration tests
- `web/`, `frontend/` — UI (the Next.js UI is being rebuilt on this API)

## License

MIT
