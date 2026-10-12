# OrionHarness

Typed self-healing multi-agent RAG over a **heterogeneous** HR / filings / calendar lake. Same datasets as before; the execution stack is new.

Builtin workers: **hr**, **finance**, **scheduler**, **chart**, **sql**. The Lab **synthesizes new agents** (it does not mix the old ones). Evaluation is **oracle-first** (`python -m bench.compile_oracles`).

See [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md), [DATA_CARD.md](DATA_CARD.md), and [REVAMP_AND_PAPER_PLAN.md](REVAMP_AND_PAPER_PLAN.md).

## Prerequisites

- Python 3.10+
- `MISTRAL_API_KEY` in `.env` (workers and router). Optional: `ORION_JUDGE_*` for a different-family judge.
- Local models: `ORION_WORKER_PROVIDER=openai_compat` + `ORION_WORKER_BASE_URL` (Ollama `http://127.0.0.1:11434/v1`, LM Studio `:1234/v1`, vLLM `:8000/v1`). Copy [`.env.example`](.env.example). Then `python -m harness.check --ping`.
- Context packing (optional): `ORION_CONTEXT_WINDOW`, `ORION_COMPACT_RATIO`, `ORION_CONTEXT_TAIL`, `ORION_TOOL_PREVIEW_CHARS`.
- Procedural skills (optional): `ORION_EVAL_EVERY` (self-eval pause), `ORION_SKILL_TOPK`. Saved under `outputs/skills/`. Curate with `python -m harness.skills --curate`.

## Quick start

```powershell
.\venv\Scripts\Activate.ps1
pip install -r setup/requirements.txt
```

`.env`:

```
MISTRAL_API_KEY=...
```

Freeze queryable tables (does not rewrite `data/` sources):

```
python -m retrieval.freeze
```

Rebuild chroma only if the index is missing or loaders changed:

```
python build_vector_db.py
```

**Terminal 1**

```
uvicorn backend.api:app --port 8000
```

**Terminal 2** (product UI)

```
cd web
npm install
npm run dev
```

Open http://localhost:3000. Sign in as `admin` / `ORION_ADMIN_PASSWORD` (dev default `admin`). Chat, upload, and Lab talk to the API on `:8000`. They write live `kb/` only, never `data/frozen/` or `bench/`.

Optional Gradio fallback: `python frontend/app.py` at http://localhost:7860.

If `ORION_API_KEY` is set, login sessions are valid bearers. Set `NEXT_PUBLIC_API_URL` if the API is not at `http://127.0.0.1:8000`.

CLI: `python main.py`

Doctor: `python -m harness.check` (add `--ping` to hit the configured LLM).

## Local models

The control plane does not change. Set provider + base URL + model ids:

```
ORION_WORKER_PROVIDER=openai_compat
ORION_WORKER_BASE_URL=http://127.0.0.1:11434/v1
ORION_WORKER_MODEL=llama3.1
ORION_ROUTER_MODEL=llama3.1
ORION_JUDGE_MODEL=llama3.1
ORION_CONTEXT_WINDOW=8192
```

Small local models often omit OpenAI `tool_calls`; the gateway also parses JSON / Hermes `<tool_call>` in the text. Paper runs should keep a **different judge family** (`ORION_JUDGE_PROVIDER` / `ORION_JUDGE_MODEL`).

## Evaluation (no annotators)

```
python -m bench.compile_oracles
python -m bench.run --split dev --heal
python -m unittest tests.test_harness
```

Held-out `--split test` was scored once (`outputs/bench_test_heal.json`). Do not score it again.

## Layout

- `harness/` — gateway, router, planner, healing, traces, tool/agent registries, context packer + session vault
- `sandbox/` — spec-only SQL + AST-gated generated code; isolated judge
- `workers/` — shared ReAct loop
- `lab/` — synthesizer → `lab/generated/`
- `repair/` — dev-only patches, never `bench/`
- `bench/` — compiled oracles and `run.py`
- `retrieval/` — embeddings, chroma, frozen tables
- `web/` — Next.js control plane (sign in, chat, Lab)
- `frontend/` — Gradio fallback
- `data/` — pinned corpus

## License

MIT
