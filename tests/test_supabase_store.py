from __future__ import annotations

import unittest
from typing import Any

from pipeline.persist import SupabaseStore


class Response:
    def __init__(self, data: list[dict[str, Any]]) -> None:
        self.data = data


class FakeTable:
    def __init__(self, data: list[dict[str, Any]], *, fail_select_count: int = 0) -> None:
        self.data = data
        self.fail_select_count = fail_select_count
        self.selected = ""
        self.limit_value: int | None = None

    def select(self, value: str, **_: Any) -> "FakeTable":
        if self.fail_select_count:
            self.fail_select_count -= 1
            raise RuntimeError("embed unsupported")
        self.selected = value
        return self

    def is_(self, *_: Any) -> "FakeTable":
        return self

    def or_(self, *_: Any) -> "FakeTable":
        return self

    def in_(self, *_: Any) -> "FakeTable":
        return self

    def order(self, *_: Any, **__: Any) -> "FakeTable":
        return self

    def limit(self, value: int) -> "FakeTable":
        self.limit_value = value
        return self

    def execute(self) -> Response:
        return Response(self.data[: self.limit_value])


class FakeClient:
    def __init__(self, tables: dict[str, FakeTable]) -> None:
        self.tables = tables

    def table(self, name: str) -> FakeTable:
        return self.tables[name]


class SupabaseStoreTests(unittest.TestCase):
    def test_fetch_unevaluated_summaries_strips_embedded_eval_runs_and_attaches_reports(self) -> None:
        store = object.__new__(SupabaseStore)
        store.client = FakeClient(
            {
                "summaries": FakeTable(
                    [
                        {
                            "id": "summary-1",
                            "report_id": "report-1",
                            "summary_text": "• Summary",
                            "summary_model": "precreated",
                            "eval_runs": [],
                        }
                    ]
                ),
                "reports": FakeTable(
                    [
                        {
                            "id": "report-1",
                            "ticker": "AAA",
                            "report_text": "text",
                            "source_pdf_url": None,
                        }
                    ]
                ),
            }
        )

        records = SupabaseStore.fetch_unevaluated_summaries(store, limit=5)

        self.assertEqual(
            records,
            [
                {
                    "summary": {
                        "id": "summary-1",
                        "report_id": "report-1",
                        "summary_text": "• Summary",
                        "summary_model": "precreated",
                    },
                    "report": {
                        "id": "report-1",
                        "ticker": "AAA",
                        "report_text": "text",
                        "source_pdf_url": None,
                    },
                }
            ],
        )

    def test_fetch_unevaluated_summaries_fallback_filters_evaluated_summaries(self) -> None:
        store = object.__new__(SupabaseStore)
        store.client = FakeClient(
            {
                "eval_runs": FakeTable([{"summary_id": "already-done"}]),
                "summaries": FakeTable(
                    [
                        {"id": "already-done", "report_id": "report-1", "summary_text": "done"},
                        {"id": "ready-summary", "report_id": "report-2", "summary_text": "ready"},
                    ],
                    fail_select_count=1,
                ),
                "reports": FakeTable(
                    [
                        {"id": "report-1", "ticker": "OLD"},
                        {"id": "report-2", "ticker": "NEW"},
                    ]
                ),
            }
        )

        records = SupabaseStore.fetch_unevaluated_summaries(store, limit=5)

        self.assertEqual([record["summary"]["id"] for record in records], ["ready-summary"])

    def test_fetch_unevaluated_reports_strips_embedded_eval_runs(self) -> None:
        store = object.__new__(SupabaseStore)
        store.client = FakeClient(
            {
                "reports": FakeTable(
                    [
                        {
                            "id": "report-1",
                            "ticker": "AAA",
                            "report_text": "text",
                            "source_pdf_url": None,
                            "eval_runs": [],
                        }
                    ]
                )
            }
        )

        reports = SupabaseStore.fetch_unevaluated_reports(store, limit=5)

        self.assertEqual(reports, [{"id": "report-1", "ticker": "AAA", "report_text": "text", "source_pdf_url": None}])

    def test_fetch_unevaluated_reports_fallback_filters_evaluated_reports(self) -> None:
        store = object.__new__(SupabaseStore)
        store.client = FakeClient(
            {
                "eval_runs": FakeTable([{"report_id": "already-done"}]),
                "reports": FakeTable(
                    [
                        {"id": "already-done", "report_text": "text", "source_pdf_url": None},
                        {"id": "missing-source", "report_text": "", "source_pdf_url": None},
                        {"id": "ready-text", "report_text": "text", "source_pdf_url": None},
                        {"id": "ready-pdf", "report_text": "", "source_pdf_url": "https://cdn.simplize.vn/a.pdf"},
                    ],
                    fail_select_count=1,
                ),
            }
        )

        reports = SupabaseStore.fetch_unevaluated_reports(store, limit=5)

        self.assertEqual([report["id"] for report in reports], ["ready-text", "ready-pdf"])


if __name__ == "__main__":
    unittest.main()
