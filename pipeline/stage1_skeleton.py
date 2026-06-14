from __future__ import annotations

import logging
from typing import Any

from openai import RateLimitError

from config import Settings, get_settings
from pipeline.llm import LLMClient, read_prompt

logger = logging.getLogger(__name__)


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
    try:
        result = llm_client.json_chat(
            model=settings.model_skeleton,
            fallback_model=settings.model_fallback,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=settings.skeleton_max_tokens,
        )
        return result.parsed
    except RateLimitError:
        logger.warning("Skeleton extraction rate-limited on all models; bypassing stage — returning empty skeleton")
        return {}

