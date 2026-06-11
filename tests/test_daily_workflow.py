from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import patch

from main import generate_and_evaluate_report_safely


class FakeStore:
    def __init__(self) -> None:
        self.summaries: list[dict[str, Any]] = []
        self.eval_runs: list[dict[str, Any]] = []

    def insert_summary(self, payload: dict[str, Any]) -> dict[str, Any]:
        summary = {**payload, "id": f"summary-{len(self.summaries) + 1}"}
        self.summaries.append(summary)
        return summary

    def insert_eval_run(self, **payload: Any) -> dict[str, Any]:
        eval_run = {**payload, "id": f"eval-{len(self.eval_runs) + 1}"}
        self.eval_runs.append(eval_run)
        return eval_run


class DailyWorkflowTests(unittest.TestCase):
    def test_safe_daily_eval_persists_error_and_continues(self) -> None:
        store = FakeStore()
        bad_report = {"id": "bad-report", "ticker": "BAD"}
        good_report = {"id": "good-report", "ticker": "GOOD"}

        def fake_generate(report: dict[str, Any], _: FakeStore) -> dict[str, Any]:
            if report["id"] == "bad-report":
                raise RuntimeError("judge timed out")
            return {"result": {"verdict": "PASS"}, "report_id": report["id"]}

        with patch("main.generate_and_evaluate_report", side_effect=fake_generate):
            outputs = [
                generate_and_evaluate_report_safely(bad_report, store),
                generate_and_evaluate_report_safely(good_report, store),
            ]

        self.assertEqual(outputs[0]["result"]["verdict"], "ERROR")
        self.assertEqual(outputs[1]["result"]["verdict"], "PASS")
        self.assertEqual(store.summaries[0]["summary_model"], "unexpected_error")
        self.assertEqual(store.eval_runs[0]["report_id"], "bad-report")
        self.assertEqual(store.eval_runs[0]["verdict"], "ERROR")


if __name__ == "__main__":
    unittest.main()
