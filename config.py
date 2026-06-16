from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

from dotenv import load_dotenv


load_dotenv()


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _csv_env(name: str, default: str = "") -> set[str]:
    return {item.strip().lower() for item in os.getenv(name, default).split(",") if item.strip()}


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value is not None and value.strip() else default


def _float_env(name: str, default: float) -> float:
    value = os.getenv(name)
    return float(value) if value is not None and value.strip() else default


@dataclass(frozen=True)
class Settings:
    greennode_api_key: str
    greennode_base_url: str
    greennode_json_mode: bool
    mock_llm_mode: bool
    model_skeleton: str
    model_align: str
    model_summary: str
    model_judge: str
    model_fallback: str
    anthropic_api_key: str
    supabase_url: str
    supabase_service_role_key: str
    demo_token: str
    port: int
    allowed_pdf_hosts: set[str]
    request_timeout_seconds: float = 60.0
    skeleton_max_tokens: int = 1800
    summary_max_tokens: int = 900
    judge_max_tokens: int = 1800
    daily_batch_size: int = 5
    demo_batch_size: int = 2
    report_text_min_chars: int = 80


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        greennode_api_key=os.getenv("GREENNODE_API_KEY", ""),
        greennode_base_url=os.getenv("GREENNODE_BASE_URL", "https://maas-llm-aiplatform-hcm.api.vngcloud.vn/v1"),
        greennode_json_mode=_bool_env("GREENNODE_JSON_MODE", True),
        mock_llm_mode=_bool_env("MOCK_LLM_MODE", False),
        model_skeleton=os.getenv("MODEL_SKELETON", "gemini/gemini-3.1-pro-preview"),
        model_align=os.getenv("MODEL_ALIGN", os.getenv("MODEL_SKELETON", "gemini/gemini-3.1-pro-preview")),
        model_summary=os.getenv("MODEL_SUMMARY", "openai/gpt-5-mini"),
        model_judge=os.getenv("MODEL_JUDGE", "gemini/gemini-3.1-pro-preview"),
        model_fallback=os.getenv("MODEL_FALLBACK", "deepseek/deepseek-v4-pro"),
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
        supabase_url=os.getenv("SUPABASE_URL", ""),
        supabase_service_role_key=os.getenv("SUPABASE_SERVICE_ROLE_KEY", ""),
        demo_token=os.getenv("DEMO_TOKEN", ""),
        port=int(os.getenv("PORT", "8080")),
        allowed_pdf_hosts=_csv_env("ALLOWED_PDF_HOSTS", "cdn.simplize.vn"),
        request_timeout_seconds=_float_env("REQUEST_TIMEOUT_SECONDS", 60.0),
        skeleton_max_tokens=_int_env("SKELETON_MAX_TOKENS", 1800),
        summary_max_tokens=_int_env("SUMMARY_MAX_TOKENS", 900),
        judge_max_tokens=_int_env("JUDGE_MAX_TOKENS", 1800),
        daily_batch_size=_int_env("DAILY_BATCH_SIZE", 5),
        demo_batch_size=_int_env("DEMO_BATCH_SIZE", 2),
        report_text_min_chars=_int_env("REPORT_TEXT_MIN_CHARS", 80),
    )


def require_env(name: str, value: str) -> str:
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value
