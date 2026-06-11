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
Supabase `reports` table
  └─ ensure report_text (stored text, or extracted from public PDF)
        │
        ▼
  Stage 1 — Skeleton Extraction                    [LLM: Qwen]
  Extracts: thesis points, key risks,
  financial highlights, disclaimers
        │
        ▼
  Stage 2 — Summary Generation                     [LLM: Qwen]
  Produces: Vietnamese bullet summary (≤ 5 bullets)
        │
        ├──────────────────────────────────────┐
        ▼                                      ▼
  Stage 3 — LLM Judge                         Deterministic Factcheck
  [LLM: Gemma]                                [code: pipeline/factcheck.py]
  Input: full report text +                   Extracts all numbers / dates
  skeleton hint + eval checklist              from both texts; flags any
  Output: blocks[], flags[]                   token in summary not in report;
                                              checks upside % for timestamp
        │                                      │
        └──────────────┬────────────────────────┘
                       ▼
  Merge issues → compute_verdict()
  → PASS / PASS-WITH-FLAG / FAIL / ERROR
                       │
                       ▼
  Persist to Supabase (`summaries` + `eval_runs`)
                       │
                       ▼
  /dashboard — aggregate metrics + per-summary detail
```

### Why two judging layers?

The LLM judge catches semantic issues (tone, logic, fabrication, disclaimer omission) but can miss exact numeric discrepancies or produce false positives. The deterministic factcheck has no false negatives on hard numbers: it token-matches every number and date in the summary against the full report text. The final verdict is always computed by code — never left to LLM discretion.

### Why a skeleton extraction step?

Extracting the skeleton first (thesis, risks, financial highlights) and feeding it as a structured hint to Stage 3 reduces bias from the judge re-reading its own extracted context. The skeleton is an auditable artifact: it can be inspected independently, and the judge can be re-run with a different prompt without re-running PDF extraction.

---

## Verdicts

| Verdict | Meaning |
|---|---|
| `PASS` | No issues found — summary is faithful and within product rules |
| `PASS-WITH-FLAG` | One minor issue (truncation, missing caveat, or format) — usable but flagged for review |
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

## Models

| Stage | Model | Reason |
|---|---|---|
| Stage 1 — skeleton | `qwen3-5-27b` | Strongest available for Vietnamese + financial reasoning + structured JSON output |
| Stage 2 — summary | `qwen3-5-27b` | Consistent language quality across extraction and generation |
| Stage 3 — judge | `gemma-4-31b-it` | Sufficient for the classification task; faster and cheaper than Qwen |
| Fallback | `MiniMax-M2.5` | Invoked if primary model times out |

All models are called via OpenAI-compatible chat completions. Swapping providers requires only env var changes.

---

## Dashboard

Two views in one `/dashboard` HTML endpoint — no login required:

**Aggregate metrics:** total evaluated, PASS / FAIL / PASS-WITH-FLAG / ERROR counts, pass rate, hallucination count and rate, buy violation count, failure breakdown by issue category.

**Per-summary detail list:** ticker, report date, verdict badge, generated summary text, issue descriptions, expandable skeleton JSON and judge JSON. Filterable by verdict, ticker, and date.

Full `report_text` is never exposed in any endpoint.

---

## Token Budget

| Stage | Model | Estimated tokens |
|---|---|---|
| Stage 1 — skeleton | Qwen | ~3,000 (long PDF input, medium JSON output) |
| Stage 2 — summary | Qwen | ~1,500 (short output) |
| Stage 3 — judge | Gemma | ~2,000 (summary + skeleton + checklist) |
| **Total per report** | | **~6,500** |

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
- No single failure type accounts for > 30% of total failures (signals a systematic prompt bug)
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
