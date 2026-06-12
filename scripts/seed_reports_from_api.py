"""Seed reports and pre-created summaries from Simplize API JSON response.

Usage:
    python scripts/seed_reports_from_api.py < response.json
    python scripts/seed_reports_from_api.py response.json

Supported input shapes:
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
    {
        "items": [
            {
                "symbol": "FRT",
                "report_url": "https://cdn.simplize.vn/...pdf",
                "response": [
                    {"title": "Mở rộng chuỗi Long Châu", "content": "..."}
                ]
            }
        ]
    }

Mapping:
    ticker/symbol              -> reports.ticker
    issue_date/report_date      -> reports.report_date
    attached_link/report_url    -> reports.source_pdf_url
    summary_text/response[]     -> summaries.summary_text

Skips existing source_pdf_url to be idempotent.
"""

from __future__ import annotations

import json
import sys
import argparse
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.persist import SupabaseStore
from pipeline.import_payload import build_report_payload, extract_items, get_summary_model, get_summary_text


def main() -> None:
    load_dotenv()

    parser = argparse.ArgumentParser(description="Seed reports and pre-created summaries from Simplize JSON.")
    parser.add_argument("input", nargs="?", help="JSON file path. Reads stdin when omitted.")
    parser.add_argument(
        "--attach-missing-summaries",
        action="store_true",
        help="When a report already exists, insert its summary only if that report has no summary yet.",
    )
    args = parser.parse_args()

    if args.input:
        raw = Path(args.input).read_text(encoding="utf-8")
    else:
        raw = sys.stdin.read()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # File chứa các objects nối tiếp nhau không có dấu [] bọc ngoài
        raw = raw.strip().rstrip(",")
        data = json.loads(f"[{raw}]")

    items = extract_items(data)

    if not items:
        print("No reports found in input.", file=sys.stderr)
        sys.exit(1)

    store = SupabaseStore()
    inserted = summaries_inserted = summaries_missing = skipped = errors = 0

    for item in items:
        payload = build_report_payload(item)
        source_pdf_url = payload.get("source_pdf_url")
        ticker = payload.get("ticker") or item.get("id") or "UNKNOWN"
        report_date = payload.get("report_date") or "no-date"
        if not source_pdf_url:
            print(f"  SKIP id={item.get('id')} — no source_pdf_url/report_url/attached_link")
            skipped += 1
            continue

        try:
            existing = store.find_existing_report(
                ticker=payload.get("ticker"),
                report_date=payload.get("report_date"),
                source_pdf_url=source_pdf_url,
            )
            if existing:
                summary_text = get_summary_text(item)
                if args.attach_missing_summaries and summary_text and not store.find_summary_for_report(existing["id"]):
                    summary_model = get_summary_model(item)
                    store.insert_summary(
                        {
                            "report_id": existing["id"],
                            "summary_text": summary_text,
                            "summary_model": summary_model,
                        }
                    )
                    print(f"  INSERT SUMMARY {ticker} {report_date} — {summary_model} (existing report)")
                    summaries_inserted += 1
                elif args.attach_missing_summaries and not summary_text:
                    print(f"  SKIP SUMMARY MISSING {ticker} {report_date}")
                    summaries_missing += 1
                print(f"  EXISTS {ticker} {report_date} — {source_pdf_url}")
                skipped += 1
                continue

            report = store.insert_report(payload)
            print(f"  INSERT REPORT {ticker} {report_date} — {source_pdf_url}")
            inserted += 1
            summary_text = get_summary_text(item)
            if summary_text:
                summary_model = get_summary_model(item)
                store.insert_summary(
                    {
                        "report_id": report["id"],
                        "summary_text": summary_text,
                        "summary_model": summary_model,
                    }
                )
                print(f"  INSERT SUMMARY {ticker} {report_date} — {summary_model}")
                summaries_inserted += 1
            else:
                print(f"  SKIP SUMMARY MISSING {ticker} {report_date}")
                summaries_missing += 1
        except Exception as exc:
            print(f"  ERROR  id={item.get('id')} — {exc}", file=sys.stderr)
            errors += 1

    print(
        f"\nDone: {inserted} reports inserted, {summaries_inserted} summaries inserted, "
        f"{skipped} skipped, {summaries_missing} summaries missing, {errors} errors"
    )


if __name__ == "__main__":
    main()
