from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import get_settings, require_env
from pipeline.llm import safe_json_parse


def call_once(client: OpenAI, *, json_mode: bool) -> tuple[str, bool, float]:
    settings = get_settings()
    started = time.perf_counter()
    kwargs = {
        "model": settings.model_judge,
        "messages": [
            {"role": "system", "content": "Return only valid JSON."},
            {"role": "user", "content": 'Return {"ok": true, "provider": "greennode"}'},
        ],
        "temperature": 0,
        "max_tokens": 80,
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    response = client.chat.completions.create(**kwargs)
    elapsed = time.perf_counter() - started
    raw = response.choices[0].message.content or ""
    try:
        safe_json_parse(raw)
        parse_ok = True
    except Exception:
        parse_ok = False
    return raw, parse_ok, elapsed


def main() -> None:
    load_dotenv()
    settings = get_settings()
    client = OpenAI(
        base_url=settings.greennode_base_url,
        api_key=require_env("GREENNODE_API_KEY", settings.greennode_api_key),
        timeout=settings.request_timeout_seconds,
    )

    total_started = time.perf_counter()
    for index in range(2):
        raw, parse_ok, elapsed = call_once(client, json_mode=False)
        print(f"\n--- call {index + 1} raw ({elapsed:.2f}s, parse_ok={parse_ok}) ---")
        print(raw)

    try:
        raw, parse_ok, elapsed = call_once(client, json_mode=True)
        print(f"\n--- json mode raw ({elapsed:.2f}s, parse_ok={parse_ok}) ---")
        print(raw)
        print(json.dumps({"json_mode_supported": parse_ok}, ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({"json_mode_supported": False, "error": str(exc)}, ensure_ascii=False))

    print(json.dumps({"total_latency_seconds": round(time.perf_counter() - total_started, 2)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
