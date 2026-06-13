from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from openai import APIConnectionError, APITimeoutError, OpenAI

from config import Settings, get_settings, require_env


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
        if not self.settings.mock_llm_mode:
            self.client = OpenAI(
                base_url=self.settings.greennode_base_url,
                api_key=require_env("GREENNODE_API_KEY", self.settings.greennode_api_key),
                timeout=self.settings.request_timeout_seconds,
            )

    def json_chat(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int,
        temperature: float = 0.1,
    ) -> LLMResult:
        if self.settings.mock_llm_mode:
            raw = self._mock_json_response(system_prompt=system_prompt, user_prompt=user_prompt)
            return LLMResult(parsed=safe_json_parse(raw), raw=raw, parse_error=False)

        raw = self._chat_once(
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            use_json_mode=self.settings.greennode_json_mode,
        )
        try:
            return LLMResult(parsed=safe_json_parse(raw), raw=raw, parse_error=False)
        except Exception:
            retry_raw = self._chat_once(
                model=model,
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
        verdict = "FAIL" if blocks or len(flags) >= 2 else "PASS-WITH-FLAG" if flags else "PASS"
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
