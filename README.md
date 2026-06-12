# AI Summary Eval Agent

Supabase-backed MVP agent for evaluating Vietnamese stock-research AI summaries. The agent reads preloaded reports and pre-created summaries from Supabase, extracts a report skeleton, judges the summary against the full report text, persists verdicts back to Supabase, and renders a simple dashboard.

## Architecture

```text
Supabase reports + pre-created summaries
  -> Stage 0: fetch + extract PDF text
  -> Stage 1: skeleton extraction (qwen3-5-27b)
  -> Stage 1b: per-bullet citation alignment (qwen3-5-27b)
  -> Stage 3: LLM judge + deterministic factcheck (gemma-4-31b-it)
  -> code-computed verdict (PASS / FAIL / PASS-WITH-FLAG / ERROR)
  -> Supabase eval_runs
  -> /dashboard
```

The production-like daily flow has **3 LLM stages + 1 persistence step**. Stage 2 summary generation remains in the repo as a contest/demo shim, but `/run-daily` now evaluates pre-created summaries from Supabase. During token-free testing, LLM stages are mocked locally with `MOCK_LLM_MODE=true`; Supabase reads/writes and dashboard behavior still run for real.

## Storage Decision

- Supabase Postgres is the durable source of truth for `reports`, `summaries`, `eval_runs`, and `agent_state`.
- Original PDF URLs are stored in `reports.source_pdf_url`.
- Extracted `report_text` is stored once so daily runtime does not repeatedly download/parse PDFs.
- `summaries` store pre-created summary text linked to `reports`; `/run-daily` evaluates these rows instead of generating new summaries.
- `/run-daily` skips any summary that already has at least one `eval_runs` row, so the same summary is not evaluated twice.
- Supabase Storage is only needed if original public PDF links become unstable.
- Generated `data/`, local `storage/`, and `.env` files are ignored and should not be committed.

## QC Strategy — Do Not Burn GreenNode Tokens Early

The code is wired to GreenNode, but the default test phase should run with `MOCK_LLM_MODE=true`.

1. **Day 1–4 / coding:** use Supabase + mock LLM flow only. Test prompt shape manually in chat by pasting report text and checking JSON format. Zero GreenNode tokens.
2. **Day 5 / first deploy:** set `MOCK_LLM_MODE=false` and run exactly 1 symbol on GreenNode to verify end-to-end.
3. **Day 6 / human eval:** PO reviews 5 verdicts and confirms judge quality.
4. **Never run multi-symbol GreenNode loops** until prompts are validated manually first.

Token-safe defaults:

- `/run-demo` processes at most 2 records.
- `/run-daily` processes 5 unevaluated pre-created summaries, judges them, and writes results. During test phase it should run only with `MOCK_LLM_MODE=true`.
- GreenNode is called only when `MOCK_LLM_MODE=false`.

## Supabase Setup

1. Create a Supabase project.
2. Open SQL Editor and run `supabase_schema.sql`.
3. Copy the project URL and service-role key into runtime env vars.
4. Keep the service-role key server-side only; never expose it in browser code.

Tables:

- `reports`: report metadata, URL, optional storage path, and extracted text.
- `summaries`: generated summary text linked to a report.
- `eval_runs`: append-only evaluation history.
- `agent_state`: runtime flags and cursors — `pipeline_enabled` (kill switch), `last_daily_run` (last batch metadata).

## Environment

Copy `.env.example` to `.env` for local development:

```bash
cp .env.example .env
```

Required variables:

- `GREENNODE_API_KEY`
- `GREENNODE_BASE_URL` — default `https://maas-llm-aiplatform-hcm.api.vngcloud.vn/v1`
- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- `DEMO_TOKEN`

Optional:

- `GREENNODE_JSON_MODE=true` after `scripts/spike_greennode.py` confirms support.
- `MOCK_LLM_MODE=true` to run Supabase-backed flow without GreenNode calls.
- `ALLOWED_PDF_HOSTS=cdn.simplize.vn` for PDF download allowlisting.
- `REPORT_TEXT_MIN_CHARS=80` rejects empty/too-short report text before judging.

## Deploy Contract (Verified)

Fill this section during B0 before building beyond the gate:

- GreenNode OpenAI-compatible endpoint: `UNVERIFIED`
- GreenNode JSON mode support: `UNVERIFIED`
- AgentBase Docker support: `UNVERIFIED`
- AgentBase exposed port: `UNVERIFIED`, expected `8080`
- AgentBase env injection: `UNVERIFIED`
- AgentBase local storage persistence across redeploy: `UNVERIFIED`; Supabase remains primary storage either way
- AgentBase outbound access to Supabase: `UNVERIFIED`

## Local Run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8080
```

Open:

- `GET http://localhost:8080/health`
- `GET http://localhost:8080/status`
- `GET http://localhost:8080/dashboard`
- `GET http://localhost:8080/results`

Protected triggers require `X-Demo-Token`:

```bash
curl -X POST http://localhost:8080/run-demo \
  -H "X-Demo-Token: $DEMO_TOKEN"

curl -X POST http://localhost:8080/run-daily \
  -H "X-Demo-Token: $DEMO_TOKEN"
```

## B0 Spikes

GreenNode:

```bash
python scripts/spike_greennode.py
```

Expected: two consecutive calls parse JSON, total latency is printed, and JSON-mode support is reported.

Run this only when ready to spend a tiny amount of GreenNode tokens. It is intentionally not required for Day 1–4 mock testing.

Supabase:

```bash
python scripts/spike_supabase.py
```

Expected: one `agent_state` row is inserted, read, deleted, and reported as OK.

## Fixture Build

Paste DevTools response into `data/fixture_raw.json`. Then:

```bash
python scripts/build_fixture.py
```

The script:

1. Reads up to 50 source records.
2. Downloads PDFs from allowlisted hosts.
3. Extracts `report_text` with PyMuPDF.
4. Generates a summary via GreenNode, unless `MOCK_LLM_MODE=true`.
5. Inserts into Supabase `reports` and `summaries`.

For quick rubric demos without PDF downloads:

```bash
python scripts/seed_demo_fixtures.py
```

This inserts PASS, wrong-number, buy-price, tone-escalation, temporal-distortion, and disclaimer-omission examples. It includes seeded summaries for `/run-demo` and `/run-daily`.

## Import Reports After Deploy

Use this protected endpoint to load static reports into Supabase after the agent is deployed:

```bash
curl -X POST "$AGENT_URL/reports/import" \
  -H "Content-Type: application/json" \
  -H "X-Demo-Token: $DEMO_TOKEN" \
  -d '{
    "reports": [
      {
        "ticker": "VTP",
        "report_date": "2026-06-01",
        "source_pdf_url": "https://cdn.simplize.vn/example.pdf",
        "report_text": "Full extracted report text here",
        "status": "ready"
      }
    ],
    "skip_existing": true
  }'
```

Rules:

- Each item needs either `report_text` or an allowlisted `source_pdf_url`.
- `report_text` is the operational ground truth used by the judge. Keep `source_pdf_url` as the source reference.
- If only `source_pdf_url` is provided, `/run-daily` extracts text with PyMuPDF and stores it back into `reports.report_text`.
- Reports with empty or too-short extracted text become controlled `ERROR` evals instead of being judged.
- `skip_existing=true` skips reports with the same `source_pdf_url`, or same `ticker + report_date` when no URL is provided.
- This endpoint can insert both reports and pre-created summaries. `/run-daily` only evaluates summaries that already exist.

## API

- `GET /health`: app liveness.
- `GET /status`: deployment smoke test without exposing secrets; shows mock mode, Supabase connectivity, and row counts.
- `GET /results`: safe JSON eval history, excluding full `report_text`.
- `GET /dashboard`: HTML dashboard with aggregate metrics, verdict/ticker/date filters, generated summaries, issue details, skeleton JSON, and judge JSON.
- `GET /pipeline/status`: current kill-switch state and last batch metadata.
- `POST /pipeline/enable`: enable `/run-daily` (requires `X-Demo-Token`).
- `POST /pipeline/disable`: disable `/run-daily` — any trigger returns immediately, 0 LLM calls (requires `X-Demo-Token`).
- `POST /run-demo`: evaluates up to 2 first summaries; does not advance daily cursor.
- `POST /run-daily`: picks next 5 unevaluated summaries, judges them, and writes eval results. No-op when pipeline is disabled or no unevaluated summaries remain.
- `POST /run-one`: ad hoc evaluation; can optionally persist if `report_id` and `summary_id` are supplied.
- `POST /reports/import`: protected static report ingestion for post-deploy use.

During token-free testing, all protected trigger endpoints still exercise Supabase persistence and dashboard aggregation, but LLM outputs are mocked locally.

## Security + Ops

- `.env` is ignored and not copied explicitly into Docker.
- `DEMO_TOKEN` protects API-burning endpoints.
- PDF fetches are restricted to `ALLOWED_PDF_HOSTS` or local files under `data/`.
- Ground-truth text is extracted with PyMuPDF, not an LLM. Skeleton is only a hint; Stage 3 judges against full `report_text`.
- Too-short PDF extraction is marked with `extract_too_short` / `report_text_too_short` and persisted as `ERROR`.
- Dashboard hides full report text.
- Token caps: skeleton `1800`, summary `900`, judge `1800` max tokens by default.
- Expected latency depends on GreenNode model speed; budget 4 LLM calls per report (Stage 1, 2, 1b, 3).

## AgentBase Deploy

The Docker image exposes port `8080` and starts:

```bash
uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}
```

Inject secrets at runtime via AgentBase env. Do not bake `.env` into the image.

Official AgentBase skills are installed project-locally under `.agents/skills/`. Restart Codex/your AI coding tool if the skills do not appear. The official guide expects AgentBase deployment to use IAM env vars `GREENNODE_CLIENT_ID` and `GREENNODE_CLIENT_SECRET` for platform operations, while this app uses `GREENNODE_API_KEY` / `GREENNODE_BASE_URL` for OpenAI-compatible MaaS calls.

Cloud smoke test after deploy:

```bash
curl "$AGENT_URL/health"
curl "$AGENT_URL/status"
curl "$AGENT_URL/dashboard"
```

For the first cloud test, keep `MOCK_LLM_MODE=true`. Only set `MOCK_LLM_MODE=false` when manually validating exactly one GreenNode-backed report.
