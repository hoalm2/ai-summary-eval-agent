# Data Model — Supabase Schema & Data Lifecycle

Covers the four Supabase tables, field-level contracts, and how data flows through each pipeline stage. Read alongside [pipeline.md](pipeline.md) for the full picture.

---

## Tables Overview

```
reports          ── source analyst reports (one row per report)
summaries        ── AI-generated summaries (one row per generation run)
eval_runs        ── evaluation results (one row per eval attempt)
agent_state      ── key-value store for pipeline run metadata
```

`eval_runs` references both `reports` and `summaries`. A report can have multiple eval runs (re-runs). A summary can have at most one eval run per generation.

---

## `reports`

Stores analyst report metadata and text. Populated via `POST /reports/import`.

| Column | Type | Nullable | Notes |
|---|---|---|---|
| `id` | uuid | No | Primary key, auto-generated |
| `ticker` | text | Yes | Stock ticker (e.g. `"VTP"`) |
| `report_date` | date | Yes | Report publication date |
| `source_pdf_url` | text | Yes | Public PDF URL; host must be in `ALLOWED_PDF_HOSTS` |
| `pdf_storage_path` | text | Yes | Local path inside `data/`; used as fallback |
| `report_text` | text | Yes | Full extracted text; populated on import or on-demand PDF extraction |
| `status` | text | No | See status values below |
| `created_at` | timestamptz | No | Auto-set by Supabase |

### `status` values

| Value | Set when |
|---|---|
| `"ready"` | `report_text` is present and long enough; import default when text provided |
| `"pending"` | Imported with `source_pdf_url` only — text not yet extracted |
| `"extract_failed"` | PDF fetch or parse failed |
| `"extract_too_short"` | PDF extracted but text below `REPORT_TEXT_MIN_CHARS` |
| `"report_text_too_short"` | `report_text` provided on import but below minimum length |

### Deduplication

`find_existing_report()` checks for duplicates before insert. Match logic:
1. By `source_pdf_url` (exact match) if provided
2. By `(ticker, report_date)` pair otherwise

When `skip_existing=true` (default), duplicate reports are skipped silently.

### Security

`report_text` is never returned by `/results` or rendered in `/dashboard`. The `/results` endpoint returns only `id, ticker, report_date, source_pdf_url, status` from the reports join. The dashboard hides full text explicitly.

---

## `summaries`

Stores pre-created or generated summaries. One row per summary to evaluate. In the production-like daily flow, summaries are seeded before `/run-daily`; Stage 2 generation remains only as a contest/demo shim.

| Column | Type | Nullable | Notes |
|---|---|---|---|
| `id` | uuid | No | Primary key |
| `report_id` | uuid | No | FK → `reports.id` |
| `summary_text` | text | No | Vietnamese bullet summary to evaluate |
| `summary_model` | text | No | Source/model name, e.g. `"precreated"`, `"mock_llm"`, or model ID |
| `created_at` | timestamptz | No | Auto-set |

### `summary_model` values

| Value | Condition |
|---|---|
| `"precreated"` | Summary was imported/seeded before eval |
| `"qwen3-5-27b"` (or current model) | Real GreenNode run |
| `"mock_llm"` | `MOCK_LLM_MODE=true` |
| `"unexpected_error"` | Stage 2 raised an unexpected exception |
| `"extract_failed"` / `"extract_too_short"` | Report text could not be obtained |

---

## `eval_runs`

Stores one evaluation result per pipeline run attempt. A report that fails PDF extraction still gets an eval run with `verdict = "ERROR"` so failures are auditable.

| Column | Type | Nullable | Notes |
|---|---|---|---|
| `id` | uuid | No | Primary key |
| `report_id` | uuid | No | FK → `reports.id` |
| `summary_id` | uuid | No | FK → `summaries.id` |
| `skeleton_json` | jsonb | No | Stage 1 output; `{}` on ERROR |
| `judge_json` | jsonb | No | Stage 3 LLM output; `{"verdict": "ERROR", "rationale": "..."}` on ERROR |
| `verdict` | text | No | `PASS` \| `PASS-WITH-FLAG` \| `FAIL` \| `ERROR` |
| `blocks` | jsonb | No | Array of BLOCK issue objects; `[]` on PASS or ERROR |
| `flags` | jsonb | No | Array of FLAG issue objects; `[]` on PASS or ERROR |
| `created_at` | timestamptz | No | Auto-set |

### `skeleton_json` schema

```json
{
  "ticker": "VTP",
  "report_date": "2026-06-01",
  "thesis_points": ["Luận điểm đầu tư chính..."],
  "key_risks": ["Rủi ro thị trường..."],
  "financial_highlights": ["Doanh thu Q1 tăng 15%..."],
  "disclaimers": ["Báo cáo mang tính tham khảo..."],
  "notes": ""
}
```

All fields are arrays of strings except `ticker`, `report_date`, and `notes`. Empty arrays `[]` are valid.

### Issue object schema (in `blocks` and `flags`)

```json
{
  "category": "A_factual",
  "summary_quote": "EPS 2.500 đ",
  "report_evidence": "not present in report",
  "explanation": "Số liệu/ngày trong summary không xuất hiện trong report theo kiểm tra deterministic.",
  "source": "deterministic_factcheck"
}
```

| Field | Type | Notes |
|---|---|---|
| `category` | string | Code name from [eval-taxonomy.md](eval-taxonomy.md) |
| `summary_quote` | string | Exact phrase from summary that triggered the issue |
| `report_evidence` | string | Relevant phrase from report, or `"not present in report"` |
| `explanation` | string | Human-readable reason |
| `source` | string | `"deterministic_factcheck"` for code-detected issues; absent for LLM judge issues |

Valid `category` values:

| Category | Severity | Detected by |
|---|---|---|
| `A_factual` | BLOCK | Deterministic factcheck + LLM judge |
| `A_logic_causal_wrong` | BLOCK | LLM judge |
| `A_logic_causal_fabricated` | BLOCK | LLM judge |
| `A_logic_temporal` | BLOCK | LLM judge |
| `B_unsupported` | BLOCK | LLM judge |
| `B_fabricated_conclusion` | BLOCK | LLM judge |
| `B_tone_escalation` | BLOCK | LLM judge |
| `buy_price_absolute` | BLOCK | LLM judge |
| `buy_price_upside` | BLOCK | Deterministic factcheck + LLM judge |
| `buy_price_timing` | BLOCK | LLM judge |
| `A_truncation` | FLAG | LLM judge |
| `C_disclaimer_omission` | FLAG | LLM judge |
| `format` | FLAG | LLM judge |
| `render` | FLAG | LLM judge |

### Hallucination and buy violation accounting

`persist.py`'s `aggregate()` method classifies issues for dashboard metrics:

- **Hallucination**: any issue whose `category` starts with `A_` or `B_`
- **Buy violation**: any issue whose `category` starts with `buy_price`

---

## `agent_state`

Key-value store for pipeline run metadata. Not used for eval logic — only for audit/status.

| Column | Type | Notes |
|---|---|---|
| `key` | text | Primary key |
| `value` | jsonb | Arbitrary structured value |
| `updated_at` | timestamptz | Updated on every `set_state()` call |

### Keys in use

| Key | Value shape | Set by |
|---|---|---|
| `"last_daily_run"` | `{"processed": 5, "mode": "mock" \| "greennode"}` | `/run-daily` on completion |

Used by `/status` indirectly via `healthcheck()` — the healthcheck queries `agent_state` with a `SELECT … LIMIT 1` to verify Supabase connectivity.

---

## Data Lifecycle Per Pipeline Stage

```
POST /reports/import
  └─ insert into reports (status="ready" or "pending")

/run-daily trigger
  └─ fetch_unevaluated_summaries()
       └─ LEFT JOIN eval_runs on summaries.id — returns summaries with no existing eval_run
       └─ attach reports metadata/source text
  └─ for each summary + report:
       ├─ ensure_report_text()
       │    ├─ (if source_pdf_url) fetch PDF → update reports.report_text + status="ready"
       │    └─ (if text too short) update reports.status → persist ERROR eval_run → stop
       │
       ├─ Stage 1: extract_skeleton(report_text) → skeleton_json
       │
       ├─ Stage 3: judge_summary(report_text, summary_text, skeleton_json)
       │    ├─ deterministic_factcheck() → blocks, flags (code only)
       │    ├─ LLM judge → judge_json
       │    └─ merge_issues() + compute_verdict() → verdict
       │
       └─ insert_eval_run() → eval_runs row
            (skeleton_json, judge_json, verdict, blocks, flags)

/dashboard
  └─ fetch_eval_runs(limit=100)
       └─ JOIN reports (metadata only, no report_text)
       └─ JOIN summaries (summary_text, model)
  └─ aggregate() → counts, rates, breakdown
```

### Unevaluated summary selection

`fetch_unevaluated_summaries()` uses a LEFT JOIN `eval_runs!left(id)` filtered by `is(eval_runs.id, null)` to find summaries with no eval run. A fallback path fetches the last 200 summaries and filters in Python when the join syntax is not supported by the Supabase version.

### Re-runs

Re-running `/run-daily` on a summary that already has an `eval_run` will **not** re-evaluate it — `fetch_unevaluated_summaries()` filters those out. To force a re-eval, delete the existing `eval_run` row in Supabase.
