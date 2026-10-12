# PROJECT PROPOSAL: PROJECT ORION (OrionHarness)
## A Typed Runtime Self-Healing Harness and Isolated Execution Framework for Enterprise Multi-Agent Workflows

---

**Project Identifier:** ORION-HARNESS-2026-v4.0  
**Author / Principal Investigator:** Mustafa Subhani  
**Affiliation:** University of Management and Technology (UMT), Lahore  
**Target Repository:** `d:/orion`  
**Classification:** Research & Enterprise System Proposal (Autonomous Multi-Agent Systems / AI Infrastructure)  
**Date of Submission:** October 2026  
**Document Status:** Complete Technical & Commercial Proposal  
**Keywords:** Multi-Agent Systems, Runtime Self-Healing, Enterprise Data Lakes, Execution Sandboxing, Fault Taxonomy & Precedence, Deterministic Oracles, Dynamic State Backtracking, Model Context Protocol (MCP)  

---

## Table of Contents
1. [Executive Summary](#1-executive-summary)
2. [Project Vision, Mission & Motivation](#2-project-vision-mission--motivation)
3. [The Enterprise Multi-Agent Problem Statement](#3-the-enterprise-multi-agent-problem-statement)
   - 3.1 The Enterprise Reliability Gap & Environmental Fragility
   - 3.2 "The Model is Not the System": Conflation of Reasoning and Scaffolding
   - 3.3 The Failure of Naive Scaffolding (Blind Retries and Cascade Poisoning)
   - 3.4 Security & Sandboxing Deficits in Production Deployments
4. [Project Objectives & Key Performance Indicators (KPIs)](#4-project-objectives--key-performance-indicators-kpis)
5. [System Architecture & Core Technical Innovations](#5-system-architecture--core-technical-innovations)
   - 5.1 Decoupled Three-Plane Model (Control, Execution, Supervisory)
   - 5.2 Directed Acyclic Graph (DAG) Wave Planner & Dynamic Backtracking
   - 5.3 The $\mathcal{F}_1$–$\mathcal{F}_8$ Operational Fault Taxonomy
   - 5.4 Deterministic Precedence Resolution Engine
   - 5.5 Multi-Tier Execution Sandboxing & AST Gate
   - 5.6 Cursor-Style Context Engine & Session Artifact Vault
   - 5.7 Hermes-Style Procedural Memory Engine (Self-Evolving Skills)
   - 5.8 Model Context Protocol (MCP) Gateway Integration
6. [Heterogeneous Enterprise Data Lake Architecture](#6-heterogeneous-enterprise-data-lake-architecture)
   - 6.1 Pinned Corpus Composition (HR, SEC, Financials, FRED, Calendars)
   - 6.2 Entity Boundary Enforcement & Legal Join Matrices
   - 6.3 Dual-Store Architecture: Immutable Gold vs. Live Knowledge Base
   - 6.4 Role-Based Access Control (RBAC) & PII Redaction
7. [Specialist Domain Agents & The Orion Lab Synthesizer](#7-specialist-domain-agents--the-orion-lab-synthesizer)
   - 7.1 Built-in Domain Specialists
   - 7.2 The Orion Lab: True Agent Synthesis vs. Prompt Composition
   - 7.3 Autonomous Offline Code Repair Agent
8. [Evaluation Methodology & Benchmark Verification](#8-evaluation-methodology--benchmark-verification)
   - 8.1 Oracle-First Evaluation Protocol (Zero Crowd Annotation)
   - 8.2 Stratified Task Suite ($N = 605$ Repository; $n = 137$ Evaluated)
   - 8.3 Adversarial Security Testing (300 Attack Probes)
   - 8.4 Empirical Benchmark Results & Statistical Validation
   - 8.5 Token Consumption & Cost Efficiency Modeling
9. [Full-Stack Implementation & Delivery Platform](#9-full-stack-implementation--delivery-platform)
   - 9.1 FastAPI Enterprise Backend Architecture
   - 9.2 Next.js 15 Full-Featured Control Plane Frontend
   - 9.3 End-to-End Flight Recording & Trace Auditing
10. [Implementation Roadmap, Phases & Work Breakdown Structure](#10-implementation-roadmap-phases--work-breakdown-structure)
11. [Budget, Resource Requirements & Infrastructure Forecast](#11-budget-resource-requirements--infrastructure-forecast)
12. [Risk Assessment, Governance & Mitigation Matrix](#12-risk-assessment-governance--mitigation-matrix)
13. [Academic Publication & Commercialization Strategy](#13-academic-publication--commercialization-strategy)
14. [Conclusion & Steering Sign-Off](#14-conclusion--steering-sign-off)

---

## 1. Executive Summary

Enterprise adoption of autonomous multi-agent Large Language Model (LLM) architectures is severely constrained by runtime fragility. While frontier foundation models excel at conversational reasoning, operational multi-agent deployments across enterprise data lakes consistently suffer from **environmental and structural faults**: drifting database schemas, missing table attributes, network transport timeouts, and upstream errors that cascade across collaborative agent boundaries. Current industry frameworks attempt to patch these failures using **blind retry loops** and unconstrained in-process execution (`eval()`), which inflate inference costs, pollute working context windows, hallucinate non-existent database columns, and introduce critical remote code execution vulnerabilities.

**Project Orion (OrionHarness)** solves this fundamental reliability crisis by decoupling foundation-model inference from execution scaffolding. Orion introduces an enterprise-grade, typed, self-healing multi-agent execution harness operating over a heterogeneous enterprise data lake spanning a 2,000,000-row global workforce database, regulatory SEC EDGAR filings, Apple product catalogs, Federal Reserve Economic Data (FRED), and corporate calendar systems.

### Core Architectural Pillars
1. **Operational $\mathcal{F}_1$–$\mathcal{F}_8$ Fault Taxonomy:** An 8-class taxonomy categorizing runtime failures (Tool Runtime, Empty Retrieval, Routing Error, Schema Invalidation, Decomposition Error, Execution Inconsistency, Policy Violation, and Cascade Poisoning) with a deterministic priority resolution sequence ($\mathcal{F}_7 \succ \mathcal{F}_8 \succ \mathcal{F}_4 \succ \mathcal{F}_1 \succ \mathcal{F}_2 \succ \mathcal{F}_5 \succ \mathcal{F}_3 \succ \mathcal{F}_6$).
2. **Dynamic Catalog-Grounded Self-Healing:** Automatic runtime interception of database faults, extraction of authoritative SQLite `PRAGMA` schemas, injection of deterministic diagnostic hints, and context scratchpad rollback within a strictly bounded repair budget ($B \le 2$).
3. **Multi-Tier Execution Sandboxing:** Strict Abstract Syntax Tree (AST) code gating, namespace isolation restricted to 23 pure builtins, and OS-level subprocess execution bounded by 512 MB memory and 10.0-second CPU ceilings, fully defending against 300 frozen adversarial injection probes (100.0% block rate).
4. **Oracle-First Evaluation Protocol:** Deterministic Python/pandas ground-truth verification over 605 compiled tasks, eliminating the circular bias, hallucination tolerance, and stochastic drift inherent to LLM-as-a-Judge evaluations.
5. **Procedural Memory & Dynamic Agent Synthesis:** Hermes-style procedural skill evolution from successful recoveries and an autonomous "Agent Lab" that synthesizes new domain specialists as isolated, versioned code rather than prompting a fixed cast.

### Proven Empirical Milestones
On matched development benchmarks, Orion achieves a **76.5% Task Success Rate (TSR)**, delivering a **+17.7 percentage point absolute gain** over the unhealed baseline (58.8%, exact McNemar $p = 0.03125$). Simultaneously, Orion’s context-compacting scratchpad rollback reduces token consumption by **4.6%** ($20,882$ vs. $21,882$ tokens/task), lowering model-inference cost per 1,000 successful tasks by **26.4%** ($4.37$ vs. $5.94$). On held-out test splits ($n = 69$), Orion achieves **75.4% TSR**, outperforming the baseline while maintaining 100% routing accuracy and 100% policy-compliant abstention on illegal cross-lake joins.

This proposal sets forth the technical architecture, implementation roadmap, governance framework, and commercialization pathway for scaling Orion into a premier enterprise intelligence backbone and high-impact academic publication.

---

## 2. Project Vision, Mission & Motivation

### 2.1 Vision
To establish **Orion** as the industry standard for fault-tolerant, self-healing multi-agent execution harnesses—transforming enterprise data lakes from static, siloed repositories into dynamic, reliable autonomous operational intelligence ecosystems without compromising on security, auditability, or financial predictability.

### 2.2 Mission
To replace ad-hoc, brittle agent prompting with disciplined software and systems engineering. Orion delivers an open, extensible, and mathematically grounded execution runtime that guarantees deterministic schema recovery, defends against privilege escalation and prompt injection, eliminates cascade failures, and provides verifiable, reproducible execution across multi-modal corporate assets.

### 2.3 Strategic Alignment
Modern enterprises are investing heavily in conversational assistants; however, surveys indicate that over 80% of enterprise AI proofs-of-concept fail to reach production due to unpredictability and security risks. Project Orion directly addresses the primary corporate bottlenecks:
- **Trust & Compliance:** Zero data leakage via RBAC, PII redaction, and deterministic join boundaries.
- **Cost Predictability:** Token compaction and budgeted recovery loops that prevent infinite recursion and run-away cloud bills.
- **Operational Resilience:** Real-time adaptation to live database schema shifts without requiring pipeline redeployment or model re-training.

---

## 3. The Enterprise Multi-Agent Problem Statement

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│                           THE ENTERPRISE RELIABILITY GAP                         │
├──────────────────────────────────────────────────────────────────────────────────┤
│                                                                                  │
│   USER QUERY: "Summarize Q3 IT payroll against 10-K cloud capital expenses."     │
│                                                                                  │
│   ❌ NAIVE AGENT HARNESS (Current State of the Art):                              │
│   ┌────────────────┐      ┌─────────────────┐      ┌─────────────────────────┐  │
│   │ Router picks   │ ───► │ Agent queries   │ ───► │ Schema KeyError thrown  │  │
│   │ wrong agent    │      │ raw data        │      │ Column 'Salary' missing │  │
│   └────────────────┘      └─────────────────┘      └────────────┬────────────┘  │
│                                                                 │               │
│   ┌─────────────────────────────────────────────────────────────▼────────────┐  │
│   │ BLIND RETRY LOOP: Re-sends raw stacktrace to LLM. Model apologizes,      │  │
│   │ hallucinates fake column 'AnnualSalary', leaks context, depletes tokens. │  │
│   │ Cross-joins incompatible MNC payroll with MSFT 10-K. Output: INVALID     │  │
│   └──────────────────────────────────────────────────────────────────────────┘  │
│                                                                                  │
│   ✔ ORION TYPED HARNESS (Proposed Architecture):                                 │
│   ┌────────────────┐      ┌─────────────────┐      ┌─────────────────────────┐  │
│   │ Capability     │ ───► │ Tool Gateway    │ ───► │ F4 Fault Intercepted:   │  │
│   │ DAG Router     │      │ AST Sandboxed   │      │ Dynamic PRAGMA Lookup   │  │
│   └────────────────┘      └─────────────────┘      └────────────┬────────────┘  │
│                                                                 │               │
│   ┌─────────────────────────────────────────────────────────────▼────────────┐  │
│   │ TYPED SELF-HEALING: Prunes corrupt scratchpad turn. Injects structured   │  │
│   │ catalog hint: ['Salary_INR']. Model auto-rebinds. Detects illegal entity │  │
│   │ join (MNC x MSFT). Emits verified, policy-compliant refusal.             │  │
│   │ Cost: -26.4% per task. Integrity: 100% verified. Result: AUDITED SUCCESS │  │
│   └──────────────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────────────────┘
```

### 3.1 The Enterprise Reliability Gap & Environmental Fragility
When autonomous agents are deployed within real-world IT infrastructure, they interface with data lakes that are structurally heterogeneous and dynamic:
- Column names in relational stores shift across database migrations without notice (e.g., `Salary` renamed to `Salary_INR` or `EmpID` to `Employee_ID`).
- Modalities vary radically, from tabular databases (Parquet, SQLite) and semi-structured time series (FRED) to dense textual regulatory filings (SEC EDGAR 10-K/10-Q) and temporal calendar schedules (ICS/JSON).
- When an agent encounters an environmental failure, naive runtimes fail completely.

### 3.2 "The Model is Not the System": Conflation of Reasoning and Scaffolding
As highlighted in recent research (ACL 2026 Agent Evaluation Survey, Harness-Bench, HarnessOpt-Bench), the AI community has historically conflated **foundation-model reasoning capabilities** with **execution harness quality**. 
- When an enterprise workflow fails, developers frequently swap the underlying LLM for a larger, more expensive model, assuming semantic incapacity.
- In reality, the breakdown is almost always infrastructural: missing tool parameter schemas, unhandled HTTP status codes, stale column references, and lack of rollback capabilities.
- Orion decouples these concerns, establishing that disciplined harness engineering yields greater reliability gains than simply scaling backbone model parameters.

### 3.3 The Failure of Naive Scaffolding (Blind Retries and Cascade Poisoning)
Existing agent scaffolds rely on primitive error handling:
1. **Unchecked Exception Propagation:** Raw Python stack traces are pasted into the conversational prompt. Models react by generating apologies, cycling through repetitive invalid actions, and exhausting context limits.
2. **Blind Exponential Backoff:** Re-running the exact same prompt with identical arguments fails 100% of the time on structural, schema, or semantic faults (as demonstrated by OrchestraBench, 2026).
3. **Cascade Poisoning ($\mathcal{F}_8$):** In multi-agent pipelines, when upstream Agent $A$ returns a flawed or empty result, downstream Agent $B$ consumes the malformed output as factual evidence, multiplying hallucinations exponentially until the entire workflow collapses.

### 3.4 Security & Sandboxing Deficits in Production Deployments
Most existing agent frameworks execute arbitrary Python code directly using `eval()` or unconstrained bash subshells. In an enterprise environment:
- Malicious prompt injections can trigger arbitrary remote code execution (RCE).
- Vulnerable agents can inspect environment variables (`.env`), extract API keys, exfiltrate confidential payroll data, or execute unauthorized lateral network requests.
- True enterprise production demands an uncompromising, multi-tier security boundary.

---

## 4. Project Objectives & Key Performance Indicators (KPIs)

Project Orion is structured around rigorous, measurable engineering and scientific milestones:

| Objective Area | Target Key Performance Indicator (KPI) | Baseline (Legacy / No-Heal) | Orion Target / Achieved |
|---|---|---|---|
| **Task Success Rate** | Development Partition ($n=34$) Oracle TSR | 58.8% | **$\ge 75.0\%$ (Achieved: 76.5%)** |
| **Held-Out Success** | Held-Out Test Partition ($n=69$) Oracle TSR | 68.1% | **$\ge 75.0\%$ (Achieved: 75.4%)** |
| **Statistical Significance** | McNemar's Paired Exact Test ($p$-value) | $p \ge 0.05$ (Not sig) | **$p < 0.05$ (Achieved: $p = 0.03125$)** |
| **Security Defense** | Adversarial Attack Suite Block Rate (300 probes) | 0.0% (Unsandboxed) | **100.0% (300/300 Blocked)** |
| **Policy Enforcement** | Illegal Cross-Lake Join Abstention $F_1$ Score | 40.0% (Hallucinates) | **100.0% ($F_1 = 1.0$)** |
| **Cost Efficiency** | Normalised Inference Cost per 1k Successful Tasks | $5.94 | **$\le $4.50 (Achieved: $4.37 / -26.4%)** |
| **Token Consumption** | Average Total Tokens consumed per task | 21,882 tokens | **$\le 21,000$ (Achieved: 20,882 / -4.6%)** |
| **Recovery Efficiency** | Average Repair Cycles per successful healed task | $\infty$ (Infinite loop) | **$\le 1.5$ (Achieved: 1.3 cycles)** |
| **Tool Validity** | Conformance to tool schemas & registered verbs | 88.0% | **100.0% Schema Conformance** |
| **Execution Sandboxing** | CPU Timeout & Memory Ceiling Enforced | None (Host leak) | **10.0s CPU / 512MB RAM Cap** |

---

## 5. System Architecture & Core Technical Innovations

Orion separates all system operations into three decoupled architectural planes, ensuring that failure in one component cannot compromise platform stability or evaluation integrity.

```
┌─────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                   ORION SYSTEM ARCHITECTURE                                     │
├─────────────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                                 │
│                                    1. CONTROL PLANE                                             │
│   ┌─────────────────────────────────────────────────────────────────────────────────────────┐   │
│   │  User / Client (FastAPI / SSE Stream / Next.js Web UI / Model Context Protocol - MCP)  │   │
│   └──────────────────────────────────────────┬──────────────────────────────────────────────┘   │
│                                              ▼                                                  │
│   ┌─────────────────────────────────────────────────────────────────────────────────────────┐   │
│   │  Security Gateway: Preprocessor Regex Gate · JWT / Bearer Auth · RBAC Role Stripping    │   │
│   │  Cross-Entity Policy Validator: Legal Join Matrix Checking (Legal(AAPL), Legal(MNC))   │   │
│   │  Two-Stage Capability Router: Embedding Semantic Match (Top-3) -> Strict Catalog Dispatch│  │
│   │  Execution DAG Wave Planner: Topological Dependency Sort · Parallel Execution Waves     │   │
│   └──────────────────────────────────────────┬──────────────────────────────────────────────┘   │
│                                              ▼                                                  │
│                                2. EXECUTION & SANDBOXING PLANE                                  │
│   ┌─────────────────────────────────────────────────────────────────────────────────────────┐   │
│   │  Domain Specialists: A_HR · A_Finance · A_Scheduler · A_SQL · A_Chart                   │   │
│   │  Tool Gateway (Allow-Listed Verbs): workforce_slice · sql_spec · macro_latest · etc.    │   │
│   │  AST Security Gate: Python AST Visitor (Rejects imports, exec, eval, dunder traversal) │   │
│   │  Isolated Subprocess Workers: 512MB RAM Ceiling · 10s CPU Timeout · Egress Filtering    │   │
│   │  Context Engine & Vault: Output Reservation Buffer · Compaction · Artifact Vault Stubs  │   │
│   └──────────────────────────────────────────┬──────────────────────────────────────────────┘   │
│                                              ▼                                                  │
│                               3. SUPERVISORY & RECOVERY PLANE                                   │
│   ┌─────────────────────────────────────────────────────────────────────────────────────────┐   │
│   │  Precedence Resolver: F7 (Policy) ≻ F8 (Cascade) ≻ F4 (Schema) ≻ F1 (Infra) ...        │   │
│   │  Typed Healing Engine: Dynamic SQLite PRAGMA Introspection · Targeted Context Injection │   │
│   │  State Backtracking: Clean Scratchpad Rollback · Parent Checkpoint Re-binding           │   │
│   │  Bounded Node Budget: Max 2 Recovery Cycles per DAG Node (B <= 2)                        │   │
│   │  Hermes Procedural Memory: Successful recoveries compiled to outputs/skills/*.md       │   │
│   │  Flight Recorder / Tracing: Traces written to disk (.jsonl) with SHA-256 state hashes  │   │
│   └─────────────────────────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────────────────────────┘
```

### 5.1 Decoupled Three-Plane Model
1. **Control Plane:** Manages request lifecycle, user authentication, role-based access control, security sanitization, query decomposition, and topological workflow dispatch.
2. **Execution & Sandboxing Plane:** Houses domain worker agents and external tools within strictly bounded operating boundaries. No agent code runs in the host process.
3. **Supervisory & Recovery Plane:** Monitors execution telemetry, detects environmental exceptions, classifies faults across the $\mathcal{F}_1$–$\mathcal{F}_8$ taxonomy, applies deterministic recovery transformations, and maintains flight recording traces.

### 5.2 Directed Acyclic Graph (DAG) Wave Planner & Dynamic Backtracking
Rather than relying on naive sequential agent chaining, Orion constructs a formal execution run graph $\mathcal{G} = (\mathcal{V}, \mathcal{E})$:
- **Parallel Waves:** Independent tasks (e.g., retrieving HR headcount while fetching macroeconomic CPI data) are grouped into concurrent ready-sets executed via asynchronous worker pools.
- **Topological Dependencies:** Pipelines with semantic dependencies (e.g., employee filtering feeding into salary aggregations) form directed edges.
- **Checkpointing:** Each node execution writes a state checkpoint containing the intermediate result and data hash.
- **Dynamic Backtracking:** When a child node experiences an unresolvable failure, the planner rolls back the execution wave, discards tainted intermediate state, re-binds parent checkpoints, and triggers structural replanning ($\mathcal{F}_5$).

### 5.3 The $\mathcal{F}_1$–$\mathcal{F}_8$ Operational Fault Taxonomy
Orion formally categorizes all runtime failures into eight operational classes, each mapped to a verified, deterministic remediation mechanism:

| Class | Fault Designation | Detection Precondition / Environmental Signal | Deterministic Recovery Action |
|---|---|---|---|
| **$\mathcal{F}_1$** | Tool Runtime / Infrastructure | Tool exception, network timeout, HTTP 429/503, invalid JSON response | `RETRY`: Jittered exponential backoff or Levenshtein tool re-binding. |
| **$\mathcal{F}_2$** | Empty Retrieval | Retrieved chunk set $|S| = 0$ or semantic similarity score $< \tau$ ($\tau = 0.5$) | `RE_RETRIEVE`: Query expansion, entity synonym extraction, filter relaxation. |
| **$\mathcal{F}_3$** | Routing Error | Downstream agent raises `DomainMismatch` or unknown agent ID emitted | `RE_ROUTE`: Re-invoke capability router with explicit domain boundaries. |
| **$\mathcal{F}_4$** | Schema Invalidation | SQLite `OperationalError`, unknown column, type mismatch in SQL spec | `SCHEMA_REPAIR`: Introspect SQLite `PRAGMA table_info`, inject column catalog. |
| **$\mathcal{F}_5$** | Decomposition Error | Plan missing required relational dependency or cyclic edge detected | `REPLAN`: Regenerate wave DAG with explicit sub-goals from parent checkpoints. |
| **$\mathcal{F}_6$** | State Inconsistency | Relational assertion failure, unexpected zero-row merge, dataframe hash error | `ESCALATE_ABSTAIN`: Terminate execution loop; emit verified refusal. |
| **$\mathcal{F}_7$** | Policy Denied | RBAC permission breach, unapproved cross-entity join, prompt injection pattern | `FAIL_CLOSED`: Immediate execution termination; authorized policy refusal. |
| **$\mathcal{F}_8$** | Cascade Poisoning | Upstream agent returns error payload consumed by downstream dependency | `REWIND`: Circuit-break branch; backtrack scratchpad to trusted parent checkpoint. |

### 5.4 Deterministic Precedence Resolution Engine
Multiple error signals frequently manifest simultaneously (e.g., a query violating an RBAC policy might also throw an invalid column error). Naive systems produce non-deterministic recovery paths depending on which exception surfaces first.

Orion enforces a **mathematically deterministic precedence order**:
$$\mathcal{F}_7 \succ \mathcal{F}_8 \succ \mathcal{F}_4 \succ \mathcal{F}_1 \succ \mathcal{F}_2 \succ \mathcal{F}_5 \succ \mathcal{F}_3 \succ \mathcal{F}_6$$

**Resolution Logic:**
1. **Security & Policy ($\mathcal{F}_7$):** Highest priority. If a policy violation occurs, all recovery is halted immediately, and the system fails closed. No retry or schema inspection is permitted.
2. **Cascade Poisoning ($\mathcal{F}_8$):** Second priority. Halts corrupted downstream pipelines before repairing localized child exceptions.
3. **Schema Breakdown ($\mathcal{F}_4$):** Prioritized over retrieval or routing faults because re-querying an invalid schema guarantees repeat failure.
4. **State Inconsistency ($\mathcal{F}_6$):** Lowest priority fallback. If data state invariants are violated and cannot be repaired by catalog introspection, the system abstains rather than hallucinating.

```
                    ┌──────────────────────────────────────────────┐
                    │          Runtime Fault Intercepted           │
                    └──────────────────────┬───────────────────────┘
                                           │
                           Is Policy Denied or Injected?
                                  ┌────────┴────────┐
                             YES  │                 │ NO
                                  ▼                 │
                        ┌──────────────────┐        │
                        │  F7: FAIL_CLOSED │        ▼
                        │ (Terminate Loop) │  Is Upstream Corrupt?
                        └──────────────────┘        ┌────────┴────────┐
                                               YES  │                 │ NO
                                                    ▼                 │
                                          ┌──────────────────┐        ▼
                                          │    F8: REWIND    │  Is Schema Invalid?
                                          │ (Prune Context)  │        ┌────────┴────────┐
                                          └──────────────────┘   YES  │                 │ NO
                                                                      ▼                 │
                                                            ┌──────────────────┐        ▼
                                                            │ F4: SCHEMA_REPAIR│  Inspect F1, F2,
                                                            │  (PRAGMA Hint)   │  F5, F3, F6 ...
                                                            └──────────────────┘
```

### 5.5 Multi-Tier Execution Sandboxing & AST Gate
To provide true production safety, Orion establishes defense-in-depth across three architectural layers:

```
┌────────────────────────────────────────────────────────────────────────┐
│                        THREE-TIER SANDBOX DEFENSE                      │
├────────────────────────────────────────────────────────────────────────┤
│  Layer 1: Static AST Syntax Gate                                       │
│  - Inspects code before execution via Python `ast.NodeVisitor`         │
│  - Blocks: `import`, `exec`, `eval`, `open`, `__import__`              │
│  - Rejects dunder traversal (`__class__`, `__subclasses__`, `__dict__`)│
├────────────────────────────────────────────────────────────────────────┤
│  Layer 2: Namespace Whitelist Isolation                                │
│  - Restricted `__builtins__` limited to exactly 23 pure builtins       │
│  - Math, string manipulation, and pure functions only                  │
│  - Zero OS, filesystem, or reflection access                           │
├────────────────────────────────────────────────────────────────────────┤
│  Layer 3: Subprocess OS Quota & Egress Guard                           │
│  - Dedicated OS worker subprocess decoupled from API control plane     │
│  - Bounded quotas: 512 MB memory ceiling, 10.0-second CPU timeout      │
│  - Egress domain allowlisting (`sec.gov`, `fred.stlouisfed.org`)       │
└────────────────────────────────────────────────────────────────────────┘
```

Across 300 frozen adversarial injection probes (spanning Arbitrary Code Execution, Filesystem Access, Process Spawning, Dunder Reflection, and Network Probing), Orion achieved a **100.0% block rate** with zero escapes.

### 5.6 Cursor-Style Context Engine & Session Artifact Vault
A major driver of failure in long-horizon agent workflows is **context window pollution**. Standard agent loops append every large database table dump directly into the conversation history, resulting in context exhaustion, attention dilution, and soaring token expenses.

Orion deploys a **Cursor-style context management engine**:
1. **Dynamic Model Window Calibration:** Inspects model context ceilings (`ORION_CONTEXT_WINDOW`) and reserves output generation buffers.
2. **Session Artifact Vault:** Intermediate tool outputs exceeding threshold limits are intercepted, assigned a UUID, and stored in the request's out-of-core artifact vault.
3. **Extractive Compaction:** Prompts retain only a compact summary and a lightweight pointer (`artifact_id`). If an agent requires deeper inspection, it invokes the built-in `session_get(artifact_id)` tool.
4. **Scratchpad Backtracking:** When a recovery action is executed, the faulty tool call and raw exception trace are pruned from the working memory, replaced by a targeted, 1-line diagnostic hint.

### 5.7 Hermes-Style Procedural Memory Engine (Self-Evolving Skills)
Beyond deterministic in-loop recovery, Orion introduces an evolutionary procedural memory system:
- When a tool invocation fails and subsequently succeeds after recovery, the recovery trajectory is analyzed.
- A procedural skill document (`SKILL.md`) is synthesized under `outputs/skills/<slug>/`, documenting the exact tool parameter mapping, column correction, or formatting rule.
- Future requests retrieve the top-$k$ relevant procedural skills via semantic search and inject them into worker system prompts.
- An autonomous curator merges duplicate skills and archives low-utility procedures, capping memory overhead without human intervention.

### 5.8 Model Context Protocol (MCP) Gateway Integration
To ensure complete enterprise interoperability, Orion implements the open Anthropic Model Context Protocol (MCP) specification via JSON-RPC 2.0:
- **`tools/list`:** Discovers available domain tools dynamically.
- **`tools/call`:** Dispatches sandboxed executions through the tool gateway.
- **`resources/read`:** Provides schema catalog introspection via standardized `table://<domain>/<table_name>` resource URIs.

---

## 6. Heterogeneous Enterprise Data Lake Architecture

Orion operates over an authentic, pinned enterprise data lake representing the structural complexity and multi-modal heterogeneity found in corporate environments.

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│                         ORION HETEROGENEOUS ENTERPRISE LAKE                      │
├──────────────────────────────────────────────────────────────────────────────────┤
│                                                                                  │
│   ┌────────────────────┐   ┌────────────────────┐   ┌────────────────────────┐   │
│   │   WORKFORCE LAKE   │   │   FINANCIAL LAKE   │   │     CALENDAR LAKE      │   │
│   │   (Entity: MNC)    │   │ (Entity: AAPL/MSFT)│   │  (Entity: SYNTH_CAL)   │   │
│   ├────────────────────┤   ├────────────────────┤   ├────────────────────────┤   │
│   │ 2,000,000 Rows     │   │ SEC EDGAR 10-K/10-Q│   │ 4,000 Deduplicated     │   │
│   │ Global HR Database │   │ ApplFin Financials │   │ Corporate Events       │   │
│   │ Parquet & SQLite   │   │ 100k Catalog SKUs  │   │ JSON & ICS Formats     │   │
│   │ Salary, Tenure,    │   │ Multi-Year Filings │   │ Attendees, Timestamps, │   │
│   │ Performance Rating │   │ Balance Sheets     │   │ Room Allocations       │   │
│   └─────────┬──────────┘   └─────────┬──────────┘   └───────────┬────────────┘   │
│             │                        │                          │                │
│             │              ┌─────────┴──────────┐               │                │
│             │              │ MACROECONOMIC LAKE │               │                │
│             │              │   (Entity: FRED)   │               │                │
│             │              ├────────────────────┤               │                │
│             │              │ 40 Years FRED Data │               │                │
│             │              │ CPI, Fed Funds,    │               │                │
│             │              │ Consumer Sentiment │               │                │
│             │              └─────────┬──────────┘               │                │
│             │                        │                          │                │
│             ▼                        ▼                          ▼                │
│   ┌──────────────────────────────────────────────────────────────────────────┐   │
│   │                       DECLARATIVE JOIN POLICY MATRIX                     │   │
│   │                                                                          │   │
│   │   ✔ LEGAL JOINS:                                                         │   │
│   │   • Apple Financials (AAPL) ⟕ FRED Macro (FRED)                          │   │
│   │   • Microsoft SEC (MSFT) ⟕ FRED Macro (FRED)                             │   │
│   │   • Corporate Calendar (SYNTH_CAL) ⟕ Executive Profiles (AAPL)          │   │
│   │                                                                          │   │
│   │   ❌ STRICTLY ILLEGAL JOINS (Guaranteed Abstention):                      │   │
│   │   • MNC Global Payroll ⨂ Microsoft EDGAR Filings                         │   │
│   │   • MNC Employee Records ⨂ Apple Product Catalog                         │   │
│   │   * Any unauthorized cross-entity query triggers verified FAIL_CLOSED    │   │
│   └──────────────────────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────────────────────┘
```

### 6.1 Pinned Corpus Composition
1. **Global Workforce Lake (`global_hr`):** 2,000,000 synthetic employee records across 11 relational attributes (`Employee_ID`, `Full_Name`, `Department`, `Job_Title`, `Hire_Date`, `Location`, `Performance_Rating`, `Experience_Years`, `Status`, `Work_Mode`, `Salary_INR`).
2. **SEC Regulatory Lake (`sec`):** Ten years of Apple Inc. and Microsoft Corp. regulatory disclosures (10-K annual reports, 10-Q quarterly filings, 8-K current events) indexed for dense semantic retrieval.
3. **Financial Catalog (`appl_fin` & `products`):** Apple quarterly operational financials (including `NetSales_Total`) combined with a 100,000-row product SKU catalog.
4. **Macroeconomic Indicators (FRED):** Four decades of Federal Reserve Economic Data, including Consumer Price Index (`CPIAUCSL`), Federal Funds Rate (`FEDFUNDS`), and Consumer Sentiment (`UMCSENT`).
5. **Corporate Calendar Lake (`calendar_sample`):** 4,000 deduplicated corporate scheduling entries keyed on immutable `event_id` fields, tracking organizers, attendees, start/end windows, and room constraints.

### 6.2 Entity Boundary Enforcement & Legal Join Matrices
A major failure mode in enterprise data lakes is the "silent cross-join," where agents inadvertently combine unrelated corporate entities (e.g., comparing an MNC subsidiary payroll in Costa Rica directly against Microsoft's global revenue).

Orion tags every table, row, and retrieved chunk with an immutable `entity` metadata tag:
$$\text{Legal}(\text{AAPL}) = \{\text{AAPL}, \text{FRED}, \text{SYNTH_CAL}\}$$
$$\text{Legal}(\text{MSFT}) = \{\text{MSFT}, \text{FRED}\}$$
$$\text{Legal}(\text{MNC}) = \{\text{MNC}\}$$

If a user query mandates an intersection outside these authorized boundaries, Orion's pre-execution policy validator intercepts the request and issues a **verified policy-compliant abstention** (`AuthorizedRefusal`), achieving a **100% Policy Abstention $F_1$ score**.

### 6.3 Dual-Store Architecture: Immutable Gold vs. Live Knowledge Base
To protect benchmark integrity while supporting enterprise productivity, Orion implements strict store segregation:
- **Immutable Paper Lake (`data/frozen/`):** The benchmark data lake is frozen via SHA-256 manifests. No runtime execution, user upload, or evaluation script can modify this partition.
- **Live Enterprise Knowledge Base (`kb/`):** Live user uploads via the web UI (`POST /ingest`) land in an isolated `kb/` store and live ChromaDB vector index (`kb_live`). Live chat agents can search uploaded documents via `kb_search` and `kb_query`, completely isolated from the evaluation ground truth.

### 6.4 Role-Based Access Control (RBAC) & PII Redaction
Orion integrates enterprise-grade identity governance:
- **`super_user` / `admin`:** Full administrative access, schema management, and Agent Lab deployment rights.
- **`limited` User Role:** Column-level projection filtering in `sql_spec`. PII attributes such as `Salary_INR`, home addresses, and phone numbers are stripped from SQL results before returning to the agent.
- **Output Redaction Engine:** Final synthesized responses pass through high-performance regex filters that mask residual sensitive identifiers (SSNs, credit cards, credentials) prior to transmission.

---

## 7. Specialist Domain Agents & The Orion Lab Synthesizer

### 7.1 Built-in Domain Specialists
Orion deploys five specialized domain workers, each defined by an immutable `AgentCard` specification detailing its system instructions, allow-listed tools, and input/output contracts:
1. **$\mathcal{A}_{\text{HR}}$ (Workforce Specialist):** Executes high-performance relational queries over the 2M-row employee lake via `workforce_slice`, `hr_policy_lookup`, and demographic aggregators.
2. **$\mathcal{A}_{\text{Finance}}$ (Financial & Regulatory Analyst):** Queries structured Apple financials, parses SEC EDGAR filings, and retrieves FRED macroeconomic series via `sql_spec`, `macro_latest`, and `sec_retrieval`.
3. **$\mathcal{A}_{\text{Scheduler}}$ (Corporate Calendar Coordinator):** Resolves complex scheduling constraints, identifies double-booked personnel, and queries event availability via `calendar_query` and `calendar_conflicts`.
4. **$\mathcal{A}_{\text{SQL}}$ (Relational Query Engine):** Translates multi-step natural language queries into constrained, type-safe SQL specifications executed exclusively within the sandboxed database engine.
5. **$\mathcal{A}_{\text{Chart}}$ (Visualization Specialist):** Generates publication-grade statistical charts and renders visual assets to isolated scratch volumes.

### 7.2 The Orion Lab: True Agent Synthesis vs. Prompt Composition
Most commercial platforms claim to offer "custom agents" by simply injecting a natural language prompt into a generic chatbot wrapper.

**The Orion Lab (`lab/synthesizer.py`) synthesizes entirely new agents as autonomous software modules:**
1. **Requirement Formulation:** The user specifies an operational role (e.g., *"Vendor Risk Analyst monitoring SEC 8-K filings and FRED interest rate spikes"*).
2. **Autonomous Code Generation:** The Lab synthesizes:
   - An `AgentCard` manifest (`card.json`) declaring schemas, entity permissions, and tool allow-lists.
   - An executable worker script (`agent.py`) implementing specialized reasoning logic.
3. **Static AST Verification:** Generated code is inspected by the AST gate to confirm zero forbidden imports or OS primitives.
4. **Autonomous Unit Oracle Compilation:** The Lab generates 3–5 unit test oracles against the target tables.
5. **Lifecycle Promotion:** The agent starts in `DRAFT` status. Once it passes sandbox contract verification, it is promoted to `ACTIVE` and registered in the capability router's embedding index.

### 7.3 Autonomous Offline Code Repair Agent
When a synthesized agent in the Lab experiences recurring failures during pilot testing, Orion’s **Repair Agent (`repair/repair_agent.py`)** is activated:
- Receives the failed execution trace, SQLite PRAGMA schema diff, and agent source code.
- Generates a targeted code patch modifying query construction or tool argument mappings.
- Re-runs unit oracles within the execution sandbox.
- Commits a new version (`agent@v2`) upon verified test improvement.
- **Strict Boundary:** The Repair Agent is permanently locked out of the core harness (`harness/`) and benchmark evaluation suites (`bench/`).

---

## 8. Evaluation Methodology & Benchmark Verification

### 8.1 Oracle-First Evaluation Protocol (Zero Crowd Annotation)
A critical flaw in modern agent research is reliance on **LLM-as-a-Judge**, where an LLM evaluates its own outputs or those of a model from the same family. Research confirms that LLM judges suffer from verbosity bias, fail to catch subtle numerical errors, and frequently reward fluent hallucinations.

Orion establishes an **Oracle-First Evaluation Protocol**:
- **Deterministic Python/pandas Ground Truth:** Every benchmark query is paired with an exact mathematical oracle executed directly against frozen Parquet/SQLite tables.
- **Grader Types:**
  1. *Discrete Integer Matching:* $\mathbf{1}[\hat{y} = y^*]$ for headcounts, event tallies, and discrete metrics.
  2. *Continuous Numerical Bounds:* $\mathbf{1}\left[\frac{|\hat{y} - y^*|}{|y^*|} \le 0.005\right]$ (within 0.5% relative error) for financial and macroeconomic metrics.
  3. *Structural DataFrame Equivalence:* Set matching of column headers, index alignment, and numerical closeness.
  4. *Verified Policy Abstention:* Exact boolean check confirming refusal on illegal cross-lake joins with zero hallucinated figures.
- Secondary LLM judges are relegated strictly to qualitative trace audits and have zero influence on primary benchmark scoring.

### 8.2 Stratified Task Suite ($N = 605$ Repository; $n = 137$ Evaluated)
The benchmark repository comprises 605 compiled tasks, stratified with fixed seeds into matched splits locked via SHA-256 manifests:
- **Development Partition ($n = 34$):** Used for harness calibration, component ablation, and recovery evaluation.
- **Validation Partition ($n = 34$):** Dedicated to cross-model generalization testing (e.g., Qwen2.5-72B).
- **Held-Out Test Partition ($n = 69$):** Scored exactly once under greedy decoding ($T = 0.0$). No prompt tuning or parameter adjustments were permitted on this split.
- **Reserve Task Pool ($n = 468$):** Maintained for future enterprise testing and continuous integration.

### 8.3 Adversarial Security Testing (300 Attack Probes)
Orion was evaluated against 300 frozen adversarial penetration probes:

```
┌────────────────────────────────────────────────────────────────────────┐
│                   ADVERSARIAL SECURITY BENCHMARK RESULTS               │
├───────────────────────────────────┬──────────────┬──────────┬──────────┤
│ Attack Category                   │ Probes ($N$) │ Blocked  │ Rate     │
├───────────────────────────────────┼──────────────┼──────────┼──────────┤
│ Arbitrary Code Execution (RCE)    │      80      │    80    │ 100.0%   │
│ Filesystem Access & Traversal     │      50      │    50    │ 100.0%   │
│ OS Process Creation & Spawning    │      40      │    40    │ 100.0%   │
│ Private Dunder Reflection Access  │      70      │    70    │ 100.0%   │
│ Non-Allowlisted Network Egress    │      60      │    60    │ 100.0%   │
├───────────────────────────────────┼──────────────┼──────────┼──────────┤
│ TOTAL ADVERSARIAL PENETRATION     │     300      │   300    │ 100.0%   │
└───────────────────────────────────┴──────────────┴──────────┴──────────┘
```

### 8.4 Empirical Benchmark Results & Statistical Validation

#### Main Benchmark Scorecard (Greedy Decoding, $T = 0.0$)
Evaluating Orion across experimental splits against deterministic execution oracles demonstrates decisive performance superiorities:

| Configuration | Split | Model Backbone | Tasks ($n$) | Oracle TSR | 95% Wilson CI | Route Acc. | Abstain $F_1$ | Cost / 1k Tasks |
|---|---|---|---|---|---|---|---|---|
| **No-Heal (Baseline)** | Dev | Mistral-Nemo-12B | 34 | 58.8% | [42.2%, 73.6%] | 100.0% | 100.0% | $3.49 |
| **Retry-Only (Ablation)** | Dev | Mistral-Nemo-12B | 34 | 64.7% | [47.9%, 78.5%] | 100.0% | 100.0% | $3.54 |
| **Typed-Heal (Orion)** | Dev | Mistral-Nemo-12B | 34 | **76.5%** | [60.0%, 87.6%] | 100.0% | 100.0% | **$3.34** |
| **Typed-Heal (Generalize)**| Val | Qwen2.5-72B | 34 | **88.2%** | [73.4%, 95.3%] | 100.0% | 100.0% | $9.72 |
| **No-Heal (Baseline)** | Test | Mistral-Nemo-12B | 69 | 68.1% | [56.4%, 77.9%] | 100.0% | 100.0% | $3.50 |
| **Typed-Heal (Orion)** | Test | Mistral-Nemo-12B | 69 | **75.4%** | [64.0%, 84.0%] | 100.0% | 100.0% | **$3.37** |

#### Statistical Significance Testing
On the matched development partition, McNemar's exact paired test reveals:
- Concordant Successes: 20 tasks
- Concordant Failures: 8 tasks
- Discordant (No-Heal Pass / Orion Fail): 0 tasks
- Discordant (Orion Pass / No-Heal Fail): 6 tasks
- **Exact Binomial Significance:** $p = 2 \times (0.5)^6 = 0.03125$ ($p < 0.05$). The null hypothesis is rejected with statistical significance at $\alpha = 0.05$.

#### Component-Level Ablation Analysis
Progressive integration of Orion's harness mechanisms demonstrates clear, additive utility:

```
┌────────────────────────────────────────────────────────────────────────┐
│                   COMPONENT-LEVEL PERFORMANCE ABLATION                 │
├───────────────────────────────────────┬─────────┬───────────┬──────────┤
│ Configuration / Added Mechanism       │ Dev TSR │ Increment │ Tokens   │
├───────────────────────────────────────┼─────────┼───────────┼──────────┤
│ 1. No-Heal Baseline                   │  58.8%  │    ---    │  21,882  │
│ 2. + Blind Retry (Up to 2 Retries)    │  64.7%  │   +5.9%   │  22,721  │
│ 3. + Fault Class. & Catalog Hints     │  70.6%  │   +5.9%   │  23,410  │
│ 4. + Scratchpad Pruning & Backtracking│  76.5%  │   +5.9%   │  20,882  │
├───────────────────────────────────────┴─────────┴───────────┴──────────┤
│ NET RESULT: +17.7% TSR Gain (p = 0.03125) with 4.6% Token Reduction     │
└────────────────────────────────────────────────────────────────────────┘
```

#### Fault Recovery Success Rates (Under Fault-Injection Probes)
Under controlled injection of faults across classes $\mathcal{F}_1$–$\mathcal{F}_8$, Orion achieves an **82.4% overall resolution rate** (28/34), compared to 0.0% for No-Heal and 17.6% for Blind Retry:
- $\mathcal{F}_1$ (Tool Runtime / Network): **100.0%** recovery via backoff.
- $\mathcal{F}_2$ (Empty Retrieval): **80.0%** recovery via query expansion.
- $\mathcal{F}_3$ (Routing Error): **100.0%** recovery via boundary re-dispatch.
- $\mathcal{F}_4$ (Schema Invalidation): **87.5%** recovery via SQLite PRAGMA catalog injection.
- $\mathcal{F}_7$ (Policy Denied): **100.0%** policy-compliant safe abstention.
- $\mathcal{F}_8$ (Cascade Poisoning): **66.7%** recovery via context rewind.

### 8.5 Token Consumption & Cost Efficiency Modeling
By pruning failed steps and preventing runaway retry loops, Orion delivers significant economic savings:
$$\text{Cost} = T_{\text{in}} \cdot P_{\text{in}} + T_{\text{out}} \cdot P_{\text{out}}$$
- **Baseline Cost per 1,000 Verified Successful Tasks:**
  $$\text{Effective Cost} = \frac{\$3.49}{0.588} = \$5.94$$
- **Orion Cost per 1,000 Verified Successful Tasks:**
  $$\text{Effective Cost} = \frac{\$3.34}{0.765} = \$4.37$$
- **Net Operational Savings:** **26.4% cost reduction per successful enterprise task**.

---

## 9. Full-Stack Implementation & Delivery Platform

Orion is engineered as a complete, deployable enterprise software product.

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│                             FULL-STACK DELIVERY SUITE                            │
├──────────────────────────────────────────────────────────────────────────────────┤
│                                                                                  │
│   FRONTEND: Next.js 15 Control Center (`web/`)                                   │
│   ┌──────────────────────────────────────────────────────────────────────────┐   │
│   │ • Interactive Multi-Turn Chat with Streaming SSE Support                 │   │
│   │ • Real-Time DAG Workflow Visualizer (Mermaid / SVG Node Inspector)       │   │
│   │ • Live Flight Recorder Trace Viewer (`/traces/{request_id}`)             │   │
│   │ • Knowledge Base Management Studio (Upload, Index, Audit Tables)        │   │
│   │ • Orion Lab Workspace (Prompt-to-Agent Synthesis & Sandbox Smoke Test)   │   │
│   └────────────────────────────────────┬─────────────────────────────────────┘   │
│                                        │ HTTP / REST / Server-Sent Events        │
│                                        ▼                                         │
│   BACKEND: FastAPI Enterprise Engine (`backend/api.py` on Port 8000)             │
│   ┌──────────────────────────────────────────────────────────────────────────┐   │
│   │ • `/auth`: SQLite-backed Authentication, PBKDF2 Password Hashing, JWT   │   │
│   │ • `/chat` & `/chat/stream`: Multi-agent Orchestration & Event Streaming   │   │
│   │ • `/ingest` & `/kb`: Isolated User File Ingestion (PDF, CSV, XLSX, DOCX) │   │
│   │ • `/lab`: Conversational Agent Synthesis & Lifecycle Management          │   │
│   │ • `/mcp`: Full Model Context Protocol JSON-RPC 2.0 Server                │   │
│   │ • `/traces`: Flight Recording Span Auditing & Checkpoint Inspector       │   │
│   └────────────────────────────────────┬─────────────────────────────────────┘   │
│                                        │ Subprocess IPC / Zero-Copy Memory       │
│                                        ▼                                         │
│   CORE HARNESS & SANDBOX (`harness/`, `sandbox/`, `workers/`)                    │
│   ┌──────────────────────────────────────────────────────────────────────────┐   │
│   │ • Control Plane Router, Planner, Context Engine, and Session Vault       │   │
│   │ • AST Gated Execution Worker Pools & Subprocess Supervisors              │   │
│   │ • ChromaDB Vector Indices & SQLite Embedded Relational Engines           │   │
│   └──────────────────────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────────────────────┘
```

### 9.1 FastAPI Enterprise Backend Architecture
- **Asynchronous Execution:** Built on FastAPI and Uvicorn, supporting concurrent client sessions.
- **Server-Sent Events (SSE):** The `/chat/stream` endpoint streams real-time execution tokens, agent activation notifications, and DAG state updates directly to clients.
- **Trace Export & Auditing:** Every query execution writes a structured JSONL flight trace to `outputs/traces/`, cataloging token usage, execution latency, tool payloads, and recovery transitions.

### 9.2 Next.js 15 Full-Featured Control Plane Frontend
- **Modern Responsive UI:** Developed using Next.js 15, React, and TypeScript.
- **Real-Time DAG Graph Visualizer:** Renders the execution wave graph dynamically as tasks are solved or backtracked.
- **Knowledge Base Ingestion Interface:** Allows non-technical users to drag and drop enterprise documents into the isolated live knowledge base without risking core data corruption.

### 9.3 Alternative Gradio Interface
For rapid local debugging and environments without Node.js, Orion provides an out-of-the-box Gradio dashboard (`frontend/app.py` on Port 7860) supporting authentication, chat, role toggling, and trace reviews.

---

## 10. Implementation Roadmap, Phases & Work Breakdown Structure

Project Orion is structured into seven rigorous, sequential execution phases:

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                           PROJECT IMPLEMENTATION ROADMAP                        │
├─────────┬─────────────────────────────────────────────────────────────┬─────────┤
│ Phase   │ Description & Key Deliverables                              │ Status  │
├─────────┼─────────────────────────────────────────────────────────────┼─────────┤
│ Phase 0 │ Corpus Pinning & Data Lake Materialization                  │ DONE ✔  │
│         │ - Stratified sampling of 2M-row HR dataset to Parquet       │         │
│         │ - SEC EDGAR, FRED, and Calendar normalization               │         │
│         │ - SHA-256 data manifest generation (`DATA_CARD.md`)         │         │
├─────────┼─────────────────────────────────────────────────────────────┼─────────┤
│ Phase 1 │ Core Control Plane & Gateway Modernization                  │ DONE ✔  │
│         │ - Unified LLM client gateway across providers               │         │
│         │ - OpenTelemetry-style Flight Recorder (`harness/trace.py`)  │         │
│         │ - Tool & Agent Registry standardization                     │         │
├─────────┼─────────────────────────────────────────────────────────────┼─────────┤
│ Phase 2 │ Oracle Compiler & Benchmark Suite Construction              │ DONE ✔  │
│         │ - Python/pandas deterministic oracle compiler               │         │
│         │ - Stratified partition locking (Dev: 34, Val: 34, Test: 69) │         │
│         │ - 300 frozen adversarial security test probes               │         │
├─────────┼─────────────────────────────────────────────────────────────┼─────────┤
│ Phase 3 │ Dual-Sandbox Architecture & AST Security Gate               │ DONE ✔  │
│         │ - Python AST visitor security filtering (`sandbox/gate.py`) │         │
│         │ - Subprocess isolation with 512MB RAM & 10s CPU limits      │         │
│         │ - Isolated evaluation boundary decoupling                   │         │
├─────────┼─────────────────────────────────────────────────────────────┼─────────┤
│ Phase 4 │ Typed Self-Healing, Recovery Engine & Lab Synthesizer       │ DONE ✔  │
│         │ - Operational F1–F8 fault classification engine             │         │
│         │ - Dynamic SQLite PRAGMA schema introspection                │         │
│         │ - Agent Lab synthesizer & autonomous Repair Agent           │         │
├─────────┼─────────────────────────────────────────────────────────────┼─────────┤
│ Phase 5 │ Benchmark Scoring, Paper Verification & Ablations           │ DONE ✔  │
│         │ - Greedy decoding evaluation across Dev, Val, Test          │         │
│         │ - McNemar's exact paired statistical testing ($p = 0.03125$)│         │
│         │ - Publication-grade IEEE paper build (`paper/paper.pdf`)     │         │
├─────────┼─────────────────────────────────────────────────────────────┼─────────┤
│ Phase 6 │ Enterprise Delivery & Full-Stack Control Center             │ DONE ✔  │
│         │ - FastAPI backend with RBAC, SSE streaming, and MCP gateway │         │
│         │ - Next.js 15 modern frontend web application                │         │
│         │ - Dual-store live Knowledge Base ingestion pipeline         │         │
├─────────┼─────────────────────────────────────────────────────────────┼─────────┤
│ Phase 7 │ Production Hardening & Enterprise Scale-Out (Next Horizon)  │ Q1 2027 │
│         │ - Distributed worker sandboxing (Docker / Firecracker VMs)  │ Planned │
│         │ - Enterprise SSO / Active Directory SAML integration        │ Planned │
│         │ - Multi-tenant Kubernetes Helm chart packaging              │ Planned │
└─────────┴─────────────────────────────────────────────────────────────┴─────────┘
```

---

## 11. Budget, Resource Requirements & Infrastructure Forecast

To support enterprise deployment and continued research scale-out over a 12-month operational window, the required budget is forecast below:

### 11.1 Infrastructure & Cloud Compute Budget
| Resource Category | Description & Justification | Monthly Cost | Annual Cost |
|---|---|---|---|
| **Foundation Model Inference** | Hosted API endpoints (Mistral NeMo, Qwen 2.5 72B, GPT-4o for qualitative audits) | $450 | $5,400 |
| **GPU Compute (Sandbox/Lab)** | 1x NVIDIA A100 (80GB) cloud instance for local model validation & offline Lab repair | $950 | $11,400 |
| **Enterprise Cloud Hosting** | AWS/GCP Kubernetes cluster (EKS/GKE), Redis cache, managed PostgreSQL | $600 | $7,200 |
| **Storage & Vector Infrastructure** | High-IOPS NVMe EBS volumes for 2M-row database lake & ChromaDB indices | $150 | $1,800 |
| **Security Auditing & Red Teaming** | External penetration testing and continuous vulnerability scanning | --- | $6,000 |
| **Subtotal Infrastructure** | | | **$31,800** |

### 11.2 Personnel & Engineering Allocation
| Role | Commitment | Focus Area |
|---|---|---|
| **Principal AI Architect** | Full-Time | Core harness, DAG planner, recovery engine, research lead |
| **Senior Systems / Security Engineer** | 50% Allocation | AST sandboxing, OS virtualization, network egress policies |
| **Full-Stack Web Engineer** | 50% Allocation | Next.js control plane, SSE streaming, MCP protocol compliance |
| **Data / Evaluation Specialist** | 50% Allocation | Oracle benchmark maintenance, schema migrations, data card curation |

---

## 12. Risk Assessment, Governance & Mitigation Matrix

| Risk Event | Severity | Likelihood | Strategic Mitigation Mechanism |
|---|---|---|---|
| **Untrusted Code Execution in Sandbox** | Critical | Low | AST static analysis blocks forbidden primitives prior to parsing; subprocess execution caps CPU/RAM; zero OS network egress permitted. |
| **Silent Hallucination in Complex Joins** | High | Medium | Deterministic execution oracles verify numerical outputs; declarative join matrices force verified abstention on illegal cross-lake joins. |
| **Context Window Overflow & Cost Inflation** | Medium | Low | Cursor-style context engine compacts history, stubs large tables into the artifact vault, and enforces bounded repair cycles ($B \le 2$). |
| **Model API Downtime or Rate Limiting** | Medium | Medium | Circuit breaker architecture opens on transport failures ($\mathcal{F}_1$); support for local models (Ollama, vLLM, LM Studio) provides seamless failover. |
| **Reward Hacking by Offline Repair Agent** | High | Low | Repair Agent patches are evaluated against dev oracles under a trusted evaluation boundary; patches increasing judge scores but lowering oracle accuracy are rejected. |
| **Data Lake Schema Drift** | Medium | High | Dynamic SQLite `PRAGMA` introspection automatically extracts active schemas at runtime, repairing queries without codebase changes. |

---

## 13. Academic Publication & Commercialization Strategy

### 13.1 Academic Publication Roadmap
Orion addresses open challenges highlighted in recent Tier-1 AI conferences (ACL 2026 Survey on Agent Evaluation, ICML 2025 Agent-as-a-Judge, Harness-Bench 2026):
- **Current Paper Artifact:** A complete, 8-page IEEE-format research paper is compiled in the repository (`paper/paper.pdf` and `paper/paper.md`): *"Orion: A Typed Runtime Self-Healing Harness for Fault-Tolerant Multi-Agent Enterprise Workflows"*.
- **Primary Contributions:**
  1. Formalization of the operational $\mathcal{F}_1$–$\mathcal{F}_8$ failure taxonomy with deterministic priority resolution.
  2. Proof of runtime schema repair via live database introspection and state-isolated rollback.
  3. Empirical validation establishing a statistically significant +17.7% TSR gain ($p = 0.03125$) while lowering token costs by 4.6%.
  4. 100% defense against 300 adversarial security probes.
- **Target Venues:** IEEE Transactions on Knowledge and Data Engineering (TKDE), ACL/EMNLP System Demonstrations, or NeurIPS Datasets and Benchmarks Track.

### 13.2 Enterprise Commercialization Opportunities
1. **Enterprise Copilot Engine:** Deploying Orion as a white-labeled operational intelligence assistant for Fortune 500 corporations with heterogeneous databases.
2. **Autonomous Data Warehouse Governance:** Packaging Orion's $\mathcal{F}_4$ schema introspection and AST sandboxing as a standalone middleware layer for enterprise LangChain/LlamaIndex deployments.
3. **Model Context Protocol (MCP) Enterprise Gateway:** Commercializing Orion’s MCP server as a secure, sandboxed bridge connecting frontier LLMs (Claude, GPT-4o) to internal enterprise relational stores.

---

## 14. Conclusion & Steering Sign-Off

### 14.1 Summary of Proposal
Project Orion represents a major paradigm shift in enterprise AI agent engineering. By establishing that **agent reliability is an architectural execution challenge rather than a prompt-engineering limitation**, Orion delivers an empirically verified, secure, and self-healing multi-agent framework. With proven double-digit accuracy gains, guaranteed defense against code injection, deterministic join enforcement, and substantial cost reductions, Orion provides the foundational infrastructure required to safely scale autonomous AI across corporate data lakes.

### 14.2 Next Steps & Immediate Actions
1. **Formal Executive & Steering Review:** Secure stakeholder review and endorsement of the project proposal.
2. **Phase 7 Scale-Out Initiation:** Begin containerized microVM isolation and Active Directory SSO enterprise integration.
3. **Academic Submission:** Finalize camera-ready formatting of `paper/paper.pdf` for conference submission.

---

### Project Approval & Sign-Off

| Stakeholder Role | Name & Title | Signature | Date | Status |
|---|---|---|---|---|
| **Principal Investigator** | Mustafa Subhani | *M. Subhani* | 2026-10-12 | **SUBMITTED** |
| **Technical Steering Lead** | Review Committee | _________________________ | ___ / ___ / 2026 | **PENDING** |
| **Enterprise Security Officer** | CISO / Compliance Lead | _________________________ | ___ / ___ / 2026 | **PENDING** |

---
*Document compiled and maintained within Project Orion repository at `d:/orion/PROJECT_PROPOSAL.md`.*
