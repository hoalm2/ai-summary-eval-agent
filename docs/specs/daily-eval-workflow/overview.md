# AI Summary Eval Agent — Overview

> For both technical and non-technical readers. Read this before the spec details or code.

---

## What This Is

The AI Summary Eval Agent is an automated quality-control pipeline for Vietnamese stock-research AI summaries. Every day it reads source analyst reports, generates bullet-point summaries, then judges each summary against the original report using a two-layer check: an LLM judge and a deterministic code-level fact verifier. Results are stored in Supabase and surfaced through a single dashboard endpoint that any stakeholder can open without logging in.

The agent replaces a fully manual PO review workflow (NotebookLM + Claude chat + Excel) that took 15–20 minutes per summary, could not scale, and produced no durable regression history.

---

## Problem

**AI Summary** is a feature that automatically compresses analyst reports (PDF) into short bullet summaries for end users. Core risks that must be controlled daily:

| Risk | Example |
|---|---|
| Factual error | Summary says EPS is 2.500 đ; report says 2.200 đ |
| Fabricated claim | Summary adds a conclusion not in the report |
| Missing disclaimer | Report says "dự kiến, chưa chắc chắn"; summary drops the caveat |
| Buy price violation | Summary implies "nên mua ngay" — violates product rules |

Manual review does not scale past one reviewer at 15–20 min/summary, and produces no history to detect regressions over time.

---

## How It Works

```
Supabase `reports` + `summaries` tables
  └─ Stage 0: ensure report_text (DB or PDF extract)
  └─ fetch pre-existing summary_text
        │
        ▼
  Stage 1 — Skeleton Extraction      [LLM: Gemini 3.1 Pro Preview]
  Extracts: thesis points, key risks,
  financial highlights, disclaimers
        │
        ▼
  Stage 1b — Citation Alignment      [LLM: Gemini 3.1 Pro Preview]
  For each summary bullet: finds 1–3
  verbatim quotes from report as evidence
        │
        ├──────────────────────────────────────┐
        ▼                                      ▼
  Stage 3b — LLM Judge               Stage 3a — Deterministic Factcheck
  [LLM: GPT-5 Mini]                       [code: pipeline/factcheck.py]
  Input: full report text +          Extracts all numbers / dates
  skeleton hint + eval checklist     from both texts; flags any
  Output: blocks[], flags[]          token in summary not in report;
                                     checks upside % for timestamp
        │                                      │
        └──────────────┬────────────────────────┘
                       ▼
  Stage 3c — Merge → compute_verdict()
  → PASS / FLAG / FAIL / ERROR
                       │
                       ▼
  Persist to Supabase (`summaries` + `eval_runs`)
                       │
                       ▼
  /dashboard — aggregate metrics + per-summary detail
```

> Stage 2 (Summary Generation, GPT-5 Mini) is a **contest shim only** — not in the main `/run-daily` flow. `/run-daily` uses pre-seeded summaries from the `summaries` table.

### Why two judging layers?

The LLM judge catches semantic issues (tone, logic, fabrication, disclaimer omission) but can miss exact numeric discrepancies or produce false positives. The deterministic factcheck has no false negatives on hard numbers: it token-matches every number and date in the summary against the full report text. The final verdict is always computed by code — never left to LLM discretion.

### Why a skeleton extraction step?

Extracting the skeleton first (thesis, risks, financial highlights) and feeding it as a structured hint to Stage 3 reduces bias from the judge re-reading its own extracted context. The skeleton is an auditable artifact: it can be inspected independently, and the judge can be re-run with a different prompt without re-running PDF extraction.

---

## Verdicts

| Verdict | Meaning |
|---|---|
| `PASS` | No issues found — summary is faithful and within product rules |
| `FLAG` | One minor issue (truncation, missing caveat, or format) — usable but flagged for review |
| `FAIL` | At least one BLOCK issue, or two or more FLAG issues — summary must not be published |
| `ERROR` | System could not evaluate — report text unreadable or judge output unparseable |

See [eval-taxonomy.md](eval-taxonomy.md) for the full issue category rubric and disambiguation rules.

---

## Deployment Context

### Contest (deadline 17 June 2026)

| Item | Value |
|---|---|
| Platform | GreenNode AgentBase |
| Compute | 3 × OpenClaw instances (2 vCPU / 4 GB RAM each) |
| LLM provider | GreenNode MaaS — OpenAI-compatible endpoint |
| Storage | Supabase Postgres |
| Deploy method | GitHub public repo → AgentBase Docker build |
| Data source | Public analyst report PDFs + seeded demo fixtures |
| Token budget | 5,000,000 credits ÷ ~6,500 tokens/run ≈ 770 full pipeline runs |

### Production (post-contest, config swap only)

| Item | Change |
|---|---|
| LLM provider | Replace `GREENNODE_API_KEY` / `GREENNODE_BASE_URL` with Anthropic Claude API |
| Data source | Internal DB `summary` + `pdf_path` from daily cron job |
| Business logic | No changes — same pipeline, prompts, eval checklist, verdict rules |

The design principle is **build once, swap config**: business logic is fully decoupled from provider and infra.

---

## Model Selection

Each stage uses a model chosen for its specific requirements. Priority reflects how much a failure at that stage degrades the final eval quality.

| Stage | Name | Model | Reason | Fallback | Priority |
|---|---|---|---|---|---|
| 0 | PDF Acquisition | — (no LLM) | Pure text extraction + regex validation | — | N/A |
| 1 | Skeleton Extraction | `gemini/gemini-3.1-pro-preview` | Largest context window; best document grounding for long Vietnamese PDFs | `qwen3-5-27b` | **HIGH** |
| 1b | Citation Alignment | `gemini/gemini-3.1-pro-preview` | Must track verbatim quotes from report; same model as Stage 1 for context consistency | `qwen3-5-27b` | **HIGH** |
| 2 | Summary Generation *(contest shim)* | `openai/gpt-5-mini` | Strong instruction-following; structured Vietnamese bullet output | `qwen3-5-27b` | MED |
| 3a | Deterministic Factcheck | — (no LLM) | Pure regex/token matching — zero hallucination risk | — | N/A |
| 3b | LLM Judge | `openai/gpt-5-mini` | Critical node — a parse error propagates to ERROR verdict for the whole record | `deepseek/deepseek-v4-pro` | **CRITICAL** |
| 3c | Merge & Verdict | — (logic code) | `compute_verdict()` is deterministic Python — verdict never delegated to LLM | — | N/A |
| P | Persist | — (DB write) | Supabase insert/update | — | N/A |

GPT-5 models (`openai/gpt-5-mini`) route through the **Responses API** (non-streaming, `reasoning: medium`). All other LLM calls use GreenNode MaaS Chat Completions. Model swap requires only `MODEL_*` env var changes — no code changes.

---

## Dashboard & HTML Report

The `/dashboard` HTML endpoint is the primary interface for PM and stakeholders to monitor AI output quality — no login required. The report design is driven by six Jobs to Be Done (JTBDs).

---

### Jobs to Be Done

| # | JTBD | Priority | Value Delivered |
|---|---|---|---|
| J1 | **Safety gate** — Know immediately if any summary crossed a product rule (buy price or Type A/B hallucination) before it reaches end users | P0 | Prevents regulatory risk and user trust damage from reaching production |
| J2 | **Daily health check** — Confirm pass rate ≥ 85% and hallucination rate ≤ 2% in one glance, without reading individual summaries | P0 | Replaces 15–20 min/summary manual review with a single threshold comparison |
| J3 | **Root cause drill-down** — Navigate from batch → failed summary → failed bullet → source evidence to understand exactly what went wrong | P1 | Cuts root cause investigation from hours to minutes; no need to re-read source PDFs |
| J4 | **Trend monitoring** — See quality trend across multiple batches to support weekly stakeholder updates | P2 | Enables data-driven model/prompt upgrade decisions; catches regressions early |
| J5 | **Demo & auditability** — Show concrete pass/fail examples with visible reasoning chains to contest judges or executives | P2 | Makes the eval system auditable and trustworthy to non-technical stakeholders |
| J6 | **Demo & auditability** — Show concrete pass/fail examples with visible reasoning chains to contest judges or executives | P2 | Makes the eval system auditable and trustworthy to non-technical stakeholders |

---

### Design Implications

Each JTBD drives a concrete constraint on the report:

- **J1 → Safety violations appear above the fold.** Buy price issues and Type A/B hallucinations must live in a persistent banner — never buried in a scrollable list. Banner collapses to a green bar when the batch is clean.
- **J2 → Show threshold gap, not just a number.** Pass rate must show current value vs. the 85% target with a clear color signal. Same for hallucination rate vs. 2% and creation success vs. 98%.
- **J3 → Three-level navigation is required.** Batch → summary → bullet. Stage 1b (citation alignment) provides the per-bullet evidence citations that make drill-down possible. Without Stage 1b, drill-down stops at the summary level.
- **J4 → Multi-batch sparkline is required.** A single batch provides no direction signal. Minimum 7 batches of pass rate history for the trend line to be meaningful.
- **J5 → Judge reasoning must be readable inline.** The `rationale` field from the LLM judge and `explanation` from the deterministic factcheck must be visible without opening a separate modal. Skeleton JSON and bullet citations are expandable in place.

---

### Report Structure

Six sections in priority order — higher sections are always visible without scrolling:

#### Section 1 — Safety Alert Banner *(J1)*

Red banner if any eval run in the batch contains `buy_price_*`, `A_*`, or `B_*` BLOCK issues. Shows a general risk summary only (e.g. "Batch contains safety violations — do not publish until reviewed") — does not expose specific tickers or issue category names. Collapses to a green "No safety violations" bar when the batch is clean.

#### Section 2 — Quality Threshold Dashboard *(J2)*

Four metric cards, each showing current value vs. target threshold with color coding:

| Metric | Target | Source |
|---|---|---|
| Pass rate | ≥ 85% | eval_runs.verdict counts |
| Hallucination rate | ≤ 2% | runs with any A_* or B_* block (excl. B_tone_escalation) ÷ total |
| Buy violations | 0 | runs with any buy_price_* block |
| Creation success rate | ≥ 98% | (total − ERROR) ÷ total |

#### Section 3 — Failure Pattern Analysis *(J4)*

Top failure types ranked by frequency this batch. For each type:
- Count and % of total failures
- Suggested fix: specific prompt file or workflow step responsible, with a concrete change recommendation

#### Section 4 — Latest Eval Runs *(J3)*

Filterable list (by verdict, ticker, date range). Each row shows:
- Verdict badge (PASS / FLAG / FAIL / ERROR)

- Ticker + report date
- Expandable issue list: per-bullet breakdown with issue category, summary quote, source evidence citation from Stage 1b

#### Section 5 — Historical Trend *(J5)*

Pass rate sparkline over last N batches. Shows 7-batch moving average and flags any batch below the 85% threshold.

#### Section 6 — Walk-through Examples *(J6)*

One PASS and one FAIL example with all reasoning expanded: skeleton JSON, per-bullet evidence citations, LLM judge rationale, deterministic factcheck findings. Intended for contest demo and stakeholder audit.

---

Full `report_text` is never exposed in any endpoint.

---

## Token Budget

| Stage | Model | Estimated tokens |
|---|---|---|
| Stage 1 — skeleton | Gemini 3.1 Pro Preview | ~4,600 (long PDF input + JSON output) |
| Stage 1b — alignment | Gemini 3.1 Pro Preview | ~5,000 (report + summary → bullet citations) |
| Stage 3b — judge | GPT-5 Mini | ~5,700 (report + skeleton + checklist) |
| Stage 2 — summary *(contest shim)* | GPT-5 Mini | ~4,400 (not in main flow) |
| **Total per record (main flow)** | | **~15,300** |

### QC strategy — do not burn tokens before validation

1. **Development:** run with `MOCK_LLM_MODE=true` — Supabase and dashboard still run for real, LLM calls are mocked. Zero GreenNode tokens.
2. **First cloud deploy:** keep `MOCK_LLM_MODE=true`; verify health, status, and dashboard with seeded fixtures.
3. **First real validation:** set `MOCK_LLM_MODE=false` and process exactly **one** report. Inspect the eval run manually before expanding.
4. **Human calibration:** PO reviews 5 verdicts to confirm judge quality before running the full demo batch.

Never run multi-report GreenNode loops until prompts have been validated manually first.

---

## Launch Criteria

Criteria are fixed before review — not adjusted after results are in.

- Pass rate ≥ 85% on full launch sample
- Zero Type A or Type B hallucinations on adversarial sample
- Buy violation count = 0
- Demo sample includes ≥ 5 report/summary pairs with at least 1–2 intentional failures

---

## Key Design Decisions

| Decision | Rationale |
|---|---|
| Separate skeleton extraction (Stage 1) before judging | Prevents self-evaluation bias; skeleton is an independently auditable artifact |
| Deterministic factcheck alongside LLM judge | LLM alone misses exact numeric discrepancies; code-level token match has no false negatives on numbers and dates |
| Three-verdict system + ERROR | Binary pass/fail loses signal; Likert 1–5 is hard to calibrate for a solo reviewer; ERROR distinguishes operational failures from quality failures |
| `compute_verdict()` in code is the authority | Verdict is never delegated to LLM discretion; consistent and auditable |
| Supabase Postgres as storage | Durable, queryable history; avoids flat-file persistence edge cases with Docker restarts |
| Sequential batch processing | 3 × 4 GB RAM instances insufficient for parallel PDF parsing |
| `MOCK_LLM_MODE=true` as development default | Prevents accidental token burn; all Supabase and dashboard behavior still runs for real |
| Prompts in `/prompts/*.md`, separate from code | Prompt changes do not require code changes; independently versioned |
| Single HTML endpoint for dashboard | No extra infra; contest judges and PO can see output immediately |

---

## Out of Scope (Contest)

- Internal company DB integration or private/PII data
- Dashboard authentication or multi-tenant access
- Real-time streaming output
- Parallel batch processing
- Email / Slack alerts
- Complex retry orchestration
- Scheduled cron trigger (manual `/run-daily` trigger for contest demo)
