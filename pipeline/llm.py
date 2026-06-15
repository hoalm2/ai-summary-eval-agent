from __future__ import annotations

import json
import logging
import random
import re
import time
from dataclasses import dataclass
from typing import Any

import anthropic as _anthropic_sdk

from openai import APIConnectionError, APITimeoutError, OpenAI, RateLimitError

from config import Settings, get_settings, require_env

logger = logging.getLogger(__name__)

_RATE_LIMIT_RETRY_WAIT_SECONDS = 65.0


FENCE_PATTERN = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)


@dataclass
class LLMResult:
    parsed: dict[str, Any]
    raw: str
    parse_error: bool


def safe_json_parse(text: str) -> dict[str, Any]:
    cleaned = FENCE_PATTERN.sub("", text.strip()).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end >= start:
        cleaned = cleaned[start : end + 1]
    return json.loads(cleaned)


class LLMClient:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.client: OpenAI | None = None
        self.anthropic_client: _anthropic_sdk.Anthropic | None = None
        if not self.settings.mock_llm_mode:
            self.client = OpenAI(
                base_url=self.settings.greennode_base_url,
                api_key=require_env("GREENNODE_API_KEY", self.settings.greennode_api_key),
                timeout=self.settings.request_timeout_seconds,
            )
            if self.settings.anthropic_api_key:
                self.anthropic_client = _anthropic_sdk.Anthropic(api_key=self.settings.anthropic_api_key)

    @staticmethod
    def _is_anthropic_model(model: str) -> bool:
        return model.startswith("claude-")

    def json_chat(
        self,
        *,
        model: str,
        fallback_model: str | None = None,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int,
        temperature: float = 0.1,
    ) -> LLMResult:
        if self.settings.mock_llm_mode:
            raw = self._mock_json_response(system_prompt=system_prompt, user_prompt=user_prompt)
            return LLMResult(parsed=safe_json_parse(raw), raw=raw, parse_error=False)

        if self._is_anthropic_model(model):
            return self.chat_anthropic(model=model, system_prompt=system_prompt, user_prompt=user_prompt, max_tokens=max_tokens)

        raw = self._chat_with_retry(
            model=model,
            fallback_model=fallback_model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            use_json_mode=self.settings.greennode_json_mode,
        )
        try:
            return LLMResult(parsed=safe_json_parse(raw), raw=raw, parse_error=False)
        except Exception:
            retry_raw = self._chat_with_retry(
                model=model,
                fallback_model=fallback_model,
                system_prompt=system_prompt,
                user_prompt=f"{user_prompt}\n\nReturn only valid JSON, no prose.",
                max_tokens=max_tokens,
                temperature=temperature,
                use_json_mode=False,
            )
            try:
                return LLMResult(parsed=safe_json_parse(retry_raw), raw=retry_raw, parse_error=False)
            except Exception:
                return LLMResult(
                    parsed={"_parse_error": True, "raw": retry_raw},
                    raw=retry_raw,
                    parse_error=True,
                )

    def text_chat(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int,
        temperature: float = 0.1,
    ) -> str:
        if self.settings.mock_llm_mode:
            return "• Mock summary: nội dung này chỉ dùng để test local, không gọi GreenNode."

        return self._chat_once(
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            use_json_mode=False,
        )

    def _chat_with_retry(
        self,
        *,
        model: str,
        fallback_model: str | None,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int,
        temperature: float,
        use_json_mode: bool,
    ) -> str:
        """Wraps _chat_once with one 429-backoff retry then optional fallback model."""
        models_to_try = [model]
        if fallback_model and fallback_model != model:
            models_to_try.append(fallback_model)

        call_kwargs: dict[str, Any] = dict(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            use_json_mode=use_json_mode,
        )
        last_exc: Exception = RuntimeError("No models attempted")
        for m in models_to_try:
            try:
                return self._chat_once(model=m, **call_kwargs)
            except RateLimitError as exc:
                wait = _RATE_LIMIT_RETRY_WAIT_SECONDS + random.uniform(0, 5)
                logger.warning("429 RateLimitError on %s; waiting %.0fs then retrying", m, wait)
                time.sleep(wait)
                try:
                    return self._chat_once(model=m, **call_kwargs)
                except RateLimitError as exc2:
                    last_exc = exc2
                    if m != models_to_try[-1]:
                        logger.warning("429 persists on %s; trying fallback model", m)
                    else:
                        logger.error("429 persists on fallback %s; all models exhausted", m)

        raise last_exc

    def _chat_once(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int,
        temperature: float,
        use_json_mode: bool,
    ) -> str:
        if self.client is None:
            raise RuntimeError("LLM client is not initialized outside mock mode.")
        if self._uses_responses_api(model):
            return self._responses_once(
                model=model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_tokens=max_tokens,
            )

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if use_json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        try:
            response = self.client.chat.completions.create(**kwargs)
        except (APITimeoutError, APIConnectionError):
            response = self.client.chat.completions.create(**kwargs)

        content = response.choices[0].message.content
        return content or ""

    def _uses_responses_api(self, model: str) -> bool:
        return model.startswith("openai/gpt-5")

    def _responses_once(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int,
    ) -> str:
        if self.client is None:
            raise RuntimeError("LLM client is not initialized outside mock mode.")
        response = self.client.responses.create(
            model=model,
            input=[
                {"role": "assistant", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_output_tokens=max_tokens,
            reasoning={"effort": "medium"},
        )
        output_text = getattr(response, "output_text", None)
        if output_text:
            return output_text
        chunks: list[str] = []
        for item in getattr(response, "output", []) or []:
            for content in getattr(item, "content", []) or []:
                text = getattr(content, "text", None)
                if text:
                    chunks.append(text)
        return "".join(chunks)

    def chat_anthropic(
        self,
        *,
        model: str = "claude-sonnet-4-6",
        system_prompt: str,
        user_prompt: str,
        max_tokens: int,
    ) -> LLMResult:
        """Call Anthropic API directly (used as final fallback when GreenNode is rate-limited)."""
        if self.anthropic_client is None:
            raise RuntimeError("Anthropic client not initialised — set ANTHROPIC_API_KEY in .env")
        message = self.anthropic_client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        raw = message.content[0].text if message.content else ""
        try:
            return LLMResult(parsed=safe_json_parse(raw), raw=raw, parse_error=False)
        except Exception:
            retry_prompt = f"{user_prompt}\n\nReturn only valid JSON, no prose."
            retry_msg = self.anthropic_client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system_prompt,
                messages=[{"role": "user", "content": retry_prompt}],
            )
            retry_raw = retry_msg.content[0].text if retry_msg.content else ""
            try:
                return LLMResult(parsed=safe_json_parse(retry_raw), raw=retry_raw, parse_error=False)
            except Exception:
                return LLMResult(parsed={"_parse_error": True, "raw": retry_raw}, raw=retry_raw, parse_error=True)

    def _mock_json_response(self, *, system_prompt: str, user_prompt: str) -> str:
        if "align each bullet" in system_prompt.lower():
            return json.dumps(
                {
                    "bullet_evals": [
                        {
                            "bullet_index": 1,
                            "bullet_text": "Mock bullet 1",
                            "report_citations": ["MOCK_LLM_MODE bullet alignment; no GreenNode tokens used."],
                        }
                    ]
                },
                ensure_ascii=False,
            )

        if "Summary Eval Judge" not in system_prompt:
            return json.dumps(
                {
                    "ticker": "",
                    "report_date": "",
                    "thesis_points": [],
                    "key_risks": [],
                    "financial_highlights": [],
                    "disclaimers": [],
                    "notes": "MOCK_LLM_MODE skeleton; no GreenNode tokens used.",
                },
                ensure_ascii=False,
            )

        summary = user_prompt.lower()
        bullets = [line.strip() for line in user_prompt.split("\n") if line.strip().startswith("•")]

        def _bullet_index_for(quote: str) -> int | None:
            q = quote.lower()
            for i, b in enumerate(bullets, start=1):
                if q in b.lower():
                    return i
            return 1 if bullets else None

        blocks: list[dict] = []
        flags: list[dict] = []
        if "nên mua ngay" in summary or "tiềm năng tăng giá" in summary:
            blocks.append(
                {
                    "category": "buy_price_timing",
                    "bullet_index": _bullet_index_for("nên mua ngay"),
                    "summary_quote": "nên mua ngay",
                    "report_evidence": "not present in report",
                    "explanation": "Mock judge phát hiện framing thời điểm mua.",
                }
            )
        if "bứt phá" in summary or "tăng vọt" in summary:
            quote = "bứt phá" if "bứt phá" in summary else "tăng vọt"
            blocks.append(
                {
                    "category": "B_tone_escalation",
                    "bullet_index": _bullet_index_for(quote),
                    "summary_quote": quote,
                    "report_evidence": "cải thiện",
                    "explanation": "Mock judge phát hiện tone escalation.",
                }
            )
        if "đã phục hồi" in summary and "kỳ vọng" in summary:
            blocks.append(
                {
                    "category": "A_logic_temporal",
                    "bullet_index": _bullet_index_for("đã phục hồi"),
                    "summary_quote": "đã phục hồi",
                    "report_evidence": "kỳ vọng",
                    "explanation": "Mock judge phát hiện forecast bị trình bày như fact đã xảy ra.",
                }
            )
        if "rủi ro chính" in summary and "summary>" in summary and "rủi ro" not in summary.split("<summary>", 1)[-1]:
            flags.append(
                {
                    "category": "C_disclaimer_omission",
                    "bullet_index": None,
                    "summary_quote": "",
                    "report_evidence": "Rủi ro chính",
                    "explanation": "Mock judge phát hiện omission disclaimer.",
                }
            )
        verdict = "FAIL" if blocks or len(flags) >= 2 else "FLAG" if flags else "PASS"
        return json.dumps(
            {
                "verdict": verdict,
                "block_count": len(blocks),
                "flag_count": len(flags),
                "blocks": blocks,
                "flags": flags,
                "rationale": "MOCK_LLM_MODE judge; no GreenNode tokens used.",
            },
            ensure_ascii=False,
        )


def read_prompt(path: str) -> str:
    with open(path, "r", encoding="utf-8") as prompt_file:
        return prompt_file.read()
