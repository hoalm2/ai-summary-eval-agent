# Daily Eval Workflow — Requirements

## Purpose

The daily eval workflow evaluates Vietnamese stock-research AI summaries against their source reports, persists verdict history, and exposes a dashboard for daily quality monitoring.

## In Scope

- Pick unevaluated reports from Supabase.
- Ensure each report has reliable `report_text`, extracting from an allowlisted PDF source when necessary.
- Extract an auditable report skeleton.
- Judge the pre-created summary against the full report text and eval checklist.
- Merge LLM judge issues with deterministic factcheck issues.
- Compute final verdict in code.
- Persist eval runs to Supabase.
- Render one dashboard endpoint containing aggregate metrics and per-summary details.
- Support a token-free mock path for local and first cloud testing.

## Out Of Scope

- Connecting to internal production DB.
- Using private customer data or PII.
- Parallel report processing.
- Dashboard login/authentication.
- Email/Slack alerts.
- Real-time streaming progress.
- Complex distributed scheduler logic.

## Verdict Rules

- `FAIL`: any BLOCK issue, or at least 2 FLAG issues.
- `FLAG`: 0 BLOCK issues and exactly 1 FLAG issue.
- `PASS`: 0 BLOCK issues and 0 FLAG issues.
- `ERROR`: controlled operational failure such as unreadable report text or judge parse failure.

## BLOCK Categories

- `A_factual`: number, percentage, price, date, name, fact, causal logic, or time order differs from the report.
- `A_logic_causal_wrong`: report says X causes Z; summary says Y causes Z.
- `A_logic_causal_fabricated`: report states X and Z independently; summary invents causality.
- `A_logic_temporal`: future forecast or expectation is presented as completed fact.
- `B_unsupported`: report does not mention or support the claim.
- `B_fabricated_conclusion`: report frames a point as hypothesis/possibility; summary states it as fact.
- `B_tone_escalation`: summary uses stronger confidence or positivity than the report supports.
- `buy_price_absolute`: summary frames a concrete price or range as an entry point.
- `buy_price_upside`: summary gives upside percentage without both report support and an as-of timestamp.
- `buy_price_timing`: summary makes a timing call such as “nên mua ngay”.

## FLAG Categories

- `A_truncation`: summary keeps one side of a compound claim and changes meaning or confidence.
- `C_disclaimer_omission`: summary omits a material caveat, risk, limitation, or uncertainty.
- `format`: bullet count, bullet length, language, or no-buy-price format rule is violated.
- `render`: broken formatting, odd characters, or visible rendering defects.

## Buy Price Rules

BLOCK if summary contains any of:

- Specific buy price or entry zone framed as advice.
- Upside percentage unless it is present in the report and accompanied by an as-of timestamp.
- Timing call such as “đây là thời điểm tốt để mua” or “nên tích lũy”.

Allowed:

- Absolute 12-month target price copied from the report.
- Valuation method and output copied from the report.
- Valuation label such as “hấp dẫn” or “chiết khấu so với lịch sử” when supported by report reasoning.

## Data Flow Requirements

- `reports.report_text` is the operational ground truth.
- `source_pdf_url` is retained as reference.
- Reports with missing or too-short text become controlled `ERROR` evals, not silent skips.
- `/run-daily` must not evaluate the same report twice under normal operation.
- `/results` and `/dashboard` must not expose full `report_text`.
- Dashboard aggregate must include total evaluated, pass/fail/pass-with-flag counts, pass rate, hallucination count/rate, buy violation count, and failure breakdown.

## Launch Criteria

- Pass rate is at least 85% on the launch sample.
- Type A/B hallucination count is 0 on the adversarial sample.
- Buy violation count is 0.
- Demo sample includes at least 5 public or synthetic report/summary pairs.
- Demo sample includes at least 1–2 intentional failures.

## Open Questions

- Does AgentBase support a cron-like scheduled trigger, or is `/run-daily` manually triggered for contest?
- Should contest demo keep Supabase as storage, or also provide a flat-file fallback for rulebook simplicity?
- What exact GreenNode MaaS base URL and model strings are enabled in the portal?
- Does the dashboard need a chart visualization, or are numeric trend/date filters enough for judging?
