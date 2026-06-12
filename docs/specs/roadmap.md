# AI Summary Eval Agent — Roadmap

## Now

- Lock project constitution and daily workflow feature spec from the PRD and eval checklist.
- Align prompts, code taxonomy, persisted JSON, and dashboard labels.
- Add golden fixture validation for the eval checklist.
- Harden `/run-daily` so one report failure does not abort the whole batch.
- Verify local mock-mode demo path end to end.

## Next

- Improve Supabase idempotency and unevaluated-report selection.
- Add dashboard trend/date views required by the PRD.
- Add prompt/schema version metadata to persisted eval runs.
- Run B0 deployment spikes for GreenNode, Supabase, and AgentBase.
- Deploy to AgentBase in `MOCK_LLM_MODE=true` and run cloud smoke checks.

## Later

- Run exactly one real GreenNode-backed report after prompt/schema validation.
- Expand adversarial sample to 10–15 difficult public reports.
- Calibrate judge quality through PO review of at least 5 verdicts.
- Consider scheduled trigger support after manual cloud flow is stable.
- Prepare production config swap for internal DB and Anthropic model provider.

## Done

- Supabase-backed MVP exists.
- Reports can be imported or preloaded into Supabase.
- `/run-daily` can evaluate pre-created summaries, persist `eval_runs`, and render dashboard.
- Mock LLM mode prevents development token burn.
- Official AgentBase skills are installed project-locally under `.agents/skills`.
