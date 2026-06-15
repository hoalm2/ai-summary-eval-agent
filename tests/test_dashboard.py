from __future__ import annotations

import unittest

from main import build_daily_trends, issue_group, render_dashboard


class DashboardTests(unittest.TestCase):
    def test_issue_group_maps_taxonomy_for_judges(self) -> None:
        self.assertEqual(issue_group("A_logic_temporal"), "A")
        self.assertEqual(issue_group("B_unsupported"), "B")
        self.assertEqual(issue_group("buy_price_timing"), "BUY")
        self.assertEqual(issue_group("C_disclaimer_omission"), "C")
        self.assertEqual(issue_group("format"), "FMT")
        self.assertEqual(issue_group("render"), "RENDER")

    def test_build_daily_trends_groups_by_eval_date(self) -> None:
        trends = build_daily_trends(
            [
                {
                    "created_at": "2026-06-11T10:00:00Z",
                    "verdict": "PASS",
                    "blocks": [],
                    "flags": [],
                },
                {
                    "created_at": "2026-06-11T11:00:00Z",
                    "verdict": "FAIL",
                    "blocks": [{"category": "A_factual"}, {"category": "buy_price_timing"}],
                    "flags": [],
                },
            ]
        )

        self.assertEqual(trends[0]["date"], "2026-06-11")
        self.assertEqual(trends[0]["total"], 2)
        self.assertEqual(trends[0]["pass"], 1)
        self.assertEqual(trends[0]["hallucination"], 1)
        self.assertEqual(trends[0]["buy_violation"], 1)

    def test_render_dashboard_shows_trend_taxonomy_and_hides_report_text(self) -> None:
        html = render_dashboard(
            {
                "total_evaluated": 2,
                "pass_count": 1,
                "fail_count": 1,
                "flag_count": 0,
                "error_count": 0,
                "pass_rate": 0.5,
                "hallucination_count": 1,
                "hallucination_rate": 0.5,
                "buy_violation_count": 1,
                "failure_breakdown": {"A_factual": 1, "buy_price_timing": 1},
            },
            [
                {
                    "created_at": "2026-06-11T10:00:00Z",
                    "verdict": "FAIL",
                    "blocks": [
                        {
                            "category": "A_factual",
                            "summary_quote": "12.000 tỷ",
                            "explanation": "Số liệu không có trong report.",
                        }
                    ],
                    "flags": [],
                    "skeleton_json": {},
                    "judge_json": {},
                    "report": {
                        "ticker": "VTP",
                        "report_date": "2026-06-10",
                        "report_text": "SECRET FULL REPORT TEXT SHOULD NOT RENDER",
                    },
                    "summary": {"summary_text": "• Doanh thu 12.000 tỷ."},
                }
            ],
        )

        self.assertIn("Pass rate trend", html)
        self.assertIn("Failure patterns", html)
        self.assertIn("Type A — factual/logic hallucination", html)
        self.assertIn("BUY — buy price violation", html)
        self.assertIn("50%", html)
        self.assertNotIn("SECRET FULL REPORT TEXT SHOULD NOT RENDER", html)


if __name__ == "__main__":
    unittest.main()
