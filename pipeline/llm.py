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


def read_prompt(path: str) -> str:
    with open(path, "r", encoding="utf-8") as prompt_file:
        return prompt_file.read()

