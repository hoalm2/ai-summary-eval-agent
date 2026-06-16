from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.pdf import extract_pdf_text
from pipeline.persist import SupabaseStore
from pipeline.stage2_summary import generate_summary


RAW_PATH = Path("data/fixture_raw.json")


def find_pdf_url(record: dict[str, Any]) -> str:
    for key in ("attachedLink", "pdf_url", "source_pdf_url", "url"):
        value = record.get(key)
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            return value
    raise ValueError(f"Cannot find PDF URL in record keys: {sorted(record.keys())}")


def get_first(record: dict[str, Any], *keys: str, default: str = "") -> str:
    for key in keys:
        value = record.get(key)
        if value is not None:
            return str(value)
    return default


def main() -> None:
    load_dotenv()
    if not RAW_PATH.exists():
        raise SystemExit("Missing data/fixture_raw.json. Paste DevTools JSON there first.")

    raw_data = json.loads(RAW_PATH.read_text(encoding="utf-8"))
    records = raw_data.get("data", raw_data) if isinstance(raw_data, dict) else raw_data
    if not isinstance(records, list):
        raise SystemExit("fixture_raw.json must be a JSON array or an object with a data array.")

    store = SupabaseStore()
    inserted = 0
    for record in records[:50]:
        source_pdf_url = find_pdf_url(record)
        ticker = get_first(record, "ticker", "symbol", "stockCode", default="UNKNOWN")
        report_date = get_first(record, "issueDate", "report_date", "date", default="")
        report_text = extract_pdf_text(source_pdf_url)
        summary_text = generate_summary(report_text, ticker=ticker)
        report = store.insert_report(
            {
                "ticker": ticker,
                "report_date": report_date or None,
                "source_pdf_url": source_pdf_url,
                "report_text": report_text,
                "status": "ready",
            }
        )
        store.insert_summary(
            {
                "report_id": report["id"],
                "summary_text": summary_text,
                "summary_model": "fixture_setup",
            }
        )
        inserted += 1
        print(f"Inserted fixture {inserted}: {ticker} {report_date}")

    print(json.dumps({"inserted": inserted}, ensure_ascii=False))


if __name__ == "__main__":
    main()
