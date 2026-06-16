# AI Summary Judge — Tech Stack

## Runtime

- **Language:** Python 3.11.
- **Web framework:** FastAPI.
- **Server:** Uvicorn.
- **Container:** root `Dockerfile`, exposed on port `8080`.
- **Deployment target:** GreenNode AgentBase for contest deployment.

## Data Storage

- **Current MVP storage:** Supabase Postgres.
- **Tables:** `reports`, `summaries`, `eval_runs`, `agent_state`.
- **Operational ground truth:** `reports.report_text`, extracted from public PDFs or provided during import.
- **Public PDF reference:** `reports.source_pdf_url`.
- **Dashboard source:** persisted `eval_runs` joined with safe report and summary metadata.

## LLM Providers And Models

- **Contest provider:** GreenNode MaaS.
- **Stage 1 skeleton model:** `gemini/gemini-3.1-pro-preview` (`MODEL_SKELETON`).
- **Stage 1b alignment model:** `gemini/gemini-3.1-pro-preview` (`MODEL_ALIGN`, defaults to `MODEL_SKELETON`).
- **Stage 3b judge model:** `openai/gpt-5-mini` (`MODEL_JUDGE`).
- **Fallback model:** `deepseek/deepseek-v4-pro` (`MODEL_FALLBACK`).
- **API routing:** GPT-5 models use the **Responses API** (`client.responses.create`, non-streaming). All other models use Chat Completions.
- **Future production provider:** config swap to Anthropic Claude API — no business logic changes.

## Pipeline Stages

1. **Stage 0 — Input / extract text:** use stored `report_text` or extract from allowed public PDF source. Fetch pre-existing `summary_text` from `summaries` table.
2. **Stage 1 — Skeleton extraction:** extract auditable thesis, risks, disclaimers, and financial highlights.
3. **Stage 1b — Citation alignment:** for each summary bullet, find 1–3 verbatim quotes from the report as evidence.
4. **Stage 3a — Deterministic factcheck:** code-level checks for numeric/date/upside violations.
5. **Stage 3b — LLM judge:** compare summary against full report, skeleton hint, and eval checklist.
6. **Stage 3c — Merge & verdict:** deterministic `compute_verdict()` from merged issues.
7. **Persist:** write eval result to Supabase.
8. **Dashboard:** serve aggregate metrics and per-summary detail from one HTML endpoint.

## Configuration

Required environment variables:

- `GREENNODE_API_KEY`
- `GREENNODE_BASE_URL`
- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- `DEMO_TOKEN`

Important optional variables:

- `MOCK_LLM_MODE`
- `GREENNODE_JSON_MODE`
- `MODEL_SKELETON`
- `MODEL_ALIGN`
- `MODEL_JUDGE`
- `MODEL_FALLBACK`
- `ALLOWED_PDF_HOSTS`
- `REPORT_TEXT_MIN_CHARS`
- `PORT`

## Constraints

- Never commit `.env` or secrets.
- Service-role Supabase key must stay server-side.
- Public PDF fetching must be allowlisted.
- Development default should avoid GreenNode token burn.
- First cloud deploy should run with `MOCK_LLM_MODE=true`.
- First real GreenNode validation should evaluate exactly one report.
- Batch processing stays sequential for contest resource limits.

## Open Deployment Confirmations

- Exact GreenNode API base URL.
- Exact enabled model strings on MaaS portal.
- GreenNode JSON mode support.
- AgentBase exposed port convention.
- AgentBase env var injection behavior.
- AgentBase outbound access to Supabase.
- AgentBase scheduled trigger support.
- AgentBase local storage persistence, if ever needed.
