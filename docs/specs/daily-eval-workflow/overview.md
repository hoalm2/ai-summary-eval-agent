# AI Summary Judge — Overview

> For both technical and non-technical readers. Read this before the spec details or code.

---

## The Problem

**AI Summary** is a product feature that auto-compresses analyst reports (PDF) into short bullet summaries for retail investors. When the AI gets it wrong, the consequences are direct:

| Risk | Example |
|---|---|
| Factual error | Summary says EPS is 2,500đ; report says 2,200đ |
| Fabricated claim | Summary adds a conclusion not present in the report |
| Missing disclaimer | Report says "estimated, not confirmed"; summary drops the caveat |
| Buy price violation | Summary implies "buy now" — violates product rules |

Before this project, catching these issues was fully manual: one PM/PO reviewing each summary with NotebookLM + Claude chat + Excel, taking **15–20 minutes per summary**. That workflow does not scale, produces no regression history, and has no automated guardrail to catch safety violations before they reach users.

---

## The Solution

The AI Summary Judge automates the daily review cycle. It receives pre-created summaries from Supabase, judges each one against the source analyst report using a two-layer check, assigns a final verdict (`PASS` / `FLAG` / `FAIL` / `ERROR`), and surfaces all results in a dashboard the PM/PO can open without logging in.

The pipeline replaces the manual review loop — the PM/PO intervenes only when the dashboard shows a failure that needs investigation.

---

## How It Works

```
Supabase `reports` + `summaries`
  └─ Stage 0: ensure report_text (DB cache or extract from PDF)
        │
        ▼
  Stage 1 — Skeleton Extraction      [LLM: Gemini 3.1 Pro Preview]
  Extracts: thesis, key risks,
  financial highlights, disclaimers
        │
        ▼
  Stage 1b — Citation Alignment      [LLM: Gemini 3.1 Pro Preview]
  Each summary bullet → 1–3 verbatim
  quotes from the report as evidence
        │
        ├──────────────────────────────────────┐
        ▼                                      ▼
  Stage 3b — LLM Judge               Stage 3a — Deterministic Factcheck
  [LLM: GPT-5 Mini]                       [code: pipeline/factcheck.py]
  Input: full report text +          Token-matches every number/date
  skeleton hint + eval checklist     in summary vs. report — zero
  Output: blocks[], flags[]          false negatives on hard numbers
        │                                      │
        └──────────────┬────────────────────────┘
                       ▼
  Stage 3c — compute_verdict()   ← deterministic Python, not LLM
  PASS / FLAG / FAIL / ERROR
                       │
                       ▼
  Supabase `eval_runs`  →  /dashboard
```

**Why two judge layers?** The LLM judge catches semantic issues — tone escalation, logic conflicts, fabricated claims, missing disclaimers. But it can miss exact numeric discrepancies and occasionally produces false positives. The deterministic factcheck has no false negatives on hard numbers: it token-matches every number and date in the summary against the full report text. The final verdict is always computed by `compute_verdict()` in code — never delegated to LLM discretion.

**Why a skeleton extraction step first?** Extracting thesis, risks, and financial highlights into a structured artifact before judging reduces self-evaluation bias (the judge doesn't re-read its own extracted context). The skeleton is independently auditable and can be inspected separately from the judge output.

---

## Verdicts

| Verdict | Meaning |
|---|---|
| `PASS` | No issues — summary is faithful and within product rules |
| `FLAG` | One minor issue (truncation, missing caveat, format) — usable but flagged for review |
| `FAIL` | At least one BLOCK issue, or ≥ 2 FLAGs — must not be published |
| `ERROR` | Pipeline could not evaluate — report text unreadable or judge output unparseable |

See [eval-taxonomy.md](eval-taxonomy.md) for the full issue category rubric and disambiguation rules.

---

## Dashboard — Built Around the PM/PO's Daily Needs

The `/dashboard` HTML endpoint is the primary interface — no login required. Its design is driven by four jobs the PM/PO needs to do every day:

| # | Job | Why it matters |
|---|---|---|
| J1 | **Safety gate** — know immediately if any summary crossed a product rule (buy price or Type A/B hallucination) | Prevents safety violations from reaching users |
| J2 | **Daily health check** — confirm pass/fail rates are within threshold in one glance, without reading individual summaries | Replaces the 15–20 min/summary manual review |
| J3 | **Root cause drill-down** — navigate from batch → failed summary → failed bullet → source evidence | Cuts investigation time from hours to minutes |
| J4 | **Trend monitoring** — see quality direction across multiple batches | Enables data-driven decisions on model/prompt upgrades |

These four jobs map directly to what the dashboard shows:

- **5 metric cards** (J1 + J2) — each card shows current value vs. target threshold with color coding: % Summary Fail (≤ 2%), Hallucination rate (≤ 2%), Buy violations (= 0), Format compliance (≥ 95%), % Fail/Flag bullets (≤ 10%). Clicking a card filters the detail view to that issue type.
- **Overview tab** (J2) — failure pattern breakdown by issue type + latest batch summary table
- **Detailed report tab** (J3) — per-summary drill-down: verdict badge → issue list → explanation → summary quote → expandable verbatim report evidence; filterable by verdict, ticker, date
- **Trend tab** (J4) — % Summary Fail over time (7-day window, 15% threshold line)

Full `report_text` is never exposed in any endpoint.

---

## Design Decisions

The jobs above — and the pipeline reliability goal of never leaving verdict authority to an LLM — drove these decisions:

| Decision | How it serves the goals |
|---|---|
| Separate skeleton extraction before judging (Stage 1) | Prevents self-evaluation bias; skeleton is independently auditable (J3) |
| Deterministic factcheck alongside LLM judge (Stage 3a + 3b) | LLM alone misses exact numeric discrepancies; code-level token match has no false negatives — critical for J1 safety gate |
| Three-verdict system + ERROR | Binary pass/fail loses signal; Likert 1–5 is hard to calibrate for a solo reviewer; ERROR separates operational failures from quality failures (J2) |
| `compute_verdict()` in code is the sole authority | Verdict is never delegated to LLM — consistent, auditable, no prompt-drift risk |
| Stage 1b citation alignment | Provides the per-bullet evidence citations that make J3 drill-down possible; without it, investigation stops at summary level |
| Supabase Postgres as storage | Durable, queryable history for J4 trend monitoring; avoids flat-file edge cases on container restarts |
| Prompts in `/prompts/*.md`, separate from code | Prompt changes do not require code changes — independently versioned and iterable |

---

## Model Selection

Each stage uses a model chosen for its specific failure mode risk. Priority reflects how much a failure at that stage degrades the final verdict.

| Stage | Model | Reason | Fallback | Priority |
|---|---|---|---|---|
| 0 — PDF Acquisition | *(no LLM)* | Pure text extraction + regex validation | — | N/A |
| 1 — Skeleton | `gemini/gemini-3.1-pro-preview` | Largest context window; best document grounding for long Vietnamese PDFs | `qwen3-5-27b` | **HIGH** |
| 1b — Citation | `gemini/gemini-3.1-pro-preview` | Must track verbatim quotes from report; same model as Stage 1 for context consistency | `qwen3-5-27b` | **HIGH** |
| 3a — Factcheck | *(no LLM)* | Pure regex/token matching — zero hallucination risk | — | N/A |
| 3b — Judge | `openai/gpt-5-mini` | Critical node — a parse error propagates to ERROR verdict for the whole record | `deepseek/deepseek-v4-pro` | **CRITICAL** |
| 3c — Verdict | *(logic code)* | `compute_verdict()` is deterministic Python — verdict never delegated to LLM | — | N/A |

GPT-5 models route through the **Responses API** (non-streaming, `reasoning: medium`). All other LLM calls use Chat Completions. Model swap requires only `MODEL_*` env var changes — no code changes.

---

## Production Path

The pipeline is built on a **build-once, swap-config** principle: business logic, prompts, eval checklist, and verdict rules are fully decoupled from provider and data source. Moving to production requires only:

- Point `MODEL_*` env vars at the Zalopay-provided AI model
- Point the data source at the internal DB that already holds pre-created summaries and their source PDFs
- Enable a scheduled daily trigger instead of the manual `/run-daily` call

No changes to pipeline stages, judging logic, or dashboard.

---

## Launch Criteria

Fixed before review — not adjusted after results are in:

- Pass rate ≥ 95% on full launch sample
- Zero Type A or Type B hallucinations on adversarial sample
- Buy violation count = 0
- Demo sample includes ≥ 5 report/summary pairs with at least 1–2 intentional failures

---

## Guiding Principles

- **Source report is ground truth.** The system judges summary faithfulness, not whether the report itself is correct.
- **Skeleton is an audit checkpoint, not authority.** If skeleton and full report conflict, the full report wins.
- **Avoid double-hallucination.** Use full report text, deterministic checks, and code-computed verdicts instead of relying only on one LLM judge.
- **Token-safe by default.** Use `MOCK_LLM_MODE=true` during development and only run real LLM calls on tightly scoped validation.
- **Build once, swap config.** Keep business logic independent from deployment infra and future model/provider changes.
- **Demo before scale.** Prove correctness on fixtures and a small cloud run before expanding to full batches.
