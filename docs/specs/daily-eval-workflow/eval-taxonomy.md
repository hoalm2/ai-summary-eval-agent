# Eval Taxonomy — Issue Categories & Verdict Rules

Authoritative rubric for evaluating AI-generated Vietnamese stock-research summaries. Applies to human review, LLM judge prompt, code verdict logic, dashboard labels, and persisted JSON — all using the same category names listed here.

---

## Verdict Rules

| Verdict | Condition |
|---|---|
| `FAIL` | Any BLOCK present, **or** ≥ 2 FLAGs |
| `FLAG` | 0 BLOCKs and exactly 1 FLAG |
| `PASS` | 0 BLOCKs and 0 FLAGs |
| `ERROR` | Controlled operational failure — unreadable report text or judge parse failure; not a summary quality verdict |

---

## 🔴 BLOCK — Fail immediately

### Hallucination — Type A: Factual Error (`A_factual`)

Does any number, percentage, price, date, name, or key fact in the summary differ from the report?  
Does any statement directly contradict a fact stated in the report?  
Is the time order or causal direction reversed?

**How to check:** Extract every number, date, and named entity from both summary and report. A token present in the summary but absent from the report is an `A_factual` candidate. The deterministic factcheck layer in `pipeline/factcheck.py` runs this check automatically.

---

### Hallucination — Type A: Logic Conflict

#### Causal misattribution — wrong cause (`A_logic_causal_wrong`)

Report says X causes Z. Summary says Y causes Z.

#### Causal misattribution — fabricated link (`A_logic_causal_fabricated`)

Report states X and Z as two independent observations. Summary invents a causal relationship between them.  
**Check:** Does the causal link in the summary appear in the source evidence, or is it the model's own inference?

> **Disambiguation:** When a summary bullet contains both a causal claim and a quantitative result, verify that the cited evidence attributes that result to the **same cause** stated in the summary. A correct number paired with a wrong cause is `A_logic_causal_wrong`, not a pass.

---

### Hallucination — Type A: Temporal Distortion (`A_logic_temporal`)

A future forecast or expectation is presented as a completed fact.

**Examples:**
- Report: "dự kiến doanh thu tăng 20% trong Q3" → Summary: "doanh thu đã tăng 20% trong Q3" ❌
- Report: "kỳ vọng biên lợi nhuận cải thiện" → Summary: "biên lợi nhuận đã cải thiện" ❌

> **Disambiguation:** Temporal distortion is `A_logic_temporal` (Type A), not Type C. Type C is reserved for genuine omissions of caveats or disclaimers.

---

### Hallucination — Type B: Unsupported Claim (`B_unsupported`)

Is there any claim in the summary that the report does not mention and cannot be reasonably inferred?  
Does the summary add conclusions, context, or facts absent from the source?

---

### Type B: Fabricated Conclusion (`B_fabricated_conclusion`)

The report frames a point as a hypothesis, possibility, or analyst expectation. The summary states it as an established fact.

**Examples:**
- Report: "có thể đạt mức tăng trưởng 15%" → Summary: "sẽ đạt tăng trưởng 15%" ❌
- Report: "analyst kỳ vọng…" → Summary: "công ty đã xác nhận…" ❌

---

### Type B: Tone Escalation (`B_tone_escalation`)

The summary uses a stronger qualifier than the report uses for the same fact. The underlying number is not wrong, but the confidence or positivity level is inflated beyond what the evidence supports.

**Signal pairs — report says → summary says (violation):**

| Report | Summary (violation) |
|---|---|
| cải thiện / hỗ trợ | lãi đậm / bứt phá mạnh mẽ |
| có tiềm năng / dự kiến | chắc chắn / tất yếu |
| tăng trưởng | tăng vọt / bùng nổ |

**Test:** If you replace the summary's qualifier with the report's qualifier, would a reader interpret the claim with lower confidence? If yes → `B_tone_escalation`.

---

### Buy Price Prohibition

BLOCK on **any** of the following:

**`buy_price_absolute`** — A specific buy price or entry zone framed as a recommendation or entry point.  
Examples: "mua ở vùng 28.000–30.000", "tích lũy dưới X đồng", "nên mua vào ở mức này"  
Distinguish from: absolute 12-month target price — **allowed**.

**`buy_price_upside`** — An upside percentage without an as-of timestamp.  
Upside % is only allowed when it is (a) stated in the report **and** (b) accompanied by an explicit as-of date in the summary.  
Examples: "+31,1% upside", "tiềm năng tăng 35%"

**`buy_price_timing`** — A timing call framing a specific moment as the right time to buy.  
Examples: "đây là thời điểm tốt để mua", "nên tích lũy ở vùng này", "nên mua ngay"

**Allowed — do not block:**

- Absolute 12-month target price copied from the report
- Valuation method and output (P/B, P/E vs. historical average/median)
- Upside % with an explicit as-of timestamp, when present in the report
- Valuation label from the report ("hấp dẫn", "chiết khấu so với lịch sử") with supporting reasoning from the report

---

## 🟡 FLAG — Record, do not block

### Type A: Truncation Distortion (`A_truncation`)

The summary takes only one side of a compound claim or complex logic chain from the report. The omitted part is significant enough to change the meaning or confidence level of the claim — even though the retained part is numerically accurate.

---

### Hallucination — Type C: Disclaimer Omission (`C_disclaimer_omission`)

Does the report include a material caveat, risk, limitation, or uncertainty that, if absent from the summary, would cause a reader to interpret the summary with more confidence than the evidence warrants?  
Is any important risk, limitation, or uncertainty from the report missing from the summary?

---

### Format Inconsistency (`format`)

- Summary is not in bullet-point format per the prompt spec
- Number of bullets or bullet length is outside the specified range (max 5 bullets, 1–2 sentences each)
- Language is not Vietnamese
- Any element violates the no-buy-price format rule

---

### Render Quality (`render`)

Broken formatting, unexpected characters, broken markdown (stray `**`, `\n` literals, encoding artifacts), or other visible rendering defects in the output text.

---

## ✅ Pass Conditions

A summary passes when all of the following hold:

- All BLOCK checks above return no violation
- No more than 1 FLAG is present
- A reader who finishes the summary has sufficient context to understand the report's conclusions without being misled

---

## Scope of Judgment

A claim is flagged only when the report explicitly contradicts it, omits it, or states the opposite — **not** because the reviewer infers it might be incomplete or misleading based on outside knowledge. Do not flag based on implications not stated in the report.

---

## Check Methods by Failure Mode

Each failure mode is caught by one of two mechanisms — **Deterministic** (rule-based code, no LLM) or **LLM Judge** (Claude Sonnet). The two layers run independently and their findings are merged before the final verdict.

| Category | Severity | Check Method | Rationale |
|---|---|---|---|
| `A_factual` | BLOCK | **Deterministic** | Numbers and dates can be matched exactly by regex — no semantic understanding needed. LLMs are unreliable at exhaustive token-level comparison. |
| `buy_price_upside` | BLOCK | **Deterministic** | Upside % and timestamp presence are structural rules checkable by pattern matching, not meaning. |
| `A_logic_causal_wrong` | BLOCK | **LLM Judge** | Requires understanding which cause the report attributes to a result vs. what the summary claims. Cannot be expressed as a rule. |
| `A_logic_causal_fabricated` | BLOCK | **LLM Judge** | Requires detecting an invented causal link between two independent observations — inherently a reasoning task. |
| `A_logic_temporal` | BLOCK | **LLM Judge** | Distinguishing forecast tense ("dự kiến") from completed fact tense ("đã xảy ra") requires language understanding. |
| `B_unsupported` | BLOCK | **LLM Judge** | Detecting a claim that exists in the summary but has no basis in the report requires reading comprehension of both texts. |
| `B_fabricated_conclusion` | BLOCK | **LLM Judge** | Requires identifying when a hypothesis or expectation has been promoted to an established fact — a semantic judgment. |
| `B_tone_escalation` | BLOCK | **LLM Judge** | Comparing the confidence level of a qualifier ("cải thiện" vs "bứt phá") requires understanding nuance of language, not token matching. |
| `buy_price_absolute` | BLOCK | **LLM Judge** | Entry price recommendations are often implicit or paraphrased; exact-match rules produce too many false negatives. |
| `buy_price_timing` | BLOCK | **LLM Judge** | Timing calls ("nên mua ngay") vary in phrasing; intent detection requires understanding context. |
| `A_truncation` | FLAG | **LLM Judge** | Requires judging whether an omitted portion of a compound claim meaningfully changes the reader's interpretation. |
| `C_disclaimer_omission` | FLAG | **LLM Judge** | Requires identifying which risks in the report are material enough that their absence from the summary is misleading. |
| `format` | FLAG | **LLM Judge** | Structural spec violations (bullet count, language, length) are partially rule-checkable but the judge handles them holistically. |
| `render` | FLAG | **LLM Judge** | Rendering defects are best detected by visual/text inspection within the full summary context. |

### Why both layers?

| | Deterministic | LLM Judge |
|---|---|---|
| **Strength** | Never misses a number mismatch; zero token cost; runs in milliseconds | Catches semantic errors, tone, logic, and omissions that rules cannot express |
| **Weakness** | Blind to meaning — cannot detect logic errors or tone shifts | May occasionally miss an exact number mismatch; costs API tokens |
| **Role** | Safety net for hallucinated figures and prohibited upside claims | Primary evaluator for the majority of failure modes |

The deterministic layer is designed to be a **strict lower bound** — if it fires, the summary fails regardless of what the LLM judge says. The LLM judge then adds coverage for everything beyond token-level matching.
