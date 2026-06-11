# Agent Pipeline — Stage-by-Stage Reference

Detailed technical reference for each stage of the eval pipeline. Covers input/output contracts, prompt files, error paths, and mock vs real mode behavior. Read [overview.md](overview.md) first for the high-level picture.

---

## Contest vs Production Pipeline

The pipeline is designed so Stage 2 (summary generation) is a **contest-only shim** that can be removed without touching any other stage.

**Contest flow** (current — no access to internal DB):
```
Stage 0 → Stage 1 → Stage 2 → Stage 1b → Stage 3 → persist
```

**Production flow** (post-contest — summary already exists in internal DB):
```
Stage 0 → Stage 1 → Stage 1b → Stage 3 → persist
```

Switching to production requires only:
1. Remove the `generate_summary()` call in `generate_and_evaluate_report()`
2. Feed the pre-existing `summary_text` from the internal DB into `align_bullets()` and `judge_summary()` directly

No changes to Stage 1b, Stage 3, persist, or dashboard.

---

## Entry Points

There are three ways the pipeline is triggered, all via HTTP:

| Endpoint | Trigger | What it runs |
|---|---|---|
| `POST /run-daily` | Manual (demo) or cron | Full pipeline: PDF → Stage 1 → Stage 2 → Stage 1b → Stage 3 → persist |
| `POST /run-demo` | Demo mode | Stage 1 + Stage 1b + Stage 3 on already-stored summaries (skips Stage 2) |
| `POST /run-one` | Ad hoc / testing | Stage 1 + Stage 1b + Stage 3 on inline report + summary text; optional persist |

All write-enabled endpoints require `X-Demo-Token` header.

---

## Stage 0 — Report Text Acquisition

**File:** `pipeline/pdf.py` — `validate_pdf_source()`, `extract_pdf_text()`  
**Caller:** `main.py` — `ensure_report_text()`

This is not an LLM stage. It ensures `report_text` is available before any LLM call.

### Input

| Field | Source | Notes |
|---|---|---|
| `report.report_text` | `reports` table | Used directly if present and long enough |
| `report.source_pdf_url` | `reports` table | Fetched on demand if `report_text` is absent |
| `report.pdf_storage_path` | `reports` table | Local path fallback (must be inside `data/`) |

### Output

- `report_text: str` — raw UTF-8 text extracted from PDF or read from DB

### Validation

- URL host must be in `settings.allowed_pdf_hosts` (`ALLOWED_PDF_HOSTS` env var)
- Local paths must resolve inside `data/` — no directory traversal
- Text length must be ≥ `settings.report_text_min_chars` (`REPORT_TEXT_MIN_CHARS`, default 200)

### Error paths

| Condition | Status written to `reports.status` | Pipeline action |
|---|---|---|
| PDF host not allowlisted | — (raises immediately) | HTTP 400 / ERROR eval |
| HTTP fetch fails | `extract_failed` | `persist_error_eval()` → ERROR verdict |
| Extracted text too short | `extract_too_short` | `persist_error_eval()` → ERROR verdict |
| `report_text` in DB too short | `report_text_too_short` | `persist_error_eval()` → ERROR verdict |

---

## Stage 1 — Skeleton Extraction

**File:** `pipeline/stage1_skeleton.py` — `extract_skeleton()`  
**Prompt:** `prompts/skeleton_extraction.md`  
**Model:** `settings.model_skeleton` (default: `qwen3-5-27b`)  
**LLM call type:** `json_chat` (structured JSON output required)  
**Max tokens:** `settings.skeleton_max_tokens`

### Input

```
report_text: str          # full report text
ticker: str | None        # injected into user prompt for context
report_date: str | None   # injected into user prompt for context
```

### User prompt structure

```
Extract a faithful skeleton from this Vietnamese stock research report.

Ticker: {ticker}
Report date: {report_date}

<REPORT>
{report_text}
</REPORT>
```

### Output

```json
{
  "ticker": "VTP",
  "report_date": "2026-06-01",
  "thesis_points": ["..."],
  "key_risks": ["..."],
  "financial_highlights": ["..."],
  "disclaimers": ["..."],
  "notes": "..."
}
```

The skeleton is passed as a hint to Stage 3, and persisted as `eval_runs.skeleton_json`. It can be inspected independently in the dashboard.

### Error paths

- If `json_chat` parse fails after 2 retries → `parse_error=True` → Stage 3 will return ERROR verdict
- In mock mode → returns a fixed skeleton with empty arrays and `notes: "MOCK_LLM_MODE skeleton; no GreenNode tokens used."`

---

## Stage 1b — Per-bullet Citation Alignment

**File:** `pipeline/stage1b_align.py` — `align_bullets()`  
**Prompt:** `prompts/bullet_alignment.md`  
**Model:** `settings.model_skeleton` (default: `qwen3-5-27b`)  
**LLM call type:** `json_chat`  
**Max tokens:** `settings.skeleton_max_tokens`

Runs **after Stage 2** in the contest flow, or **immediately after Stage 1** in production (when summary is pre-existing). For each bullet in the summary, finds 1–3 verbatim quotes from the report that a reader would need to verify or refute that bullet's claim.

This stage is what enables per-bullet eval in Stage 3 and the bullet-level breakdown in the dashboard.

### Input

```
report_text: str      # full report text
summary_text: str     # the generated (or pre-existing) summary
```

### User prompt structure

```
<REPORT>{report_text}</REPORT>
<SUMMARY>{summary_text}</SUMMARY>
```

### Output

```json
[
  {
    "bullet_index": 1,
    "bullet_text": "<exact bullet text from summary>",
    "report_citations": [
      "<verbatim quote from report>",
      "<verbatim quote from report>"
    ]
  }
]
```

Returns `[]` on parse error — Stage 3 degrades gracefully (falls back to full report scan).

### Error paths

- Parse error after 2 retries → returns `[]`, pipeline continues
- In mock mode → returns a single placeholder bullet with `"MOCK_LLM_MODE bullet alignment"` citation

---

## Stage 2 — Summary Generation (contest only)

**File:** `pipeline/stage2_summary.py` — `generate_summary()`  
**Prompt:** `prompts/summary_generate.md`  
**Model:** `settings.model_summary` (default: `qwen3-5-27b`)  
**LLM call type:** `text_chat` (free-text, not JSON)  
**Max tokens:** `settings.summary_max_tokens`

Only called by `/run-daily` (`generate_and_evaluate_report`). The `/run-demo` and `/run-one` endpoints receive a pre-written summary and skip this stage.

### Input

```
report_text: str     # full report text
ticker: str | None   # injected for context
```

### User prompt structure

```
Ticker: {ticker}

<REPORT>
{report_text}
</REPORT>
```

### Output

```
summary_text: str    # Vietnamese bullet summary, ≤ 5 bullets, 1–2 sentences each
```

The summary is immediately written to the `summaries` table before Stage 3 runs, so a failure in Stage 3 does not cause data loss.

### Error paths

- Timeout / API error → raises exception → caught by `generate_and_evaluate_report_safely()` → `persist_error_eval()` → ERROR verdict, `summary_model = "unexpected_error"`
- In mock mode → returns `"• Mock summary: nội dung này chỉ dùng để test local, không gọi GreenNode."` — `summary_model` written as `"mock_llm"`

---

## Stage 3 — Judge + Deterministic Factcheck

**File:** `pipeline/stage3_judge.py` — `judge_summary()`  
This stage runs two sub-checks internally and merges their results before computing verdict.

---

### 3a — Deterministic Factcheck

**File:** `pipeline/factcheck.py` — `deterministic_factcheck()`

Runs before the LLM judge. No API call.

#### Input

```
report_text: str
summary_text: str
```

#### Process

1. Extract all number and date tokens from both texts using regex:
   - `NUMBER_PATTERN` — integers, decimals, currency units (đồng, tỷ, triệu, %), multipliers (x)
   - `DATE_PATTERN` — `DD/MM/YYYY`, `YYYY-MM-DD`, `QN/YYYY`, bare years (`20xx`)
   - All tokens normalized: lowercased, whitespace stripped, commas → dots
2. Any token in `summary_tokens` but absent from `report_tokens` → `A_factual` BLOCK
3. Scan summary for `UPSIDE_PATTERN` (e.g. "tiềm năng tăng 35%"). Flag as `buy_price_upside` BLOCK if:
   - The upside % is not present in the report, **or**
   - The summary lacks a `TIMESTAMP_PATTERN` (ngày/as of/tại ngày + date)

#### Output

```python
FactcheckResult(
    blocks=[{"category": "A_factual", "summary_quote": "...", "report_evidence": "not present in report",
              "explanation": "...", "source": "deterministic_factcheck"}, ...],
    flags=[]   # deterministic layer produces no flags
)
```

---

### 3b — LLM Judge

**Prompt:** `prompts/eval_judge.md`  
**Model:** `settings.model_judge` (default: `gemma-4-31b-it`)  
**LLM call type:** `json_chat`  
**Max tokens:** `settings.judge_max_tokens`

#### Input (user prompt structure)

```
<REPORT>
{report_text}
</REPORT>
<SUMMARY>
{summary_text}
</SUMMARY>
<FORMAT_SPEC>
Summary phải là tiếng Việt, tối đa 5 bullet points, mỗi bullet 1–2 câu, không nêu giá mua.
</FORMAT_SPEC>
<SKELETON>
{skeleton_json as JSON}
</SKELETON>
```

#### Output (judge_json)

```json
{
  "verdict": "FAIL",
  "block_count": 1,
  "flag_count": 0,
  "blocks": [
    {
      "category": "B_tone_escalation",
      "summary_quote": "bứt phá mạnh mẽ",
      "report_evidence": "cải thiện",
      "explanation": "..."
    }
  ],
  "flags": [],
  "rationale": "..."
}
```

Each issue object has these fields:

| Field | Description |
|---|---|
| `category` | Code name from [eval-taxonomy.md](eval-taxonomy.md) |
| `summary_quote` | The exact phrase from the summary that triggered the issue |
| `report_evidence` | The relevant phrase from the report (or `"not present in report"`) |
| `explanation` | Human-readable reason |
| `source` | `"deterministic_factcheck"` for factcheck issues; absent for LLM judge issues |

#### LLM parse error handling

1. First attempt: `json_chat` with `response_format: json_object` if `GREENNODE_JSON_MODE=true`
2. On parse failure: one retry with appended `"Return only valid JSON, no prose."`
3. On second failure: `LLMResult(parse_error=True)` → `compute_verdict()` returns `ERROR`

---

### 3c — Merge and Verdict

**File:** `pipeline/factcheck.py` — `merge_issues()`, `compute_verdict()`

1. `merge_issues(deterministic.blocks, llm_blocks)` — deduplicates by `(category, summary_quote)` key
2. `merge_issues(deterministic.flags, llm_flags)` — same
3. `compute_verdict(blocks, flags, parse_error)`:

```python
if parse_error:        return "ERROR"
if blocks or flags≥2:  return "FAIL"
if flags == 1:         return "PASS-WITH-FLAG"
return "PASS"
```

The LLM's own `verdict` field in `judge_json` is **ignored** — `compute_verdict()` is the authority.

#### Final output of `judge_summary()`

```python
{
    "verdict": "FAIL",          # from compute_verdict()
    "block_count": 1,
    "flag_count": 0,
    "blocks": [...],            # merged
    "flags": [...],             # merged
    "judge_json": {...},        # raw LLM output
    "parse_error": False,
    "rationale": "...",
}
```

---

## Persist

**File:** `pipeline/persist.py` — `SupabaseStore`

Called from `main.py` after Stage 3 completes.

| Write | Table | Data |
|---|---|---|
| `insert_summary()` | `summaries` | `report_id`, `summary_text`, `summary_model` |
| `insert_eval_run()` | `eval_runs` | `report_id`, `summary_id`, `skeleton_json`, `judge_json`, `verdict`, `blocks`, `flags` |
| `update_report_text()` | `reports` | `report_text`, `status = "ready"` (on successful PDF extraction) |
| `update_report_status()` | `reports` | `status` = error reason (on failure) |

---

## Error Isolation

Errors are contained at the per-report level so a bad report does not abort the batch:

```
generate_and_evaluate_report_safely()
  └─ generate_and_evaluate_report()  [may raise]
       └─ caught → persist_error_eval() → ERROR verdict inserted
            └─ if persist itself fails → returns in-memory ERROR result (not persisted)
```

The daily batch iterates `[generate_and_evaluate_report_safely(r) for r in reports]`. One ERROR does not stop subsequent reports.

---

## Mock vs Real Mode

Controlled by `MOCK_LLM_MODE` env var (default `true`).

| Behavior | `MOCK_LLM_MODE=true` | `MOCK_LLM_MODE=false` |
|---|---|---|
| Stage 1 LLM call | Returns fixed skeleton JSON, 0 tokens | Calls GreenNode `qwen3-5-27b` |
| Stage 2 LLM call | Returns fixed mock summary string | Calls GreenNode `qwen3-5-27b` |
| Stage 3 LLM judge | Keyword-matches summary for `buy_price_timing`, `B_tone_escalation`, `A_logic_temporal`, `C_disclaimer_omission` | Calls GreenNode `gemma-4-31b-it` |
| Deterministic factcheck | Runs normally (no LLM) | Runs normally |
| Supabase reads/writes | Real | Real |
| `summary_model` value saved | `"mock_llm"` | Model name (e.g. `"qwen3-5-27b"`) |

The mock judge uses the following keyword triggers for test coverage:

| Keyword in summary | Issue generated |
|---|---|
| `"nên mua ngay"` or `"tiềm năng tăng giá"` | `buy_price_timing` BLOCK |
| `"bứt phá"` or `"tăng vọt"` | `B_tone_escalation` BLOCK |
| `"đã phục hồi"` + `"kỳ vọng"` present in full prompt | `A_logic_temporal` BLOCK |
| `"rủi ro chính"` in report but not in summary section | `C_disclaimer_omission` FLAG |
