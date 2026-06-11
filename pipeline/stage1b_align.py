from __future__ import annotations

from typing import Any

from config import Settings, get_settings
from pipeline.llm import LLMClient, read_prompt


PROMPT_PATH = "prompts/bullet_alignment.md"


def align_bullets(
    report_text: str,
    summary_text: str,
    *,
    llm_client: LLMClient | None = None,
    settings: Settings | None = None,
) -> list[dict[str, Any]]:
    """For each bullet in summary_text, find verbatim supporting quotes from report_text.

    Returns a list of bullet_eval dicts:
        [{"bullet_index": 1, "bullet_text": "...", "report_citations": ["...", ...]}, ...]

    Returns [] on parse error so downstream stages degrade gracefully.
    In production (no Stage 2), call this immediately after Stage 1a with the
    pre-existing summary from the internal DB.
    """
    settings = settings or get_settings()
    llm_client = llm_client or LLMClient(settings)
    system_prompt = read_prompt(PROMPT_PATH)
    user_prompt = f"""
<REPORT>
{report_text}
</REPORT>
<SUMMARY>
{summary_text}
</SUMMARY>
"""
    result = llm_client.json_chat(
        model=settings.model_skeleton,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        max_tokens=settings.skeleton_max_tokens,
    )
    if result.parse_error:
        return []
    parsed = result.parsed
    if isinstance(parsed, dict) and "bullet_evals" in parsed:
        evals = parsed["bullet_evals"]
        return evals if isinstance(evals, list) else []
    if isinstance(parsed, list):
        return parsed
    return []
