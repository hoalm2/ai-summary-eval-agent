"""Seed reports table from Simplize API JSON response.

Usage:
    python scripts/seed_reports_from_api.py < response.json
    python scripts/seed_reports_from_api.py response.json

Input shape (from Simplize API):
    {
        "data": {
            "reports": [
                {
                    "id": 6805012,
                    "ticker": "PVS",
                    "issue_date": "10/06/2026",
                    "attached_link": "https://cdn.simplize.vn/...pdf",
                    ...
                }
            ]
        }
    }

Mapping:
    ticker       -> ticker
    issue_date   -> report_date (DD/MM/YYYY -> YYYY-MM-DD)
    attached_link -> source_pdf_url

Upserts on source_pdf_url to be idempotent.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.persist import SupabaseStore


def parse_issue_date(raw: str) -> str:
    """Convert DD/MM/YYYY to YYYY-MM-DD."""
    return datetime.strptime(raw.strip(), "%d/%m/%Y").strftime("%Y-%m-%d")


def build_payload(item: dict) -> dict:
    return {
        "ticker": item.get("ticker") or None,
        "report_date": parse_issue_date(item["issue_date"]) if item.get("issue_date") else None,
        "source_pdf_url": item.get("attached_link") or None,
        "pdf_storage_path": None,
        "report_text": None,
        "status": "pending",
        "external_id": item.get("id") or None,
        "ticker_name": item.get("ticker_name") or None,
        "industry_name": item.get("industry_name") or None,
        "report_type": item.get("report_type") or None,
        "source": item.get("source") or None,
        "title": item.get("title") or None,
        "file_name": item.get("file_name") or None,
        "target_price": item.get("target_price") or None,
        "recommend": item.get("recommend") or None,
    }


def main() -> None:
    load_dotenv()

    if len(sys.argv) > 1:
        raw = Path(sys.argv[1]).read_text(encoding="utf-8")
    else:
        raw = sys.stdin.read()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # File chứa các objects nối tiếp nhau không có dấu [] bọc ngoài
        raw = raw.strip().rstrip(",")
        data = json.loads(f"[{raw}]")

    # Support: full API envelope / bare list / bare array of objects
    if isinstance(data, list):
        items = data
    else:
        items = data.get("data", {}).get("reports", [])

    if not items:
        print("No reports found in input.", file=sys.stderr)
        sys.exit(1)

    store = SupabaseStore()
    inserted = skipped = errors = 0

    for item in items:
        source_pdf_url = item.get("attached_link")
        if not source_pdf_url:
            print(f"  SKIP id={item.get('id')} — no attached_link")
            skipped += 1
            continue

        try:
            existing = store.find_existing_report(
                ticker=item.get("ticker"),
                report_date=parse_issue_date(item["issue_date"]) if item.get("issue_date") else None,
                source_pdf_url=source_pdf_url,
            )
            if existing:
                print(f"  EXISTS {item.get('ticker')} {item.get('issue_date')} — {source_pdf_url}")
                skipped += 1
                continue

            payload = build_payload(item)
            store.insert_report(payload)
            print(f"  INSERT {item.get('ticker')} {item.get('issue_date')} — {source_pdf_url}")
            inserted += 1
        except Exception as exc:
            print(f"  ERROR  id={item.get('id')} — {exc}", file=sys.stderr)
            errors += 1

    print(f"\nDone: {inserted} inserted, {skipped} skipped, {errors} errors")


if __name__ == "__main__":
    main()
