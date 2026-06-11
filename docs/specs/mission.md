# AI Summary Eval Agent — Mission

## Problem

AI-generated Vietnamese stock-research summaries can mislead users when they:

- Change numbers, dates, names, causal logic, or time order from the source report.
- Add unsupported claims or conclusions not present in the source report.
- Omit material risks, caveats, or uncertainty that change interpretation.
- Imply buy price, upside, or timing advice in ways that violate product rules.

The current manual workflow depends on one PO using NotebookLM, Claude chat, and Excel. It takes roughly 15–20 minutes per summary, does not scale, and does not create durable regression history.

## Users

- **Primary user:** solo PM/PO reviewing daily AI summary quality.
- **Contest/demo audience:** GreenNode AgentBase judges and public voters who need to see a working automated agent.
- **Future production user:** internal product/quality team monitoring summary regressions over time.

## Goal

Replace the manual evaluation flow with an automated daily pipeline that:

1. Reads source stock-research reports.
2. Generates or receives AI summaries.
3. Extracts auditable source skeletons.
4. Judges each summary against the full source report and eval checklist.
5. Persists evaluation history.
6. Renders a dashboard with aggregate quality and per-summary details.

## Success Criteria

- Daily evaluation can run without PO intervention except when failures require review.
- Every evaluated summary receives one final verdict: `PASS`, `PASS-WITH-FLAG`, `FAIL`, or controlled `ERROR`.
- Failure taxonomy is consistent across prompts, code, persisted output, dashboard, and docs.
- Launch sample pass rate is at least 85%.
- Adversarial sample has zero Type A or Type B hallucinations.
- Buy price violation count is always 0.
- No single failure type accounts for more than 30% of all failures.
- Demo can show both aggregate dashboard metrics and expandable per-summary evidence.

## Guiding Principles

- **Source report is ground truth.** The system judges summary faithfulness, not whether the report itself is correct.
- **Skeleton is an audit checkpoint, not authority.** If skeleton and full report conflict, the full report wins.
- **Avoid double-hallucination.** Use full report text, deterministic checks, and code-computed verdicts instead of relying only on one LLM judge.
- **Token-safe by default.** Use `MOCK_LLM_MODE=true` during development and only run GreenNode on tightly scoped validation.
- **Build once, swap config.** Keep business logic independent from deployment infra and future model/provider changes.
- **Demo before scale.** Prove correctness on fixtures and a tiny cloud run before multi-report GreenNode loops.

## Out Of Scope For Contest

- Internal company DB integration.
- Customer/private/PII data.
- Dashboard authentication or multi-tenant permissions.
- Real-time streaming.
- Email, Slack, or alerting workflows.
- Parallel batch processing.
- Complex retry orchestration beyond simple controlled failure behavior.
