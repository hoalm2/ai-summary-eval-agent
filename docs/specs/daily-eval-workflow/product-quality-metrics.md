# Product Quality Metrics — AI Summary Feature

PRD-level success criteria for the AI Summary feature deployed to end users. These thresholds define what "healthy" looks like in production and are used as pass/fail gates for daily monitoring.

The eval agent's taxonomy ([eval-taxonomy.md](eval-taxonomy.md)) covers the internal issue categories. This file defines the *outcome metrics* those categories roll up into.

---

## 3.1 Quality Metrics — Accuracy & Reliability

| Metric | Target | Definition |
|---|---|---|
| **Hallucination rate** | ≤ 2% of sampled summaries | Summaries with ≥ 1 Type A or Type B hallucination issue ÷ total sampled summaries |
| **CXM display rate** | ≥ 98% confirmed displayed | % of generated summaries confirmed rendered to end users in CXM without system error |
| **Buy price prohibition** | 0 violations | Count of summaries with any `buy_price_absolute`, `buy_price_upside`, or `buy_price_timing` BLOCK issue |
| **UI format compliance** | 100% | % of summaries with 0 `format` or `render` FLAG issues (correct bullet structure, Vietnamese language, length within spec) |

### How these map to eval verdict categories

| Product metric | Eval issue categories that count toward it |
|---|---|
| Hallucination rate | `A_factual`, `A_logic_causal_wrong`, `A_logic_causal_fabricated`, `A_logic_temporal`, `B_unsupported`, `B_fabricated_conclusion` |
| Buy price prohibition | `buy_price_absolute`, `buy_price_upside`, `buy_price_timing` |
| UI format compliance | `format`, `render` |

> **Note:** `B_tone_escalation` is a BLOCK in the eval taxonomy but is classified as a hallucination-adjacent quality issue, not a direct safety violation. It should be tracked separately in the hallucination rate breakdown.

---

## 3.2 UX Metrics

| Metric | Target | Definition |
|---|---|---|
| **Format consistency** | ≥ 95% of summaries | % of summaries with bullet-point format and length consistent with prompt spec (≤ 5 bullets, 1–2 sentences each, Vietnamese) |
| **User downvote rate** | ≤ 5% of summaries | % of displayed summaries receiving a user downvote — threshold above which the AI chat / explanation feature is triggered |

> **User downvote rate** is a *downstream* product metric not directly measurable by the eval agent (requires live user feedback data). Track separately from the automated eval. The eval agent's `format` + `render` FLAG rate is a leading indicator.

---

## 3.3 Operational Metrics

| Metric | Target | Definition |
|---|---|---|
| **Creation success rate** | ≥ 98% | % of batch jobs completing without `ERROR` verdict (i.e., report text readable + judge output parseable) |
| **Render time (P90)** | ≤ 3s from BE call | 90th-percentile latency from backend summary-fetch call to CXM display — measured at product layer, not within eval agent |

> **Creation success rate** is directly measurable by the eval agent: `(total runs − ERROR runs) ÷ total runs`. Target ≥ 98% maps to no more than 1 ERROR per 50 reports.

---

## Relationship to Launch Criteria

The launch criteria in [requirements.md](requirements.md) are the *contest* gates. This file defines the *production* ongoing thresholds. They are compatible but differ in scope:

| Gate | Source | When it applies |
|---|---|---|
| Pass rate ≥ 85%, zero Type A/B on adversarial sample, zero buy violations | requirements.md | Contest launch sample only |
| Hallucination rate ≤ 2%, creation success ≥ 98%, downvote rate ≤ 5% | This file | Ongoing production monitoring |

The eval agent's daily batch output should be compared against both sets of thresholds: the launch criteria for the contest demo, and these product metrics for production readiness reporting.
