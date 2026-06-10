from __future__ import annotations

from typing import Any

from config import Settings, get_settings
from pipeline.llm import LLMClient, read_prompt


PROMPT_PATH = "prompts/skeleton_extraction.md"


def extract_skeleton(
    report_text: str,
    *,
    ticker: str | None = None,
    report_date: str | None = None,
    llm_client: LLMClient | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    llm_client = llm_client or LLMClient(settings)
    system_prompt = read_prompt(PROMPT_PATH)
    user_prompt = f"""
Extract a faithful skeleton from this Vietnamese stock research report.

Ticker: {ticker or ""}
Report date: {report_date or ""}

<REPORT>
{report_text}
</REPORT>
"""
    result = llm_client.json_chat(
        model=settings.model_skeleton,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        max_tokens=settings.skeleton_max_tokens,
    )
    return result.parsed

