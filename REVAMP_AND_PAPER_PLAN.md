# Orion Revamp and Publication Plan

**Status:** research diagnosis and architecture proposal (no code changes yet)  
**Constraint:** keep the same datasets; rebuild the *harness*, not the corpus  
**Eval constraint:** no hired annotators and no pre-existing labeled QA set. Do **not** depend on crowdsourcing. Compile gold from the tables; keep LLM-as-Judge only as a secondary scorer.  
**Goal:** a clean, optimized, publication-grade enterprise agent stack, then a paper that can actually get through review

This document is the diagnosis of the current system, the research gap it can fill, and a concrete plan for a full harness-engineering revamp.

---

## 1. What Orion is today

Orion is a **multi-agent RAG workplace assistant** over three enterprise domains:

| Domain | Data you already have | Agent |
|---|---|---|
| HR | ~2M-row MNC employee CSV, HR analytics xlsx, business-conduct PDF | `hr_agent` |
| Finance | SEC EDGAR filings (MSFT 10-K/10-Q/8-K), Apple financial CSVs, FRED macros (CPI, Fed Funds, UMCSENT), 100k Apple products CSV | `finance_agent` |
| Scheduler | 500k synthetic Apple calendar events + ICS calendar | `scheduler_agent` |
| Cross-cutting | Chart generation, NL→pandas | `ChartAgent`, `SQLAgent` |
| Meta | Custom-agent builder + simulation | `lab_agent`, `backend/simulation.py` |

A central **orchestrator** (`backend/orchestrator.py`) asks Mistral to decompose a query into tasks, routes each task to an agent, then synthesizes a final answer. There is a FastAPI backend, Gradio UI, ChromaDB retrieval, regex security filters, JSONL audit logs, and a large `benchmark_validation.py` that scores the system with **LLM-as-Judge**.

That is a reasonable student/product prototype. It is **not** yet a paper-grade agent harness.

---

## 2. The real problem (why this would not survive review)

The current code conflates three things reviewers now insist on separating:

1. **Model capability** (Mistral Small / Nemo / Large)
2. **Harness quality** (routing, tools, memory, recovery, permissions, traces)
3. **Evaluation quality** (oracle vs judge, isolation, contamination, sample size)

ACL 2026’s survey on LLM-agent evaluation states this directly: most benchmarks still **conflate the backbone LLM with the agent harness**, and future work must vary them independently. Harness-Bench (May 2026) and HarnessOpt-Bench (Aug 2026) exist because the field has decided “the model is not the system.”

Orion currently reports system scores as if they were model scores, then labels prompt rewriting as “RL.” That is the fastest path to a reject.

### 2.1 Architecture issues

| Issue | Where | Why it matters |
|---|---|---|
| Copy-pasted LLM clients | every agent has its own `call_mistral()` | no shared budget, tracing, retries, or model-harness ablations |
| Keyword tool selection | `finance_agent.detect_tool_need`, SQL keyword scoring | not ReAct / structured function calling; unmeasurable tool-use accuracy |
| Sequential, non-recovering orchestration | `execute_task` → on exception mark `FAILED` and continue | no typed recovery; one bad sub-agent poisons synthesis |
| Fake “sandbox” | `SQLAgent.execute_pandas_query` uses `eval(code, {"__builtins__": {}}, {"df": df, "pd": pd})` | pandas can still do I/O; this is not isolation |
| Fake “RL” | `test_rl_policy_improvement_builtin` swaps a short prompt for a longer prompt | reviewers will treat this as prompt engineering, not RL |
| Fake “co-training” | `test_cross_agent_cotraining` | already **failed** in `benchmark_results.json` (0.0 vs 15.0 target) |
| Stale/wrong schemas | SQL agent comments “600 employees”; HR loader samples 5k of 2M; finance SQL loads `data/EXCEL` while RAG uses SEC/FRED/ApplFin | agents and eval can be answering different worlds |
| Embedding comment vs code | `embeddings.py` claims `all-mpnet-base-v2`, loads `all-MiniLM-L6-v2` | retrieval quality is overstated |
| Chunking is character windows | `finance_loader._chunk_text` | comments say “semantic chunking”; it is not |
| README vs code | README says Gemini; code is Mistral | reproducibility failure |
| CORS `allow_origins=["*"]` | `backend/api.py` | not a paper issue, but it shows the security story is surface-level |

### 2.2 Evaluation issues (this is the paper-killer)

Current eval in `benchmark_validation.py` and `backend/simulation.py`:

- **LLM-as-Judge on the same model family** (Mistral Large judging Mistral Nemo/Small). Circular and biased.
- **Simulation pass/fail is keyword matching**: if the answer contains `"error"`, `"cannot"`, `"unable"` it fails. A correct “cannot find that employee” is scored as failure. A fluent hallucination is scored as success.
- **n is tiny**: `MAX_SIMULATION_TEST_CASES = 2`. Routing test has 6 items. Not statistically reportable.
- **Ground truths are hardcoded and likely stale** (e.g. “7 departments: HR, Marketing, …” vs the 2M-row MNC CSV).
- **No held-out split.** The same queries can leak into prompts, labs, and judges.
- **No oracle graders** for numeric HR/finance/calendar facts that *are* deterministically checkable on your CSVs.
- **No process metrics**: routing accuracy is tested, but tool-choice, citation grounding, cascade radius, recovery-by-failure-mode are not.
- **Cost is not a first-class metric** (ACL 2026 flags this as an open gap; Kapoor et al. 2024 “AI Agents That Matter”).
- **Judge and worker share the same process, files, and API keys.** There is no trusted evaluation boundary.

### 2.3 Self-healing / sandboxing: claimed vs real

What exists today:

- HTTP 429 retries
- JSON parse retries
- regex injection blacklist
- PII redaction on output
- audit JSONL
- Lab Agent that appends text to a prompt after a 2-case sim

What does **not** exist:

- failure taxonomy
- typed recovery (retry ≠ replan ≠ re-retrieve ≠ re-route ≠ sandbox re-exec)
- isolated code execution
- isolated evaluation agent
- trajectory recorder that a judge can audit without seeing worker secrets
- permissioned tool gateway
- budget metering per request

OrchestraBench (Aug 2026) showed the result you must not ignore: **blind retry recovers tool faults, partially recovers ambiguous delegation, and never recovers latent/semantic faults.** If Orion’s “self-healing” is just retry, the paper has no contribution.

---

## 3. Related work (what already exists)

You cannot publish “we built a multi-agent HR/finance chatbot.” That is 2023. The 2025–2026 literature has already moved to **harness, recovery, and evaluation protocol**.

### 3.1 Foundational agent patterns

- **ReAct** (Yao et al., 2023) — reason + act loop.
- **Reflexion** (Shinn et al., NeurIPS 2023) — verbal RL via episodic reflection; *not* weight updates.
- **Self-Refine** (Madaan et al., NeurIPS 2023) — iterative self-feedback.
- **AutoGen / MetaGPT / CAMEL** — multi-agent scaffolds.
- **Agent-as-a-Judge** (Zhuge et al., ICML 2025) — another agent evaluates trajectories, not just final text.

### 3.2 Enterprise / workplace agents (closest domain cousins)

- **TheAgentCompany** (Xu et al., 2024) — 175 long-horizon workplace tasks; SOTA ~30% full completion. Simulated company, not RAG over *your* tabular/SEC corpus.
- **τ-bench / τ²-bench** (Yao et al. 2024; Barres et al. 2025) — tool-agent-user, policy adherence, pass^k reliability.
- **WorkArena / WorkArena++** — enterprise *web* workflows, not internal RAG+SQL.
- **OdysseyBench** (Wang et al., 2025) — long-horizon office app workflows.
- **LH-Bench / “Beyond Binary Correctness”** (2026) — subjective enterprise tasks, expert rubrics, test-time verification; recovery depended on **error-message quality**, not retry count.

### 3.3 Harness engineering (the new mainstream, 2026)

- **Harness-Bench** (Yao et al., Peking / Qiyuan, May 2026, arXiv:2605.27922) — 106 sandboxed offline tasks; shows performance must be reported at **model–harness configuration** level. Identifies *execution-alignment failures* (plausible reasoning decoupled from tools/state/evidence).
- **HarnessOpt-Bench** (Ursekar et al., Scale AI, Aug 2026, arXiv:2608.06301) — optimizing the harness itself under a **trusted execution environment**, held-out test partition, budget metering.
- **OrchestraBench** (Aug 2026, arXiv:2608.05263) — orchestration failure modes, recovery, decomposition quality. **Explicit future work: full finance/HR suite and a real multi-agent harness.**
- **MAST** (Cemri et al., 2025) — 14 multi-agent failure modes; you should use this taxonomy rather than inventing a private one.
- **Who&When** (Zhang et al., ICML 2025) — failure attribution: which agent, when.

### 3.4 Self-healing

- **A Self-Healing Framework for Reliable LLM-Based Autonomous Agents** (arXiv:2605.06737) — detection + reliability score + recovery. Evaluated in *simulated* software automation. **Future work they name:** real production domains, proactive healing, finer-than-binary success.
- Union.ai / industry “infrastructure-as-context” — feed structured infra failures back into the loop; code-mode + stateless sandboxes as fallback.

### 3.5 Evaluation methodology surveys (use these as the “why now”)

- Yehudai et al., **A Survey on Evaluation of LLM-based Agents**, ACL 2026 Findings. Future work: cost, safety, granular trajectories, **decouple LLM vs harness**, Agent-as-Judge, standardized self-reflection eval (still a gap).
- “From benchmarks to deployment” (Artificial Intelligence Review, 2026).
- “Beyond Component Testing” (arXiv:2607.29405) — trajectory acceptability, not just task success; contamination; aging leaderboards.

---

## 4. The publication gap (this is the paper)

Several 2026 papers leave a hole that **your existing dataset fills exactly**.

| Paper | What they did | What they explicitly leave open |
|---|---|---|
| OrchestraBench | mechanism probes on arithmetic chains; keyword vs model routing | “Full **finance, HR**, and DevOps suite validation remains future work.” “A **real multi-agent harness** is future work.” |
| Self-healing framework (2605.06737) | simulated API/doc workflows, binary TSR | real production domains; proactive healing; partial success |
| Harness-Bench | SWE / workspace / generic agent tasks | not enterprise RAG over HR payroll + SEC + calendars |
| TheAgentCompany | simulated company tools (web, chat, files) | not oracle-checkable queries over large internal tabular/SEC corpora |
| ACL 2026 survey | maps the field | decouple model vs harness; cost; safety; self-reflection protocol |
| HarnessOpt-Bench | TEE for *optimizing* harnesses | you can *instantiate* that protocol on a workplace RAG agent |
| LH-Bench | subjective enterprise artifacts | you can add **oracle-first** enterprise tasks (numbers, joins, calendar constraints) where binary correctness *is* valid |

**One-sentence thesis:**

> Existing agent papers either evaluate coding/web harnesses, or evaluate workplace agents without a proper harness/oracle/recovery stack. Orion can be rebuilt as the missing piece: a **typed self-healing, dual-sandbox enterprise harness** on a **heterogeneous HR / filings / calendar lake**, with **oracle + judge-on-trace** scoring, a **Repair Agent** that patches worker code under a trusted eval boundary, and a **Lab that synthesizes new agents** rather than wrapping a fixed cast.

That is novel enough for Findings / D&B / a strong workshop, and potentially a main-conference paper if the oracle suite and ablations are tight.

### 4.1 What not to claim

Do **not** call prompt rewriting “RL.” If you want a learning component, use the literature’s language: **verbal reinforcement / Reflexion / harness optimization**. Real RL (GRPO/PPO on trajectories) is a follow-up paper, not v1.

Do **not** claim a new general agent benchmark that competes with Harness-Bench’s 106 tasks unless you are willing to release 100+ human-reviewed, oracle-checkable tasks. A **focused enterprise suite (80–150 tasks) on one corpus** is enough if it is high quality.

Do **not** claim SOTA on SWE-bench or τ-bench. Stay in your domain.

---

## 5. How to benchmark with no labeled dataset and no hired humans

This is the blocker. LLM-as-Judge was the right *engineering* shortcut. It is the wrong *only* metric for a paper, because a judge from the same model family can reward fluent nonsense and punish honest abstention. You do **not** need a crowd or a hand-written QA set to fix that.

SWE-bench does not hire people to grade patches; it runs tests. τ-bench does not hire people to grade every turn; it checks database state. You have the same kind of answer key: **HR CSV, ApplFin, FRED, calendar JSON**. The answers already exist as rows. The missing piece is a **compiler** that turns those rows into (question, gold, grader) triples.

That compiler *is* a contribution: “automatic oracle construction for enterprise multi-agent RAG, so evaluation does not require annotation.” ACL 2026 explicitly lists synthetic/automated eval as a future direction for scaling beyond human labels.

### 5.1 The protocol (three layers, zero crowd)

| Layer | Role | Who writes gold | What it measures |
|---|---|---|---|
| **L1 Oracle** | primary correctness | Python against the frozen tables | Exact numbers, sets, dates, SQL results, citations present/absent |
| **L2 Process / traces** | always on, every run | nobody | routing, tool choice, crash, schema error, citation-id overlap, cost, recovery class |
| **L3 Judge** | primary for open-ended **and** every-run quality rating | LLM **from a different family** than the worker | narrative quality **plus** a structured run score used by self-healing |

**Do both.** Oracle decides “was the number right.” Judge decides “was the run any good” (grounding, completeness, safety, whether healing helped). Neither replaces the other. A fluent wrong number fails L1. A correct number buried in a messy uncited dump fails L3. Self-healing may trigger on **either** signal.

### 5.2 Layer 1 — compile the eval set from your data (do this first)

Freeze one table per domain, then generate thousands of items with templates. Gold is computed, not written.

**HR** (`Employee_ID, Full_Name, Department, Job_Title, Hire_Date, Location, Performance_Rating, Experience_Years, Status, Work_Mode, Salary_INR`):

```text
template: "How many {Status} employees work in {Department} under {Work_Mode}?"
gold:     int(df.query(Status==... & Department==... & Work_Mode==...).shape[0])
grader:   extract the first integer from the answer; exact match (or ±0)

template: "What is the average Salary_INR for {Job_Title} in {Department}?"
gold:     df.loc[mask, "Salary_INR"].mean()
grader:   numeric within 0.5% relative error

template: "List Employee_IDs in {Department} with Performance_Rating == 5 and Status == Active"
gold:     sorted set of IDs
grader:   set equality (order-insensitive)
```

**Finance** (`ApplFin.csv` by Year/Quarter; FRED `DATE,CPIAUCSL`):

```text
template: "What was Apple NetSales_Total in {Year} Q{Quarter}?"
gold:     row.NetSales_Total
grader:   numeric exact (tables are integers)

template: "What was US CPI (CPIAUCSL) on {DATE}?"
gold:     row.CPIAUCSL
grader:   numeric exact / one decimal
```

**Scheduler** (use `event_id` as key; the JSON has repeats, so **dedupe by event_id** before compiling):

```text
template: "How many events does {organizer} have on {date}?"
gold:     count
grader:   integer exact

template: "Who attends {title} starting at {start_time}?"
gold:     set(attendees)
grader:   set F1 or exact set
```

**Cross-agent (still no humans):**

```text
template: "For department {D}, report headcount and Q{q} NetSales_Total."
gold:     {headcount: n, netsales: x}   # two oracles, both must match
grader:   both fields extracted
```

**Unanswerable / abstention (critical, still automatic):**

```text
template: "What is the salary of EMP9999999?"          # ID not in table
template: "What was revenue in year 1800?"
gold:     ABSTAIN
grader:   must not invent a number; regex for “not found/insufficient” + no hallucinated ID
```

Implementation sketch: one script `bench/compile_oracles.py` that samples keys from the frozen tables, fills templates, stores `{id, split, domain, query, gold, grader_type, gold_sql}` as JSONL. You can emit **500+ items in an afternoon**. Then **lock** a test split and never look at it while tuning prompts.

Paraphrase the questions with an LLM if you want linguistic variety — but **keep gold from pandas**, never from the LLM. That is the rule that makes this publishable.

### 5.3 Layer 2 — metrics that need no answers at all

These cost nothing extra if you record traces:

- **Routing:** you can label expected agent with the same templates (`hr` vs `finance` vs `scheduler` vs `sql`). That is not “writing QA”; it is a column in the compiler.
- **Tool contract:** forecast questions must call `forecast`; lookup questions must not.
- **Grounding:** any number in the answer should appear in tool output or retrieved text (string match). Fluency without that string is a fail.
- **Crash rate, timeout, JSON-parse fail, unknown-column rate**
- **Cost:** tokens, seconds, USD
- **Healing:** inject a broken column name / empty retrieval / wrong route; you already know the fault, so Recovery Success Rate needs no human

Fault injection is how the 2026 self-healing paper evaluates FDA/RSR. It is stronger than a judge saying “looks recovered.”

### 5.4 Layer 3 — keep LLM-as-Judge, but make it defensible

Use it only for tasks with no unique number: meeting-note bullets, “brief the CEO”, chart interpretation, policy-PDF questions.

Rules reviewers accept:

1. **Worker and judge are different families.** If the worker is Mistral, the judge cannot be Mistral Large. Use a second provider or a local open model. Same-family judging is the actual objection, not “you used a judge.”
2. **The judge sees evidence, not vibes.** Pass retrieved chunk IDs + tool tables into the judge; ask “is claim C supported by evidence E?” That is faithfulness, close to an oracle.
3. **Structured judge output:** `{supported: bool, hallucinated_numbers: [], score: 0-2}` parsed as JSON. Not a 0–100 essay score.
4. **You, not a crowd, calibrate on ~30 items.** You are allowed to be the annotator. One evening: sample 30 judge decisions, mark agree/disagree, report agreement (e.g. 26/30). Papers do this constantly. It is not “hiring people.” If you truly cannot do even this, drop L3 from the main table and put it in an appendix labeled *unvalidated LLM judge*.
5. **Never optimize the harness against judge scores on test.** Judge scores on test are reported once. Tune on dev oracles only (HarnessOpt-Bench held-out rule).

Agent-as-a-Judge (Zhuge et al., ICML 2025) is the citation for “a judge agent that reads traces.” Your eval sandbox is that, plus oracles so the judge is not the only voter.

### 5.5 Paper-facing wording (use this in the method section)

> We do not rely on crowdsourced answer keys. Structured tasks are compiled from the frozen enterprise tables: a template engine samples real keys and a deterministic grader checks the agent’s extractable answer against pandas gold. Open-ended tasks are scored by an isolated judge model from a different family, given retrieved evidence and traces. We report oracle TSR as the primary metric and judge scores as secondary.

That sentence pre-empts the “no human eval” review comment.

### 5.6 What this means for the paper story

Turn the constraint into a claim:

> **Enterprise agent evaluation can be oracle-first.** If the system is answering over tables, the tables *are* the benchmark. LLM-as-Judge is reserved for the residual subjective tail.

That is more interesting than “we hired 3 annotators.” It also matches your real situation.

Caveats to state in Limitations (do not hide them):

- Compiled questions are template-shaped; paraphrase with an LLM to reduce template overfitting, gold still from pandas.
- Calendar JSON contains duplicate events; compile on unique `event_id`.
- SEC text questions are weaker without span-level gold; treat SEC as retrieval+citation tasks (“must quote a chunk id that contains the keyword”), not free-form fact scoring, unless you extract a small numeric table from filings programmatically.
- A 30-item author check is not a user study. Do not claim usability/adoption without users.

### 5.7 Locked design: judge-on-trace + Repair Agent + Lab synthesizer

These three features belong in the paper. They also have hard isolation rules, or the judge gets hacked and the result is not publishable.

**Every run (no extra work for you):**

1. Worker executes inside the harness; every span is traced (`request_id`, agent, tool, args hash, result hash, error class, tokens).
2. **Oracle** scores extractable answers when gold exists.
3. **Eval agent (LLM-as-Judge)** reads the *trace + artifacts + retrieved evidence*, not just the final paragraph, and emits a structured card:

```json
{
  "overall": 0-2,
  "grounded": true,
  "complete": true,
  "safe": true,
  "failure_class": "F2|F4|none|...",
  "repair_hint": "column Performance_Rating not PerformanceScore"
}
```

4. If oracle fail **or** judge `overall < 2` **or** an exception: **Heal loop** (retry / replan / re-retrieve) with the judge’s `failure_class` and `repair_hint`. Budget: 2 cycles.
5. If still failing: optionally hand the same trace to the **Repair Agent** (below). Default for live queries is heal-only; Repair Agent is lab / offline so a paper run does not rewrite code on the test set.

One command you actually run: `python -m bench.run --split test --heal`. No writing QA, no clicking traces. A UI can still show traces when you want to inspect.

**Repair Agent (self-modifying code — yes, with a cage):**

Today the Lab only appends text to a prompt. The intended loop is: *see a bad span in the trace → agent rewrites the worker’s own code.* That is the Gödel / Darwin Gödel Machine / HarnessOpt-Bench line. Do it.

What it may edit (allow-list only): `harness/workers/` and `lab/generated/`.

- tool schemas, retrieval filters, pandas spec builders, prompts, routing rules
- a **new** agent file the Lab just synthesized

What it must **never** edit (eval sandbox / TEE):

- `bench/oracles/`, test JSONL, judge prompts used for the held-out split, API keys

Protocol (this is what makes it a paper, not a demo):

- Repair Agent gets: failing trace, judge card, oracle diff, current file(s).
- It proposes a patch in the sandbox, tests on **dev** oracles only.
- If dev score rises and crash rate does not, commit a **new version** (`agent@v17`). Test split is scored **once** at the end.
- Every patch is versioned and reversible. If the judge score goes up but the oracle goes down, that is **reward hacking** — report it, do not deploy it.

Cite: Reflexion (verbal heal on the same run), HarnessOpt-Bench / VeRO (code-level harness edit under a trusted eval boundary), ADAS / AFlow (agent programs as code).

**Lab as agent factory, not a mixer:**

Current Lab is a wrapper: it asks which of `hr, finance, scheduler, chart, sql` to enable and injects a system prompt. That is composition, not creation. Change it.

The Lab should **synthesize a new agent**:

1. User states a job (“vendor-risk analyst over 8-Ks + FRED”).
2. Lab writes a new worker module: role prompt, tool allow-list, retrieval collections, output contract, unit oracles compiled from the relevant tables.
3. New agent gets its own id, version, sandbox, and traces. It does **not** have to call the old HR agent.
4. It may *use* shared platform tools (`retrieve`, `sql_spec`, `forecast`) the way a new service uses an SDK — that is not “mixing pre-written agents.”
5. Then: simulate on compiled oracles → judge-on-trace → Repair Agent iterates the **new** file.

Paper language: **agent synthesis + self-repair**, not multi-agent routing among a fixed cast.

Safety: generated code runs only in the execution sandbox; no network; cannot import `os`/`subprocess`; cannot touch eval.

### 5.8 Mixed companies in one lake — this is OK if you name it

You already mix MSFT SEC filings, Apple financials, a global MNC HR file (Denmark, Costa Rica, …), FRED macros, and a synthetic Apple calendar. Do **not** pretend this is one fictional company. Reviewers will catch “our headcount vs our 10-K” when the 10-K is Microsoft and payroll is a generic MNC.

Frame it as a **heterogeneous enterprise data lake** (what real firms have after acquisitions and vendor dumps). That is a feature.

Required in the datasheet, as metadata on every chunk/row:

- `entity`: `MSFT` | `AAPL` | `MNC` | `FRED` | `SYNTH_CAL`
- `modality`: table | filing | calendar | macro
- `join_legal`: which sources may be joined

Then compile **three task families**, still with no manual QA:

| Family | Example | Gold |
|---|---|---|
| Intra-entity | “AAPL NetSales_Total 2023 Q4” | ApplFin row |
| Cross-source, legal | “FRED CPI on date D and AAPL stock that quarter” | two oracles |
| Cross-source, **illegal** | “MSFT 10-K revenue vs MNC payroll in IT” | gold = **ABSTAIN** / entity-mismatch |

The third family is paper gold: agents that glue incompatible companies must fail. The Repair Agent and judge should punish silent joins. Mixed data is then a **grounding / entity-disambiguation** benchmark, not a mess you apologize for.

---

## 6. Keep the same data — change how it is used

Do not collect a new corpus. Freeze and version what you have.

### 6.1 Canonical data layer (required cleanup)

Today agents disagree about what the world contains. Before any paper experiment:

1. **Pick one source of truth per domain** and **tag every row/chunk with `entity`** (MSFT, AAPL, MNC, FRED, SYNTH_CAL). Do not collapse them into “the company.”
2. Stop the SQL agent from reading a different `data/EXCEL` snapshot than the RAG store.
3. Materialize **queryable tables** (Parquet/SQLite) from the same files used for retrieval:
   - HR: stratified sample used for RAG **plus** a held-out SQL gold table with known aggregates.
   - Finance: ApplFin + FRED as numeric tables; SEC as retrieval-only documents with citation IDs.
   - Scheduler: sampled events as a calendar table with primary keys.
4. Publish `DATA_CARD.md`: row counts, date ranges, sampling method, PII policy, train/dev/test **query** split (data stays shared; **tasks** are split).

This is also a contribution: a **pinned enterprise workplace corpus** with both unstructured (SEC, policy PDF) and structured (HR, prices, calendar) modalities. Few public agent papers have that combination.

### 6.2 Task suite to build on top of this data (`orion-bench`)

Three task types, because reviewers now expect both oracles and process scores (Harness-Bench pattern):

**A. Oracle-checkable (primary, ~60%)**

Deterministic graders. Examples:

- HR: headcount by department/location/status; salary stats; tenure filters.
- Finance: period totals from ApplFin; FRED value on a date; “did this 10-Q mention X” with citation span.
- Scheduler: events on a date, conflicts, who is double-booked.
- SQL: exact dataframe/SQL result hashes.
- Multi-agent: “payroll vs budget” where the gold is a join of two tables.

**B. Process / contract tasks (~25%)**

- Correct agent routing (gold labels).
- Correct tool chosen (forecast vs retrieve vs SQL).
- Citation must come from retrieved chunk IDs.
- Disabled-agent fail-closed behavior.
- Permission: limited role must not see salary.

**C. Rubric / long-horizon (~15%)**

CEO briefing, meeting-notes extraction, cross-domain narrative. Score with an isolated judge from a **different model family**, given evidence + traces (see §5.4). Optional: you personally spot-check ~30 judge decisions in one evening and report agreement. If you skip even that, keep these tasks in the appendix, not the main table.

Splits: **dev / val / test**, non-overlapping queries. Test is invisible to any optimizer, lab agent, or prompt-tuning loop (HarnessOpt-Bench protocol).

---

## 7. What to build: a real harness

Rebuild Orion as **OrionHarness** (or a better name). Same agents/domains, new execution stack.

```
                    ┌─────────────────────────────────────────┐
                    │              Control plane               │
                    │  router · planner · budget · permissions │
                    └───────────────┬─────────────────────────┘
                                    │
              ┌─────────────────────┼─────────────────────┐
              ▼                     ▼                     ▼
     Worker agents            Tool gateway           Memory / RAG
     (builtin + Lab-          (allow-listed)         (entity-tagged)
      synthesized)                   │
                                    ▼
                          Execution sandbox
                                    │
                          traces (flight recorder)
                                    │
              ┌─────────────────────┴─────────────────────┐
              ▼                                           ▼
     Eval sandbox                                  Repair Agent
     oracle + LLM judge                            patches workers
     (cannot see test gold                         (cannot touch eval)
      until split is named)
              │
              └── judge card / oracle diff ──► heal loop (same run)
                                              └── optional patch (dev only)
```

### 7.1 Shared LLM gateway (delete the copy-paste)

One client: model id, temperature, timeout, token accounting, retry with backoff, request-id propagation. Every call is a traced span. This is how you later ablate “same model, different harness.”

### 7.2 Structured tools, not keywords

Finance should not regex for `"forecast"`. Expose tools with JSON schemas:

- `retrieve(collection, query, filters)`
- `sql_query(statement)` / `pandas_agg(spec)` — **spec, not free Python**
- `forecast(series_id, periods, method)`
- `anomaly_detect(series_id, method, k)`
- `calendar_query(...)`
- `render_chart(spec)`

Prefer **constrained specs** over generated Python. If you must generate code, it only runs in the execution sandbox.

### 7.3 Self-healing that is actually a contribution

Implement **typed recovery** aligned with MAST + OrchestraBench, not a generic retry loop.

Failure classes to detect (from traces, not from “the model said it failed”):

| Class | Detect how | Heal how |
|---|---|---|
| F1 Tool / runtime | exception, empty frame, timeout | sandbox re-exec with repaired spec; one retry max |
| F2 Retrieval miss | low similarity, no citation overlap | query rewrite + metadata filter + increase `k` with MMR |
| F3 Routing error | downstream agent returns domain-mismatch signal | re-route; never silently dump into finance |
| F4 Schema / column error | unknown column, type error | schema-repair prompt with **actual** columns only |
| F5 Decomposition error | missing join / wrong task graph | replan with previous results as evidence |
| F6 Semantic / latent | oracle or eval-agent contradiction | **do not blind-retry**; escalate to replan or abstain |
| F7 Policy / injection | gateway deny | fail closed, log, do not heal into compliance bypass |
| F8 Cascade | downstream consumed corrupt upstream | rewind to last trusted span (OrchestraBench finding) |

Required paper metrics:

- **TSR** task success (oracle)
- **FDA** failure detection accuracy (injected faults)
- **RSR by class** (this is the money table)
- **Cascade radius** vs pipeline depth
- **Abstention quality** (correct “I don’t know” vs hallucination)
- **EO** extra tokens / latency / $ from healing
- **pass^k** for reliability (τ-bench style)

Healing must be **budgeted** (e.g. max 2 repair cycles, 15% token overhead cap) or reviewers will say you bought accuracy with 5× cost.

Optional but strong: **proactive healing** — pre-exec schema validation, constrained decoding for tool JSON, retrieve-then-verify before the worker answers. The self-healing preprint listed this as future work.

### 7.4 Dual sandboxing

**Execution sandbox (worker tools)**

- No `eval` of free Python in-process.
- Options, in order of paper credibility: (1) **spec-only aggregations** (safest, easiest to grade), (2) RestrictedPython / WASM, (3) short-lived container with no network, read-only data mount, CPU/memory limits.
- Charts write only to a scratch volume.
- Tool gateway is the only path to data.

**Evaluation sandbox (judge)**

Copied from HarnessOpt-Bench’s trusted execution idea:

- Evaluation agent **cannot** see worker system prompts, API keys, or test gold during worker execution.
- It receives: task spec, traces, artifacts, retrieved chunk IDs, tool I/O.
- Oracles run as pure functions (pandas gold vs answer).
- Rubric judge is a **different model family** than the worker (e.g. worker = Mistral, judge = a second provider or local Llama). Same-family judging is a known bias.
- Held-out test labels live only in the eval sandbox.

This is the “evaluation agent” the user asked for: not another chat persona, a **privileged, isolated grader**.

### 7.5 Tracing (flight recorder)

Every run emits an OpenTelemetry-style trace:

`request_id → plan → [span: agent, tool, args, result_hash, tokens, latency, error_class]`

Without this you cannot compute process scores or recovery-by-class. Harness-Bench’s whole point is traces + artifacts + validators, not a final paragraph.

### 7.6 Security that matches an enterprise paper

Regex blacklists are not a contribution. For the paper:

- allow-listed tools only
- role-based views (salary columns stripped for `limited`)
- injection tests as a **graded security score** (Harness-Bench does this)
- sandbox so code-injection payloads cannot read `.env`
- keep PII redaction, but measure false positives (your current filters will redact legitimate finance discussion of “API” etc.)

### 7.7 Harness-engineering principles (what 2025–2026 production systems actually do)

Orion today is a **monolith**: harness, agents, `eval()`, credentials, and session prints all share one Python process. That is the failure mode the field already named. Use these principles; they are now standard, not optional.

**P1. Brain / session / hands (Anthropic managed-agents + Nexu guide, 2026).**  
The harness (brain) is **stateless**. The session is an **append-only event log** outside the process. Sandboxes and MCP tools (hands) are **disposable**. If a generated agent crashes, you kill that sandbox and keep going. The harness must never `import` Lab-generated code into its own process.

**P2. Small stable tool vocabulary, fat registry (Harness MCP v2).**  
They went from 130+ tools (~26% of a 200k window) to **11 generic verbs** (~1.6%) dispatching to 125–220 resource types. One-tool-per-API kills selection accuracy. Orion should expose ~8–12 verbs (`retrieve`, `sql_spec`, `forecast`, `calendar_query`, `http_fetch`, `search_tools`, `run_agent`, …) and a **registry** that maps `(verb, resource)` to an implementation. Adding a tool is a registry row, not a new agent file and not a new LLM schema dumped into every prompt.

**P3. Progressive disclosure (Anthropic Tool Search, MCP client best practices, Microsoft Agent Framework).**  
Do not stuff every tool and every agent schema into context. Always-on: 3–5 hot tools. Everything else: `search_tools` / `search_agents` then `load_schema`. Anthropic measured tool-selection accuracy *up* (Opus 4: 49% → 74%) when they stopped dumping catalogs. Threshold they publish: use search once you have **>10 tools or >10k tokens of schemas**.

**P4. Programmatic / code-mode tool calling (Anthropic 2026, Harbor, ForgeMax).**  
For fan-in (filter 2M HR rows, then average), the model writes a short script that calls tools *inside the sandbox*; only the summary returns to the LLM. Intermediate tables never enter the context window (one published example: ~150k tokens → ~2k). This is also how you stay fast.

**P5. Vault + proxy for anything on the internet.**  
Sandbox never sees API keys. Agent calls `http_fetch(resource_id, …)` → gateway looks up secrets, allow-listed host, timeout, size cap → returns text. Prompt injection that says “print env” finds nothing. Microsoft MCP Security Gateway: intercept call *and* response (injection in web pages is a real channel).

**P6. Circuit breakers and typed errors, not Python exceptions through the harness (ForgeMax, Bastion, MCP Gateway spec).**  
`Timeout | CircuitOpen | SchemaInvalid | PolicyDenied | AgentCrashed`. Three failures → that **agent or tool** goes `PAUSED` for 30s. The harness stays `Closed`. GraphBit: a failed sandbox is a **failed tool result** the LLM can retry, not an uncaught crash.

**P7. Typed catalog, not prompted routing (GraphBit, OrchestraBench).**  
Prompted “pick an agent from this paragraph” hallucinates ids and loops. Router may only emit an id that exists in the registry. Unknown id = structured error + re-route, never `ask_finance` as default.

**P8. Lazy everything.**  
Do not boot Chart, SQL, Lab agents, or a web sandbox until a tool call asks. Anthropic reported ~60% p50 / ~90% p95 TTFT drop when sandbox provisioning left the critical path. Orion currently loads data and hits Mistral for agents that were never needed.

**P9. Parallel DAG, sequential only on true dependencies (AdaptOrch, Claude Agent Teams, GraphBit).**  
Independent HR count and FRED CPI run **concurrently**. Sequential orchestration is why “many agents make it slow.” Routing overhead should be <50ms; the cost is LLM calls, so *fewer* calls in parallel beats *more* agents in a line.

**P10. Generated workers are cattle; the harness is the kernel.**  
Lab output starts `DRAFT`. It must pass contract tests + a sandbox smoke run before `ACTIVE`. Canary: shadow-score on dev oracles. One crash does not take down chat. Rollback to last good version. Repair Agent patches the worker, never the kernel.

### 7.8 Adding tools, including internet

**How a tool is added (human or Lab):**

1. Write a **manifest** (JSON): `id`, `verb`, `schema`, `side_effect` (`read|write|egress`), `timeout_ms`, `max_bytes`, `allowed_hosts` (if egress), `circuit_breaker`.
2. Register in the tool gateway. The LLM does not get a new top-level function; it gets a new `resource_type` behind an existing verb, **or** a deferred schema behind `search_tools`.
3. Compile 5–10 oracle cases for that tool (even web: “fetch this allow-listed fixture HTML and extract X”).
4. Only then is it callable.

**Internet tools that are actually useful on *this* corpus (allow-list, not open web):**

| Tool | Why it belongs | Constraint |
|---|---|---|
| `edgar_fetch(cik, form, date)` | Live MSFT/AAPL filings vs your frozen dump | SEC.gov only, rate-limit |
| `fred_series(id, date)` | FRED already in-tree; live refresh | api.stlouisfed.org |
| `web_search(q, domain)` | news/macro around a filing date | allow-list: sec.gov, fred.stlouisfed.org, apple.com, microsoft.com |
| `http_get(url)` | only if URL host ∈ allow-list | size cap, no redirects off-list |
| FX / quotes (optional) | Apple `Stock_Price` cross-check | one market-data host |

Do **not** give Lab-generated agents raw `requests.get`. They call `http_get` on the gateway. Writes (post, email, calendar mutate) are `side_effect=write` and default **confirm / dry-run** in the paper experiments.

MCP is the right *plugin socket*: each family of tools can be an MCP server (FRED, EDGAR, internal SQL). The harness speaks MCP; agents never hardcode URLs. Adding a server is config, not a rewrite of `finance_agent.py`.

### 7.9 Speed: many agents must not mean many sequential LLM calls

Current Orion: analyze (1 LLM) → agent1 (1) → agent2 (1) → synthesize (1), all serial, all Large/Nemo, all with fat prompts. That is the slowness.

Rules:

- **Router = small / cheap model**; workers = larger only when the task needs it (you already have `model_tier.py` — use it).
- **Two-stage route:** embed the query against AgentCards (~1ms) → top-3 cards in the prompt → LLM picks. Do not list 40 generated agents in the orchestrator prompt.
- **Run only the chosen workers, in parallel** if no data dependency. `asyncio.gather` / thread pool around HTTP.
- **Do not synthesize** if a single agent answered an oracle-shaped question.
- **Compact tool results** (Harness MCP “compact mode”): tables as 20-row previews + hash of full result stored in the session log; worker can `session_get(span_id)` if it needs more.
- **Cache** retrieval embeddings and FRED/SEC GETs with TTL.
- New Lab agents are **not** on the hot path until `ACTIVE` and until the router retrieved their card. Default catalog for live chat = builtin + ACTIVE with `latency_class=fast`. Heavy agents (`latency_class=slow`) only when `search_agents` pulls them in.

Paper metric: tokens and wall time **per successful oracle task**, not “we added 12 agents.”

### 7.10 How a Lab-made agent plugs in (and what if it is never routed)

Plug-in is a **registry event**, not an `if agent ==` in `orchestrator.py`.

AgentCard (stored in `lab/generated/<id>/card.json`):

```text
id, version, status: DRAFT|ACTIVE|PAUSED|RETIRED
intent: one-line (for embedding search)
entities: [AAPL, FRED, ...]
tools: [retrieve, sql_spec, http_get]
input_schema, output_schema
timeout_ms, memory_mb
```

Lifecycle:

1. Lab writes files in a sandbox workdir.
2. **Static gate:** allowed imports, no `os`/`subprocess`, Pydantic card valid, no writes to `harness/` or `bench/`.
3. **Contract tests:** 3 compiled oracles for its declared entities. Fail → stay `DRAFT`, Repair Agent may patch.
4. `register(card)` → embedding of `intent` added to the agent index. Orchestrator code does not change.
5. `ACTIVE`. Router can see it via search, not via a 4k-token dump.

**Misroute / never selected:**

- If embedding similarity to an ACTIVE card is high and the LLM picked something else → heal as **F3**, re-route with that card forced into the shortlist (OrchestraBench: detection, not blind retry).
- If the LLM emits an id not in the registry → typed error, never crash, never default-to-finance.
- Users (and the Lab) can `@agent_id` pin. Evaluation tasks for a new agent **set `allowed_agents: [that_id]`** so routing mistakes do not hide Lab quality.
- Builtin specialists remain the default; generated agents are opt-in until their oracle pass-rate on dev ≥ threshold.

### 7.11 Generated code must not be able to break the harness

This is the kernel vs process model.

| If Lab/Repair emits… | What happens |
|---|---|
| Syntax / import error | Load fails; agent stays `DRAFT`; trace `AgentCrashed`; chat still works |
| Infinite loop / hang | `timeout_ms` kills **only that sandbox** |
| `os.environ` / `.env` read | Import blocked; or empty env in sandbox |
| Exception mid-query | Span = failed tool; orchestrator continues other branches; circuit breaker |
| Prompt injection “ignore harness” | Worker cannot see harness source or eval; tools still go through gateway |
| Infinite agent↔agent loop | max depth (e.g. 3) + cycle detection on `run_agent` |
| Judge/oracle files edited | Path allow-list; TEE rejects; Repair Agent cannot open `bench/` |

Implementation bar for the paper (Windows-friendly): start with **subprocess + RestrictedPython / separate venv** per generated agent, timeout, memory cap. Containers later if you need them. Hot pool if sandbox start dominates (Bastion: <200ms pre-warm).

The Repair Agent’s job after a crash is: read the span, patch `lab/generated/<id>/`, re-run contract tests, bump version. It does not reboot the API.

---

## 8. Suggested system name and paper framing

**Working title:**

> **OrionHarness: Typed Self-Healing and Isolated Evaluation for Enterprise Multi-Agent RAG**

**Subtitle / contribution list (typical 3-bullet abstract):**

1. A **workplace agent harness** (routing, tools, permissions, traces, recovery) over a pinned HR–finance–calendar corpus.
2. A **dual-sandbox protocol**: constrained execution for tools + trusted evaluation agent with oracle-first grading.
3. Empirical result: on the same data and models, harness + typed healing improves oracle TSR and **recovery on tool/schema faults**, but **not** latent semantic faults — confirming OrchestraBench in a real multi-agent HR/finance setting, which they listed as future work.

That last sentence is how you cite and extend, not ignore, concurrent 2026 work.

**Alternate titles if you want a benchmark-flavored paper:**

- *EnterpriseHarness-Bench: Oracle-Checkable Multi-Agent Tasks over HR, SEC, and Calendar Corpora*
- *Heal-or-Abstain: Failure-Typed Recovery in Workplace Agents*

Pick one story. A harness paper with a small high-quality suite is cleaner than a giant benchmark with weak oracles.

---

## 9. Experiments a reviewer will accept

Hold **data** and **task suite** fixed. Vary harness and (separately) model.

### 9.1 Main table (must have)

Rows = model–harness configs, e.g.:

- Mistral-Nemo + **legacy Orion** (current code, seed harness)
- Mistral-Nemo + OrionHarness (no healing)
- Mistral-Nemo + OrionHarness + retry-only
- Mistral-Nemo + OrionHarness + typed healing
- Same four with a second model (required: “not just one API”)

Columns: oracle TSR, process score, security score, tokens, latency, $, RSR by failure class, abstention-F1.

This is the Harness-Bench reporting standard.

### 9.2 Ablations (must have)

- Healing off / retry-only / typed / typed+rewind
- Execution sandbox vs in-process `eval`
- Isolated eval-agent vs in-process Mistral-as-Judge
- Keyword router vs LLM router (OrchestraBench already showed 0% vs 100% on adversarial routing — replicate on *your* HR/finance queries)
- RAG-only vs SQL-oracle path for numeric questions (expect RAG to lose; that is a finding, not a bug)

### 9.3 Fault injection (must have)

Inject F1–F8 at known rates (the self-healing paper used 30%). Report FDA and RSR. Without injection, “self-healing works” is unfalsifiable.

### 9.4 Reliability (no crowd)

- 3 seeds for stochastic cells, 95% CIs
- pass^k on a 20-task *oracle* subset (same question, k independent runs; gold is still pandas)
- Optional one-evening author check: 30 judge decisions, report raw agreement. Skip this before you skip oracles.

### 9.5 What to drop from the current benchmark script

Drop or relabel: “RL Policy Improvement”, “Cross-Agent Co-Training”, “reduction in human edits” estimated by a judge, multi-persona usability as a *primary* metric. Those reads as lab-course KPIs. They can go to an appendix as “system usability,” not claims.

---

## 10. Implementation plan (same repo, clean architecture)

The full engineering spec (interfaces, plug-and-play rules, commands) is in **[IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)**. Corpus pin: **[DATA_CARD.md](DATA_CARD.md)**.

Suggested layout after the revamp (this is now the live tree):

```
orion/
  data/                  # frozen corpus (as now)
  harness/
    gateway.py           # single LLM + tool gateway, budgets
    router.py
    planner.py
    healing.py           # typed policies
    trace.py
    permissions.py
  sandbox/
    exec_sandbox.py
    eval_sandbox.py
  agents/                # thin domain policies; no raw HTTP
  retrieval/             # loaders + vector store
  lab/
    synthesizer.py       # writes new agent modules, not wrappers
    generated/           # versioned synthesized workers
  repair/
    repair_agent.py      # patch workers from traces; never touches bench/
  bench/
    tasks/               # jsonl: id, split, domain, entity, gold, grader
    oracles/
    eval_agent.py        # LLM-as-Judge on traces
    injectors.py
    run.py               # one command: compile + run + score
  experiments/           # configs for the paper tables
```

**Phase 0 — freeze truth (1 week)**  
Datasheet, entity tags, one table per domain, delete dual schemas, pin embedding model, pin chunker, pin index version.

**Phase 1 — harness skeleton (1–2 weeks)**  
Gateway, traces, structured tools, spec-based SQL (no `eval`), fail-closed permissions.

**Phase 2 — compile oracles + 200–500 tasks (1 week, no annotators)**  
`bench/compile_oracles.py` over frozen tables, including legal joins and illegal-join abstain tasks. Paraphrase queries with an LLM if needed; **gold always from pandas**. Lock test split.

**Phase 3 — sandboxes + eval agent (1–2 weeks)**  
Exec isolation; eval process with oracles **and** a different-family judge that scores every trace.

**Phase 4 — typed healing + Repair Agent + Lab synthesizer (2 weeks)**  
Same-run heal from judge cards; Repair Agent patches `workers/` and `lab/generated/` on **dev** only; Lab writes new agent files, then simulate → judge → repair.

**Phase 5 — paper experiments (1–2 weeks)**  
Two models × harness ablations (heal off / judge-heal / repair-on-dev); CIs; cost; reward-hacking check (judge↑ oracle↓).

Do not start Phase 5 until the test split is locked. Never let the Repair Agent see test gold.

---

## 11. Where to send it

### Realistic first target

- **EMNLP / ACL Findings** — agent evaluation + enterprise NLP is a natural fit; Findings is the honest venue if n_tasks is ~100 and models are 2.
- **NeurIPS Datasets & Benchmarks** — if you lead with `orion-bench` (tasks + oracles + traces) and treat the harness as the reference system.
- **ICLR / NeurIPS workshop** (agents, eval, trustworthy LLM) — best first public shot; cite and get feedback before a main conference.

### If the systems story is strong

- **AAAI** (application + intelligence systems)
- **FSE / ICSE SE4AI** or **TOSEM** — if you frame typed healing + sandboxes as **software reliability for agent systems** (Kephart autonomic computing lineage). This is underexploited and your CS/SE reviewers will like traces, failure classes, and RSR more than NLP reviewers will.

### Avoid as the *only* shot

A main-track NeurIPS/ICML paper that is “we wrapped Mistral with retries on private HR data.” That bar is now Harness-Bench scale (5k trajectories, 106 tasks, multiple harnesses). You can get there later by scaling the suite; do not pretend you are there on day one.

### Reviewer checklist (write the paper to this)

- [ ] Model and harness ablated independently  
- [ ] Oracle metrics for the majority of tasks (compiled from tables; no crowd required)  
- [ ] Held-out test never used for prompt/harness search  
- [ ] Cost/tokens reported next to accuracy  
- [ ] Recovery broken down by failure class  
- [ ] Judge ≠ worker model family  
- [ ] Code + task suite released (at least the oracles and harness)  
- [ ] Honest related work vs Harness-Bench, OrchestraBench, TheAgentCompany, Reflexion  
- [ ] No “RL” unless there are actual policy gradients or a clearly labeled verbal-RL setup  

---

## 12. Recommended paper outline

1. **Introduction** — workplace agents fail as *systems* (routing, tools, eval leakage), not just as models. OrchestraBench left HR/finance + real multi-agent harness open.
2. **Related work** — agents; workplace benches; harness benches; self-healing; eval isolation.
3. **Corpus and task suite** — datasheet; oracle vs rubric; splits.
4. **OrionHarness** — gateway, tools, dual sandbox, typed healing, eval agent.
5. **Experimental protocol** — configs, injection, metrics, cost.
6. **Results** — main table; RSR-by-class (expect the three-tier split); sandbox vs `eval` security; isolated judge vs in-process judge agreement.
7. **Analysis** — execution-alignment failures (Harness-Bench language) on SEC/HR retrieval; when healing hurts (cost, cascading wrong repairs).
8. **Limitations** — single organization-style corpus; English; API models; no real users.
9. **Conclusion** — harness + typed healing is the unit of progress for enterprise agents.

---

## 13. Bottom line

**Issue:** Orion is a working multi-agent RAG demo with a course-style benchmark. The interesting parts (HR + SEC + calendar data, orchestrator, lab/sim) are buried under copy-pasted clients, keyword tools, in-process `eval`, LLM-as-Judge, and mislabeled “RL.”

**Limitation to state, not a blocker:** no crowdsourced labels and no user study. Primary metrics are compiled oracles and traces. LLM-as-Judge is secondary and uses a different model family.

**Suggestion:** freeze the dataset, rebuild a real **harness** with structured tools, **typed self-healing**, **execution sandbox**, and an **isolated evaluation agent** with oracle-first tasks compiled from the same tables. Run model–harness ablations and recovery-by-failure-mode. That directly answers 2026 future-work bullets instead of competing with SWE-bench.

When you want to start implementation, the right order is: **datasheet + gold tables → oracles → gateway/tools → sandboxes → healing → experiments.** Do not add more judge prompts to `benchmark_validation.py`; that file should be replaced, not extended.
)
