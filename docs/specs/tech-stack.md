# AI Summary Eval Agent — Tech Stack

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

- **API shape:** OpenAI-compatible chat completions.
- **Contest provider:** GreenNode MaaS.
- **Stage 2a skeleton model:** `qwen3-5-27b`.
- **Stage 2b summary model:** `qwen3-5-27b`.
- **Stage 3 judge model:** `gemma-4-31b-it`.
- **Fallback model:** `MiniMax-M2.5`.
- **Future production provider:** Anthropic Claude API by config swap, without changing business logic.

## Pipeline Stages

1. **Input / extract text:** use stored `report_text` or extract from allowed public PDF source.
2. **Stage 2b summary generation:** generate Vietnamese bullet summary from full report text.
3. **Stage 2a skeleton extraction:** extract auditable thesis, risks, disclaimers, and financial highlights.
4. **Stage 3 judge:** compare summary against full report, skeleton hint, and eval checklist.
5. **Deterministic factcheck:** code-level checks for numeric/date/upside violations.
6. **Persistence:** write summary and eval result to Supabase.
7. **Dashboard:** serve aggregate metrics and per-summary detail from one HTML endpoint.

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
- `MODEL_SUMMARY`
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
