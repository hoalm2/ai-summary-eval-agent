# AI Summary Judge

Automated quality-control pipeline for Vietnamese stock-research AI summaries — catches hallucinations, factual errors, and buy-price violations before they reach end users.

---

## Why The Judge Exists

### The Problem

**AI Summary** is a product feature that auto-compresses analyst reports (PDF) into short bullet summaries for retail investors. When the AI gets it wrong, the consequences are real:

| Risk | Example |
|---|---|
| Factual error | Summary says EPS is 2,500đ; report says 2,200đ |
| Fabricated claim | Summary adds a conclusion not present in the report |
| Missing disclaimer | Report says "estimated, not confirmed"; summary drops the caveat |
| Buy price violation | Summary implies "buy now" — violates product rules |

### The Manual Workflow Didn't Scale

Before this project, quality review was fully manual: one PM/PO using NotebookLM + Claude chat + Excel, taking **15–20 minutes per summary**. It did not scale, produced no regression history, and had no automated guardrails.

### Who This Is For

**Primary user: PM/PO** responsible for daily AI summary quality review. The pipeline replaces the manual workflow — open the dashboard, see the verdict, intervene only when needed.

---

## How It Works

```
Supabase `reports` + `summaries`
  └─ Stage 0: ensure report_text (DB cache or extract from PDF)
        │
        ▼
  Stage 1 — Skeleton Extraction        [LLM: Gemini 3.1 Pro Preview]
  Extracts: thesis, key risks,
  financial highlights, disclaimers
        │
        ▼
  Stage 1b — Citation Alignment        [LLM: Gemini 3.1 Pro Preview]
  Each summary bullet → 1–3 verbatim
  quotes from the report as evidence
        │
        ├──────────────────────────────────────┐
        ▼                                      ▼
  Stage 3b — LLM Judge             Stage 3a — Deterministic Factcheck
  [LLM: Claude Sonnet 4.6]             [code: pipeline/factcheck.py]
  Input: full report + skeleton        Token-matches every number/date
  + eval checklist                     in summary vs. report — zero
  Output: blocks[], flags[]            false negatives on hard numbers
        │                                      │
        └──────────────┬────────────────────────┘
                       ▼
  Stage 3c — compute_verdict()   ← deterministic Python, not LLM
  PASS / FLAG / FAIL / ERROR
                       │
                       ▼
  Supabase `eval_runs`  →  /dashboard
```

**Why two judge layers?** The LLM judge catches semantic issues (tone, logic, fabrication) but can miss exact numeric discrepancies. The deterministic factcheck token-matches every number and date — no false negatives. The final verdict is always computed by code, never delegated to the LLM.

---

## Verdicts

| Verdict | Meaning |
|---|---|
| `PASS` | No issues — summary is faithful and within product rules |
| `FLAG` | One minor issue — usable but flagged for review |
| `FAIL` | At least one BLOCK issue, or ≥ 2 FLAGs — must not be published |
| `ERROR` | Pipeline could not evaluate — report text unreadable or judge output unparseable |

Full rubric → [docs/specs/daily-eval-workflow/eval-taxonomy.md](docs/specs/daily-eval-workflow/eval-taxonomy.md)

---

## Model Selection

| Stage | Model | Reason | Fallback | Priority |
|---|---|---|---|---|
| 0 — PDF Acquisition | *(no LLM)* | Pure text extraction + regex | — | N/A |
| 1 — Skeleton | `gemini/gemini-3.1-pro-preview` | Largest context window; best document grounding for long Vietnamese PDFs | `qwen3-5-27b` | HIGH |
| 1b — Citation | `gemini/gemini-3.1-pro-preview` | Must track verbatim quotes; same model as Stage 1 for context consistency | `qwen3-5-27b` | HIGH |
| 3a — Factcheck | *(no LLM)* | Pure regex/token matching — zero hallucination risk | — | N/A |
| 3b — Judge | `claude-sonnet-4-6` | Critical node — a parse error propagates to ERROR verdict for the whole record; Anthropic SDK used directly | `deepseek/deepseek-v4-pro` | CRITICAL |
| 3c — Verdict | *(logic code)* | `compute_verdict()` is deterministic Python — verdict never delegated to LLM | — | N/A |

Model swap requires only `MODEL_*` env var changes — no code changes.

### Production Readiness

The pipeline is designed to move to production with config changes only — no business logic rewrite needed:

| | Current (demo) | Production |
|---|---|---|
| LLM provider | GreenNode MaaS | Zalopay-provided AI model (env swap) |
| Data source | Public analyst PDFs + seeded fixtures | Internal DB `summary` + `pdf_path` |
| Trigger | Manual `/run-daily` call | Scheduled daily cron |
| Business logic | — | No changes |

---

## Dashboard

`/dashboard` is an HTML endpoint requiring no login — open directly in any browser.

**5 metric cards** — each shows current value vs. target threshold with color coding:

| Metric | Target |
|---|---|
| % Summary Fail | ≤ 2% |
| Hallucination rate (Type A + B issues) | ≤ 2% |
| Buy violations | = 0 |
| Format compliance | ≥ 95% |
| % Fail/Flag bullets per summary | ≤ 10% |

Clicking a card filters the detail view to that issue type.

**3 tabs:**

- **Overview** — failure pattern breakdown by issue type + latest batch summary table
- **Detailed report** — per-summary drill-down: verdict badge → issue list (BLOCK/FLAG) → explanation → summary quote → expandable report evidence; filterable by verdict, ticker, date
- **Trend** — % Summary Fail over time (7-day window, 15% threshold line)

---

## Quick Start (Local + Mock Mode)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, DEMO_TOKEN
MOCK_LLM_MODE=true uvicorn main:app --reload --port 8080
```

Seed demo fixtures:
```bash
python scripts/seed_demo_fixtures.py
```

Endpoints:
```bash
GET  http://localhost:8080/dashboard
GET  http://localhost:8080/status
POST http://localhost:8080/run-demo   -H "X-Demo-Token: $DEMO_TOKEN"
POST http://localhost:8080/run-daily  -H "X-Demo-Token: $DEMO_TOKEN"
```

`MOCK_LLM_MODE=true` — Supabase and dashboard run for real; LLM calls are mocked. Zero token burn during development.

---

## Doc Index

Read in this order to understand the project from why → what → how:

| # | Doc | Read when |
|---|---|---|
| 1 | [docs/specs/daily-eval-workflow/overview.md](docs/specs/daily-eval-workflow/overview.md) | Understand the full pipeline, model selection, dashboard design, and design decisions — **read this before anything else** |
| 2 | [docs/specs/daily-eval-workflow/eval-taxonomy.md](docs/specs/daily-eval-workflow/eval-taxonomy.md) | Full issue rubric (A_*, B_*, buy_price_*), verdict rules, and product quality thresholds |
| 3 | [docs/specs/daily-eval-workflow/pipeline.md](docs/specs/daily-eval-workflow/pipeline.md) | Tech: stage-by-stage implementation, input/output contracts, error paths, tech stack |
| 4 | [docs/specs/daily-eval-workflow/data-model.md](docs/specs/daily-eval-workflow/data-model.md) | Tech: Supabase schema and data lifecycle |

Operational reference (not required reading):

| Doc | Use for |
|---|---|
| [docs/runbook-seed-reports.md](docs/runbook-seed-reports.md) | How to seed reports and pre-created summaries into Supabase |

---

## Architecture Details

- Supabase Postgres is the durable source of truth: `reports`, `summaries`, `eval_runs`, `agent_state`
- `report_text` is extracted once and cached in DB — daily runtime does not re-download PDFs
- `/run-daily` skips any summary that already has an `eval_runs` row — no duplicate evaluation
- `agent_state` holds the pipeline kill switch (`pipeline_enabled`) and last batch metadata
