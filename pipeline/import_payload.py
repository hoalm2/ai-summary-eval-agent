from __future__ import annotations

from datetime import datetime
from typing import Any


DEFAULT_SUMMARY_MODEL = "precreated"


def extract_items(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if not isinstance(data, dict):
        return []
    for key in ("reports", "items"):
        items = data.get(key)
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
    nested_reports = data.get("data", {}).get("reports") if isinstance(data.get("data"), dict) else None
    if isinstance(nested_reports, list):
        return [item for item in nested_reports if isinstance(item, dict)]
    return []


def parse_report_date(raw: Any) -> str | None:
    if not raw:
        return None
    value = str(raw).strip()
    if not value:
        return None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value[:10], fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return value[:10]


def format_response_summary(response: Any) -> str | None:
    if isinstance(response, str):
        return response.strip() or None
    if not isinstance(response, list):
        return None
    bullets: list[str] = []
    for entry in response:
        if isinstance(entry, str):
            content = entry.strip()
            if content:
                bullets.append(f"• {content}")
            continue
        if not isinstance(entry, dict):
            continue
        title = str(entry.get("title") or "").strip()
        content = str(entry.get("content") or "").strip()
        if title and content:
            bullets.append(f"• {title}: {content}")
        elif content:
            bullets.append(f"• {content}")
        elif title:
            bullets.append(f"• {title}")
    return "\n".join(bullets).strip() or None


def get_ticker(item: dict[str, Any]) -> str | None:
    value = item.get("ticker") or item.get("symbol")
    return str(value).strip() if value else None


def get_source_pdf_url(item: dict[str, Any]) -> str | None:
    value = item.get("source_pdf_url") or item.get("attached_link") or item.get("report_url")
    return str(value).strip() if value else None


def get_summary_text(item: dict[str, Any]) -> str | None:
    value = item.get("summary_text")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return format_response_summary(item.get("response"))


def get_summary_model(item: dict[str, Any]) -> str:
    value = item.get("summary_model")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return DEFAULT_SUMMARY_MODEL


def build_report_payload(item: dict[str, Any]) -> dict[str, Any]:
    report_text = item.get("report_text")
    if isinstance(report_text, str):
        report_text = report_text.strip() or None
    else:
        report_text = None
    return {
        "ticker": get_ticker(item),
        "report_date": parse_report_date(item.get("report_date") or item.get("issue_date")),
        "source_pdf_url": get_source_pdf_url(item),
        "report_text": report_text,
        "status": item.get("status") or ("ready" if report_text else "pending"),
    }
