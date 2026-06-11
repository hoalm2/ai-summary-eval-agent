from __future__ import annotations

import json
from typing import Any

from config import Settings, get_settings
from pipeline.factcheck import compute_verdict, deterministic_factcheck, merge_issues
from pipeline.llm import LLMClient, read_prompt


PROMPT_PATH = "prompts/eval_judge.md"
FORMAT_SPEC = "Summary phải là tiếng Việt, tối đa 4 bullet points, mỗi bullet 1–2 câu, không nêu giá mua, giá vào lệnh, hay khuyến nghị thời điểm mua."


def judge_summary(
    *,
    report_text: str,
    summary_text: str,
    skeleton_json: dict[str, Any] | None = None,
    bullet_evals: list[dict[str, Any]] | None = None,
    llm_client: LLMClient | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    llm_client = llm_client or LLMClient(settings)
    deterministic = deterministic_factcheck(report_text, summary_text)
    system_prompt = read_prompt(PROMPT_PATH)
    user_prompt = f"""
<REPORT>
{report_text}
</REPORT>
<SUMMARY>
{summary_text}
</SUMMARY>
<FORMAT_SPEC>
{FORMAT_SPEC}
</FORMAT_SPEC>
<SKELETON>
{json.dumps(skeleton_json or {}, ensure_ascii=False)}
</SKELETON>
<BULLET_EVALS>
{json.dumps(bullet_evals or [], ensure_ascii=False)}
</BULLET_EVALS>
"""
    llm_result = llm_client.json_chat(
        model=settings.model_judge,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        max_tokens=settings.judge_max_tokens,
    )
    judge_json = llm_result.parsed
    llm_blocks = judge_json.get("blocks", []) if isinstance(judge_json, dict) else []
    llm_flags = judge_json.get("flags", []) if isinstance(judge_json, dict) else []
    blocks = merge_issues(deterministic.blocks, llm_blocks)
    flags = merge_issues(deterministic.flags, llm_flags)
    verdict = compute_verdict(blocks, flags, llm_result.parse_error)
    return {
        "verdict": verdict,
        "block_count": len(blocks),
        "flag_count": len(flags),
        "blocks": blocks,
        "flags": flags,
        "judge_json": judge_json,
        "parse_error": llm_result.parse_error,
        "rationale": judge_json.get("rationale", "") if isinstance(judge_json, dict) else "",
        "bullet_evals": bullet_evals or [],
    }

