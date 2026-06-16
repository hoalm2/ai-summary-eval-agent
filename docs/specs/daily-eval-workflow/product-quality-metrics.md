# Product Quality Metrics — AI Summary Judge

PRD-level success criteria for the AI Summary Judge feature deployed to end users. These thresholds define what "healthy" looks like in production and are used as pass/fail gates for daily monitoring.

The eval agent's taxonomy ([eval-taxonomy.md](eval-taxonomy.md)) covers the internal issue categories. This file defines the *outcome metrics* those categories roll up into.

---

## 3.1 Quality Metrics — Accuracy & Reliability

| Metric | Target | Definition | Dashboard card |
|---|---|---|---|
| **Hallucination rate** | ≤ 2% of evaluated summaries | Summaries with ≥ 1 Type A or Type B issue (excl. `B_tone_escalation`) ÷ total evaluated summaries | Hallucination rate |
| **Buy price prohibition** | 0 violations | Count of summaries with any `buy_price_absolute`, `buy_price_upside`, or `buy_price_timing` BLOCK issue | Buy violations |
| **UI format compliance** | ≥ 95% | % of summaries with 0 `format` or `render` FLAG issues (correct bullet structure, Vietnamese language, length within spec) | Format compliance |

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
| **User downvote rate** | ≤ 5% of summaries | % of displayed summaries receiving a user downvote — threshold above which the AI chat / explanation feature is triggered |

> **User downvote rate** is a *downstream* product metric not directly measurable by the eval agent (requires live user feedback data from CXM). Track separately from the automated eval. The eval agent's `format` + `render` FLAG rate (Format compliance card) is a leading indicator.

---

## 3.3 Operational Metrics

| Metric | Target | Definition |
|---|---|---|
| **Eval pipeline success rate** | ≥ 98% | % of batch runs completing without `ERROR` verdict (i.e., report text readable + judge output parseable) — `evaluated_count ÷ total` |

> **Eval pipeline success rate** is directly measurable by the eval agent. Target ≥ 98% maps to no more than 1 ERROR per 50 reports. Shown on the dashboard run list as the ERROR count per batch.

---

## Relationship to Launch Criteria

The launch criteria in [requirements.md](requirements.md) are the *contest* gates. This file defines the *production* ongoing thresholds. They are compatible but differ in scope:

| Gate | Source | When it applies |
|---|---|---|
| Pass rate ≥ 85%, zero Type A/B on adversarial sample, zero buy violations | requirements.md | Contest launch sample only |
| Hallucination rate ≤ 2%, eval pipeline success ≥ 98%, format compliance ≥ 95%, downvote rate ≤ 5% | This file | Ongoing production monitoring |

The eval agent's daily batch output should be compared against both sets of thresholds: the launch criteria for the contest demo, and these product metrics for production readiness reporting.
