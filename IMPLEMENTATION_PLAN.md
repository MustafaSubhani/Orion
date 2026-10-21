"""OrionHarness implementation spec. Research framing lives in REVAMP_AND_PAPER_PLAN.md."""

## Layout

```
harness/          control plane (gateway, router, planner, healing, traces, permissions, registries)
                  context_engine.py packs prompts to the model window; session.py is the artifact vault
sandbox/          exec_sandbox (sql_spec + AST gate) and eval_sandbox (oracles + judge)
workers/          generic ReAct worker; builtins are AgentCards, not copy-pasted HTTP clients
retrieval/        embeddings, chroma, frozen tables, freeze.py
lab/              synthesizer writes lab/generated/<id>/{card.json,agent.py}
repair/           patch allow-listed worker files on dev only
bench/            compile_oracles, graders, injectors, run.py
kb/               live product store (uploads). Never written by freeze or bench.
backend/api.py    FastAPI (`/auth`, `/chat`, `/ingest`, `/kb`, `/lab`, `/mcp`; chat sets `kb_enabled=True`)
web/              Next.js control plane (sign in, chat, Lab)
frontend/app.py   Gradio fallback
data/             frozen corpus (untouched except data/frozen/ materializations)
```

## Plug-and-play

- New tool: `ToolManifest` + handler in `harness/tool_registry.py` or `harness/tool_extras.py`. No worker edits.
- New agent: Lab `customize` or a hand-written `AgentCard` + `register()`. Router code does not change.
- New model: `ORION_WORKER_MODEL` / `ORION_WORKER_PROVIDER` / `ORION_JUDGE_*`. Workers never open HTTP clients.

## Commands

```
python -m retrieval.freeze
python -m bench.compile_oracles
python -m bench.run --split dev --heal
python -m bench.run --split dev --heal --inject F4 --limit 5
uvicorn backend.api:app --port 8000
cd web && npm run dev
python frontend/app.py
python -m harness.check
python -m harness.check --ping
python -m bench.run --split dev --heal --passk 3 --limit 20
```

## Isolation rules

- Generated `agent.py` is AST-gated and runs in a subprocess with secrets stripped.
- Repair Agent cannot open `bench/` or `harness/`.
- Judge sees traces + evidence, not worker API keys or test gold.
- `eval()` of free pandas is gone; numeric queries use `sql_spec`.

## Context packing (Cursor-style)

The gateway never sends the raw ReAct transcript. Before each HTTP call, `pack_for_model`:

1. Infers the **model window** (`ORION_CONTEXT_WINDOW` or a hint from the model id).
2. Reserves output tokens, then takes `min(window × ORION_COMPACT_RATIO, remaining ORION_MAX_TOKENS)`.
3. Stubs large tool payloads into the per-request **artifact vault**; the prompt keeps a preview + `artifact_id`.
4. Folds middle turns into one extractive compact note (system + live tail stay).
5. Drops oldest non-system turns, then slims tool schemas (`search_tools` / `session_get` stay full).

Workers call `session_get` to expand one artifact. In the benchmark path compaction stays extractive (no extra LLM call). In product mode `context.summarize=abstractive` summarizes the folded middle with the summarizer tier and **fact-checks** the result (every number, id and date must appear in the source), falling back to the extractive note. Traces record `compacted`, `artifacts_stubbed`, `middle_folded`.

Env: `ORION_CONTEXT_WINDOW` (0 = infer), `ORION_COMPACT_RATIO` (default 0.75), `ORION_CONTEXT_TAIL` (8), `ORION_TOOL_PREVIEW_CHARS` (1200).

## Procedural memory (Hermes-style loop, not RL)

Typed F1–F8 healing still owns recovery *class*. Skills are **repeatable procedures** written after a fail→success on the same tool:

1. **Error detection** — ReAct keeps the error in the transcript (bounded by `ORION_MAX_TOOL_ROUNDS`). F6/F7 still do not retry.
2. **Self-eval** — every `ORION_EVAL_EVERY` (default 4) tool calls, the harness injects an extractive pause note (no extra LLM).
3. **Skill evolution** — `skill_manage` create, or automatic `remember_recovery` when the same verb fails then succeeds. Files live in `outputs/skills/<slug>/SKILL.md`. Cannot mention `bench/` or secrets.
4. **Future adaptation** — next request selects top `ORION_SKILL_TOPK` skills into the system prompt.
5. **Curator** — merge overlapping skills, archive extras past 40. `python -m harness.skills --curate`.

This is procedural memory, not self-healing hardware and not RL. Repair Agent still patches generated `agent.py` only.

## Tools (fat registry, small verb set)

Workers do not grow a new LLM function per API. New capabilities are registry rows. Always-on infra: `search_tools` (pass a verb to load its JSON schema), `session_get`, `skill_manage`, `schema_lookup`, `join_check`, `evidence_check`.

Lake tools:

| Verb | Why |
|---|---|
| `schema_lookup` | real columns / samples before `sql_spec` (F4) |
| `distinct_values` / `describe_column` / `row_lookup` | lists, stats, keyed rows without a full scan |
| `join_check` | illegal entity mix → abstain |
| `evidence_check` | number appears in this request's tool artifacts |
| `calendar_query` | `match_count` + `count_only` + attendee / event_id |
| `calendar_conflicts` | overlapping events for organizer or date |
| `compare_series` | two numeric columns, optional DATE align |
| `workforce_slice` / `ratio_agg` / `top_n` | HR departments (IT, Sales, Marketing, …) on the MNC table |
| `macro_latest` / `series_window` | FRED / ApplFin dated series |
| `product_search` | frozen AAPL product catalog |
| `http_fetch` | allow-listed SEC / FRED / Apple / Microsoft only |

There is **no** sales-CRM / marketing-campaign / IT-ticket lake. Those words are HR `Department` values (plus Apple `products` for catalog questions). Do not add fake tables. Lab can still synthesize a specialist card over the same tools.

No open-web search. Lab `tools=all` can grant the full legal set.

## Execution DAG + backtracking

The planner builds a **run graph**, not a knowledge graph:

- One node per routed agent. Pipeline language (`then`, `based on that`, …) becomes a chain; otherwise nodes are a parallel ready-set.
- Success writes a **checkpoint** (agent, result hash-equivalent text).
- A failed child with parents is **rewound**: descendants reset, parent checkpoints stay, evidence is rebound.
- F5 `REPLAN` rewrites the unfinished tail from current checkpoints. F8 blocked children (parent failed) never run.
- Heal hints (`SCHEMA_REPAIR`, `RE_RETRIEVE`) re-bind parent checkpoints so the child does not forget upstream facts.
- Exhausted heal with a leftover error class is **FAILED** (so rewind can fire), not COMPLETED just because the worker printed text.
- Inner `REWIND` only rebinds evidence; the outer planner owns the rewind counter (max 2).
- `python -m bench.run --split dev --heal --inject F4` one-shots a schema fault for RSR. `--judge` adds judge-on-trace. `--no-heal` is the ablation. Summary includes `cascade_radius_max`.

## Phases (status)

0. Freeze tables + entity tags on RAG chunks — **done** (`data/frozen/` + `MANIFEST.json`)
1. Shared gateway / tools / traces
2. Oracle compiler (no annotators) — **done** (`bench/tasks/SPLIT_LOCK.json`)
3. Dual sandbox
4. Typed healing + Lab synthesizer + Repair Agent
5. `python -m bench.run` for paper tables — **done** (test scored once; inject table refreshed on current tools).
6. Product control plane — **done** (`/chat`, `/ingest`, `/kb`, `/mcp`, `/traces`; Gradio sends Bearer + role; chat uses live KB, bench does not).

**Locked scorecard (do not score `--split test` again)**

| Cell | n | TSR / rate | File |
|---|---|---|---|
| Test one-shot (Nemo heal) | 69 | **68.1%** | `bench_test_heal.json` |
| Qwen 7B val (current tools) | 34 | **82.4%** | `bench_val_heal_qwen.json` |
| Nemo val templates | 34 | 34/34 (compiler=tools; not a headline) | `bench_val_heal_general.json` |

Inject on current tools, Nemo, first 8 dev tasks (`*_injectF*_tools.json`):

| Class | FDA | RSR |
|---|---|---|
| F1 tool | 12.5% | **100%** |
| F2 retrieval | 0% | **100%** |
| F3 routing | 37.5% | **62.5%** |
| F4 schema | 12.5% | **100%** |
| F5 decompose | 0% | **100%** |
| F6 semantic | 0% | **87.5%** |
| F7 policy | **87.5%** | **87.5%** |
| F8 cascade | **87.5%** | **100%** |

Recovery is strong; detection is weak except F7/F8. F3 is the hard class. Qwen transfer and the one-shot test are the generalization numbers.

See [REVAMP_AND_PAPER_PLAN.md](REVAMP_AND_PAPER_PLAN.md) for the publication argument.

## Two stores (paper vs product)

Same gateway, tools, and healing. Two catalogs:

| Store | Path | Who writes | Who reads |
|---|---|---|---|
| Paper lake | `data/frozen/` + chroma `hr_data` / `finance_data` / `scheduler_data` | `python -m retrieval.freeze` only | bench + workers via `retrieve` / `sql_spec` |
| Live KB | `kb/` + chroma `kb_live` (optional) | `POST /ingest` (chat attach) | chat workers via `kb_list` / `kb_search` / `kb_query` |

Chat uploads never overwrite `data/frozen/` or `bench/`. `python -m bench.run` passes `kb_enabled=False`. `sql_spec` rejects `kb_*` tables (`use kb_query`). Lexical `kb_search` works if embeddings/chroma are missing. Max upload 15MB. crawl4ai is not in this step.

## Platform layer (product mode)

Everything below is gated by is_product(ctx) (live chat sets kb_enabled=True). The benchmark path never calls it, and 	ests/registry_snapshot.py plus the sanity benches pin that.

| Concern | Module | Contract |
|---|---|---|
| Settings | harness/settings_store.py | DB admin override, then ORION_<KEY>, then default; typed, bounded, tenant-aware, audited. One SCHEMA dict is the single source of truth. |
| State | harness/statedb.py | One SQLite file (ORION_STATE_DB) for settings, approvals, threads, quality queue. Atomic file helpers in harness/fsutil.py retry Windows os.replace collisions. |
| Auth | harness/auth.py, ackend/database.py | scrypt hashes, hashed expiring sessions, rate-limited login, auth required by default. |
| API | ackend/api.py, ackend/routes/* | create_app() + lifespan; one error envelope; /v1/chat/completions is OpenAI-compatible and streams. |
| Concurrency | harness/context.py, planner.py | RequestContext.fork() per node: own cancel token, shared budget/session/fault state behind locks. Waves run in a thread pool with node timeouts and crash isolation. |
| Healing | harness/healing.py, harness/faults.py | Resolver applies the declared precedence F7>F8>F4>F1>F2>F5>F3>F6. Fault injection lives only in aults.py. |
| Packs | harness/packs.py, packs/enterprise_lake/ | Everything lake-specific (entities, joins, tools, agent cards, policy) is a pack. A parity test freezes the registry the paper measured. |
| Connectors | harness/connectors/ | Read-only SQLAlchemy and file sources, live schema introspection feeding F4, role masking, symmetric join policy. |
| Guard | harness/guard.py | Windowed HF classifier on input and untrusted tool output; fail open or closed; guard.enabled toggles it. |
| Web search | harness/web_search.py | openai hosted search on the OpenAI provider, ddg, or mcp:<server>; allowed-domain filter; results pass through the guard. |
| MCP | harness/mcp_server.py, mcp_client.py | Server speaks 2025-06-18 over stdio and HTTP. Client bridges external servers on a background asyncio loop and exposes them as tools. |
| Writes | harness/approvals.py, 	ool_registry._write_gate | External WRITE and egress verbs follow 	ools.write_policy; confirm creates a single-use grant. Internal memory verbs are exempt (INTERNAL_WRITE_VERBS). |
| Memory | 	hreads.py, summarizer.py, skills.py | Threads with rolling fact-checked summary; skills are scoped per user/tenant, batched, semantically selected, curated in the background. |
| Lab | lab/, harness/agent_registry.py, harness/quality.py | Card-only agents run the generic worker. Contract tests are built from the live schema; promotion needs lab.min_score; repair restores via 
egistry.update on regression. |
| Sandbox | sandbox/policy.py, limits.py | One policy for the AST gate; Job Object (Windows) / rlimits (POSIX); minimal env; sockets cut. |
| Traces | harness/trace.py | Disk copy is PII-scrubbed (in-memory spans are not, so judges and the bench see raw), audit log rotation, retention pruning, pluggable exporters. |
| CLI / packaging | harness/cli.py, pyproject.toml | orion serve|mcp|check|settings|users|connectors|version; extras per feature; orion.packs entry point group. ench, paper, data are not packaged. |

Known limits (by design, documented rather than hidden):

- The write gate covers external WRITE and egress verbs only.
- F8 rewind remains in the planner outer loop, not the resolver.
- Six frozen-table tools stay in core because the paper's oracles depend on them.
- Lab evaluation uses the configured judge; a different-family judge is available but not required.
