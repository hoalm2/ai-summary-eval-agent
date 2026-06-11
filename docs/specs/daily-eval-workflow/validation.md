# Daily Eval Workflow — Validation

## Spec Validation

- [ ] Project constitution exists and reflects PRD mission, tech stack, roadmap, constraints, and out-of-scope items.
      verify: `test -f docs/specs/mission.md && test -f docs/specs/tech-stack.md && test -f docs/specs/roadmap.md`

- [ ] Daily workflow feature spec exists with requirements, plan, and validation docs.
      verify: `test -f docs/specs/daily-eval-workflow/requirements.md && test -f docs/specs/daily-eval-workflow/plan.md && test -f docs/specs/daily-eval-workflow/validation.md`

- [ ] Verdict rules in docs match final checklist: FAIL for any BLOCK or at least 2 FLAGs; PASS-WITH-FLAG for exactly 1 FLAG; PASS for no issues.
      verify: compare `docs/specs/daily-eval-workflow/requirements.md` with `Eval checklist.rtf` rating rules.

## Local Mock Workflow

- [ ] App starts locally without calling GreenNode when `MOCK_LLM_MODE=true`.
      verify: `MOCK_LLM_MODE=true uvicorn main:app --port 8080`

- [ ] Health endpoint returns OK.
      verify: `curl http://localhost:8080/health`

- [ ] Status endpoint reports `mock_llm_mode=true` and Supabase connectivity.
      verify: `curl http://localhost:8080/status`

- [ ] Seed fixtures can be inserted into Supabase.
      verify: `MOCK_LLM_MODE=true python scripts/seed_demo_fixtures.py`

- [ ] `/run-demo` processes at most the configured demo batch size and persists eval runs.
      verify: `curl -X POST http://localhost:8080/run-demo -H "X-Demo-Token: $DEMO_TOKEN"`

- [ ] `/run-daily` picks unevaluated reports, generates summaries, judges them, and persists eval runs.
      verify: `curl -X POST http://localhost:8080/run-daily -H "X-Demo-Token: $DEMO_TOKEN"`

## Eval Validity

- [ ] PASS fixture returns no blocks and no flags.
      verify: run golden fixture test for supported target price, thesis, and risk summary.

- [ ] Wrong-number fixture returns `FAIL` with Type A factual issue.
      verify: run golden fixture test where summary changes a report number.

- [ ] Temporal-distortion fixture returns `FAIL` with Type A temporal/logic issue.
      verify: run golden fixture test where forecast is stated as completed fact.

- [ ] Tone-escalation fixture returns `FAIL` with Type B tone issue.
      verify: run golden fixture test where “cải thiện” becomes “bứt phá/tăng vọt”.

- [ ] Buy-price timing fixture returns `FAIL` with BUY issue.
      verify: run golden fixture test where summary says “nên mua ngay” or equivalent timing advice.

- [ ] Disclaimer omission fixture returns `PASS-WITH-FLAG` when exactly one material caveat is omitted.
      verify: run golden fixture test with one `C_disclaimer_omission`.

- [ ] Two FLAG issues produce `FAIL`.
      verify: unit test `compute_verdict([], [flag1, flag2]) == "FAIL"`.

- [ ] Judge parse failure produces controlled `ERROR`.
      verify: unit test or fake LLM result with `parse_error=True`.

## Data Flow And Safety

- [ ] `/results` does not expose full `report_text`.
      verify: inspect `/results` JSON and confirm report objects include metadata only.

- [ ] `/dashboard` does not render full `report_text`.
      verify: inspect dashboard HTML for known long report text fragments.

- [ ] Too-short report text persists a controlled `ERROR` eval.
      verify: import or evaluate a report below `REPORT_TEXT_MIN_CHARS` and inspect latest eval run.

- [ ] Report import skips duplicates when `skip_existing=true`.
      verify: call `/reports/import` twice with same `source_pdf_url` or same `ticker + report_date`.

- [ ] One bad report does not abort the entire daily batch.
      verify: run daily batch with one invalid report and one valid report; valid report still gets an eval.

## Dashboard Readiness

- [ ] Dashboard shows aggregate totals, pass/fail/pass-with-flag counts, pass rate, hallucination count/rate, buy violation count, and failure breakdown.
      verify: open `/dashboard` after seeded evals and visually inspect metrics.

- [ ] Dashboard detail list shows ticker, date, verdict badge, generated summary, issues, skeleton JSON, and judge JSON.
      verify: expand at least one row in `/dashboard`.

- [ ] Dashboard supports filtering by verdict and date.
      verify: use filter controls and confirm visible rows change.

- [ ] Buy violation count is 0 for launch sample.
      verify: inspect `/dashboard` aggregate after launch sample.

## Deploy Gate

- [ ] Supabase spike passes.
      verify: `python scripts/spike_supabase.py`

- [ ] GreenNode tiny JSON spike passes before any multi-report run.
      verify: `python scripts/spike_greennode.py`

- [ ] AgentBase build exposes the expected port and reads runtime env vars.
      verify: cloud `/health` and `/status` after deploying with `MOCK_LLM_MODE=true`.

- [ ] AgentBase runtime can reach Supabase.
      verify: cloud `/status` reports `supabase_ok=true`.

- [ ] First real model validation processes exactly one report.
      verify: set `MOCK_LLM_MODE=false`, reduce batch/input to one report, run one protected trigger, inspect one new eval run.
