from __future__ import annotations

from config import Settings, get_settings
from pipeline.llm import LLMClient, read_prompt


PROMPT_PATH = "prompts/summary_generate.md"


def generate_summary(
    report_text: str,
    *,
    ticker: str | None = None,
    llm_client: LLMClient | None = None,
    settings: Settings | None = None,
) -> str:
    settings = settings or get_settings()
    llm_client = llm_client or LLMClient(settings)
    system_prompt = read_prompt(PROMPT_PATH)
    user_prompt = f"""
Ticker: {ticker or ""}

<REPORT>
{report_text}
</REPORT>
"""
    return llm_client.text_chat(
        model=settings.model_summary,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        max_tokens=settings.summary_max_tokens,
    ).strip()

