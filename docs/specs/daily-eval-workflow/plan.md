# Daily Eval Workflow — Plan

## 1. Lock Spec And Taxonomy

- Map PRD taxonomy and eval checklist into the repo spec.
- Decide canonical issue category names for code, prompts, dashboard, and persisted JSON.
- Record launch criteria and out-of-scope constraints.

## 2. Build Golden Eval Fixtures

- Convert seeded examples into reusable test fixtures.
- Add cases for factual error, causal misattribution, temporal distortion, unsupported claim, fabricated conclusion, tone escalation, buy price violations, disclaimer omission, truncation, format, and render defects.
- Include both positive allowed cases and negative violation cases for target price/upside rules.

## 3. Add Focused Validation Tests

- Test deterministic verdict rules.
- Test deterministic factcheck on numeric/date/upside cases.
- Test mock-mode judge and summary path without GreenNode calls.
- Test API-level `/run-demo` or workflow orchestration with a fake store/client if feasible.

## 4. Align Prompt And Output Schema

- Update prompts so category labels match the feature spec.
- Require judge output fields that are stable enough for persistence and dashboard display.
- Validate or normalize judge JSON before computing verdict.
- Preserve code-computed final verdict as the authority.

## 5. Harden Supabase Workflow

- Make report import and daily selection idempotent at database or query level.
- Avoid loading all historical `eval_runs` into memory for unevaluated report selection.
- Ensure unexpected per-report failures are persisted as controlled `ERROR` results without aborting the batch.
- Add audit metadata for model names, mock/real mode, parse errors, and prompt/schema version if schema changes are acceptable.

## 6. Improve Dashboard Demo Readiness

- Add date filtering or trend view expected by the PRD.
- Ensure failure categories visibly map to Type A/B/C/BUY/FMT/RENDER.
- Keep full report text hidden.
- Make demo reset/seed/run steps easy to repeat.

## 7. Run Deploy Gates

- Verify Supabase insert/read/delete.
- Verify GreenNode JSON mode and exact model strings with one tiny call.
- Verify AgentBase Docker build, port, env injection, and outbound Supabase.
- Deploy first with `MOCK_LLM_MODE=true`.
- Run exactly one real GreenNode-backed report only after local and cloud mock validation pass.

## Suggested Build Order

1. Documentation/spec lock.
2. Golden fixtures and tests.
3. Prompt/schema alignment.
4. Supabase hardening.
5. Dashboard readiness.
6. AgentBase deploy validation.
