# AI Summary Eval Agent

Supabase-backed MVP agent for evaluating Vietnamese stock-research AI summaries. The agent reads report/summary fixtures from Supabase, extracts a report skeleton with GreenNode MaaS, judges the summary against the full report text, persists verdicts back to Supabase, and renders a simple dashboard.

## Architecture

```text
Supabase reports + summaries
  -> Stage 1: skeleton extraction via GreenNode
  -> Stage 3: judge via GreenNode using full report text as ground truth
  -> deterministic factcheck + code-computed verdict
  -> Supabase eval_runs
  -> /dashboard
```

This repo intentionally has **3 LLM stages in the product design**, but this MVP runtime uses Stage 1 and Stage 3 for evaluation because summaries are pre-generated during fixture setup. Stage 2 summary generation lives in `pipeline/stage2_summary.py` and `scripts/build_fixture.py`.

## Storage Decision

- Supabase Postgres is the durable source of truth for `reports`, `summaries`, `eval_runs`, and `agent_state`.
- Original PDF URLs are stored in `reports.source_pdf_url`.
- Extracted `report_text` is stored once so daily runtime does not repeatedly download/parse PDFs.
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
- `/run-daily` processes 5 records, but during test phase it should run only with `MOCK_LLM_MODE=true`.
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
- `agent_state`: cursor state such as `last_daily_index`.

## Environment

Copy `.env.example` to `.env` for local development:

```bash
cp .env.example .env
```

Required variables:

- `GREENNODE_API_KEY`
- `GREENNODE_BASE_URL`
- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- `DEMO_TOKEN`

Optional:

- `GREENNODE_JSON_MODE=true` after `scripts/spike_greennode.py` confirms support.
- `MOCK_LLM_MODE=true` to run Supabase-backed flow without GreenNode calls.
- `ALLOWED_PDF_HOSTS=cdn.simplize.vn` for PDF download allowlisting.

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
4. Generates a summary via GreenNode.
5. Inserts into Supabase `reports` and `summaries`.

For quick rubric demos without PDF downloads:

```bash
python scripts/seed_demo_fixtures.py
```

This inserts PASS, wrong-number, buy-price, tone-escalation, temporal-distortion, and disclaimer-omission examples.

## API

- `GET /health`: app liveness.
- `GET /results`: safe JSON eval history, excluding full `report_text`.
- `GET /dashboard`: HTML dashboard.
- `POST /run-demo`: evaluates up to 2 first summaries; does not advance daily cursor.
- `POST /run-daily`: evaluates next 5 summaries and advances `agent_state.last_daily_index`.
- `POST /run-one`: ad hoc evaluation; can optionally persist if `report_id` and `summary_id` are supplied.

During token-free testing, all protected trigger endpoints still exercise Supabase persistence and dashboard aggregation, but LLM outputs are mocked locally.

## Security + Ops

- `.env` is ignored and not copied explicitly into Docker.
- `DEMO_TOKEN` protects API-burning endpoints.
- PDF fetches are restricted to `ALLOWED_PDF_HOSTS` or local files under `data/`.
- Dashboard hides full report text.
- Token caps: skeleton `1800`, summary `900`, judge `1800` max tokens by default.
- Expected latency depends on GreenNode model speed; budget roughly 2 LLM calls per daily eval item.

## AgentBase Deploy

The Docker image exposes port `8080` and starts:

```bash
uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}
```

Inject secrets at runtime via AgentBase env. Do not bake `.env` into the image.
