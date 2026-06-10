from __future__ import annotations

import re
from dataclasses import dataclass


NUMBER_PATTERN = re.compile(
    r"(?<!\w)(?:\d{1,3}(?:[.,]\d{3})+|\d+)(?:[.,]\d+)?(?:(?:\s*(?:đồng|tỷ|triệu|nghìn)\b)|(?:\s*%)|(?:đ(?![A-Za-zÀ-ỹ]))|(?:\s*x\b))?",
    re.IGNORECASE,
)
DATE_PATTERN = re.compile(
    r"\b(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}[/-]\d{1,2}[/-]\d{1,2}|Q[1-4][/-]?\d{2,4}|20\d{2})\b",
    re.IGNORECASE,
)
UPSIDE_PATTERN = re.compile(r"(?:upside|tiềm năng tăng(?: giá)?|tăng giá)\D{0,24}([+-]?\d+(?:[,.]\d+)?)\s*%", re.IGNORECASE)
TIMESTAMP_PATTERN = re.compile(r"(?:ngày|as of|tại ngày|cập nhật)\s*\d{1,2}[/-]\d{1,2}[/-]\d{2,4}", re.IGNORECASE)


@dataclass
class FactcheckResult:
    blocks: list[dict]
    flags: list[dict]


def normalize_token(value: str) -> str:
    return re.sub(r"\s+", "", value.lower()).replace(",", ".")


def extract_tokens(text: str) -> set[str]:
    tokens = {normalize_token(match.group(0)) for match in NUMBER_PATTERN.finditer(text)}
    tokens.update(normalize_token(match.group(0)) for match in DATE_PATTERN.finditer(text))
    return {token for token in tokens if token}


def deterministic_factcheck(report_text: str, summary_text: str) -> FactcheckResult:
    report_tokens = extract_tokens(report_text)
    summary_tokens = extract_tokens(summary_text)
    blocks: list[dict] = []

    for token in sorted(summary_tokens - report_tokens):
        blocks.append(
            {
                "category": "A_factual",
                "summary_quote": token,
                "report_evidence": "not present in report",
                "explanation": "Số liệu/ngày trong summary không xuất hiện trong report theo kiểm tra deterministic.",
                "source": "deterministic_factcheck",
            }
        )

    for match in UPSIDE_PATTERN.finditer(summary_text):
        upside_phrase = match.group(0)
        report_has_upside = normalize_token(match.group(1) + "%") in report_tokens or upside_phrase.lower() in report_text.lower()
        summary_has_timestamp = bool(TIMESTAMP_PATTERN.search(summary_text))
        if not report_has_upside or not summary_has_timestamp:
            blocks.append(
                {
                    "category": "buy_price_upside",
                    "summary_quote": upside_phrase,
                    "report_evidence": "not present in report" if not report_has_upside else "upside present but summary lacks timestamp",
                    "explanation": "Upside % chỉ được phép khi report có nêu và summary kèm timestamp.",
                    "source": "deterministic_factcheck",
                }
            )

    return FactcheckResult(blocks=blocks, flags=[])


def merge_issues(*issue_lists: list[dict]) -> list[dict]:
    seen: set[tuple[str, str]] = set()
    merged: list[dict] = []
    for issues in issue_lists:
        for issue in issues:
            key = (issue.get("category", ""), issue.get("summary_quote", ""))
            if key in seen:
                continue
            seen.add(key)
            merged.append(issue)
    return merged


def compute_verdict(blocks: list[dict], flags: list[dict], parse_error: bool = False) -> str:
    if parse_error:
        return "ERROR"
    if len(blocks) >= 1 or len(flags) >= 2:
        return "FAIL"
    if len(flags) == 1:
        return "PASS-WITH-FLAG"
    return "PASS"
