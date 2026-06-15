from __future__ import annotations

import unittest
from typing import Any

from pipeline.factcheck import compute_verdict, deterministic_factcheck
from pipeline.llm import LLMResult
from pipeline.persist import SupabaseStore
from pipeline.stage3_judge import judge_summary
from tests.golden_eval_cases import GOLDEN_EVAL_CASES


class FakeJudgeClient:
    def __init__(self, *, blocks: list[dict[str, Any]], flags: list[dict[str, Any]], parse_error: bool = False) -> None:
        self.blocks = blocks
        self.flags = flags
        self.parse_error = parse_error

    def json_chat(self, **_: Any) -> LLMResult:
        if self.parse_error:
            return LLMResult(parsed={"_parse_error": True}, raw="not json", parse_error=True)
        return LLMResult(
            parsed={
                "verdict": "IGNORED_BY_CODE",
                "block_count": len(self.blocks),
                "flag_count": len(self.flags),
                "blocks": self.blocks,
                "flags": self.flags,
                "rationale": "fake judge for golden tests",
            },
            raw="{}",
            parse_error=False,
        )


class EvalGoldenTests(unittest.TestCase):
    def test_compute_verdict_rules(self) -> None:
        self.assertEqual(compute_verdict([], []), "PASS")
        self.assertEqual(compute_verdict([], [{"category": "format"}]), "FLAG")
        self.assertEqual(compute_verdict([], [{"category": "format"}, {"category": "render"}]), "FAIL")
        self.assertEqual(compute_verdict([{"category": "A_factual"}], []), "FAIL")
        self.assertEqual(compute_verdict([], [], parse_error=True), "ERROR")

    def test_deterministic_factcheck_blocks_unseen_numbers(self) -> None:
        result = deterministic_factcheck(
            "Doanh thu 2025 dự kiến đạt 10.000 tỷ đồng, tăng 12% so với cùng kỳ.",
            "• Doanh thu 2025 dự kiến đạt 12.000 tỷ đồng, tăng 12% so với cùng kỳ.",
        )

        self.assertTrue(any(issue["category"] == "A_factual" for issue in result.blocks))

    def test_golden_eval_cases(self) -> None:
        for case in GOLDEN_EVAL_CASES:
            with self.subTest(case=case["name"]):
                result = judge_summary(
                    report_text=case["report_text"],
                    summary_text=case["summary_text"],
                    skeleton_json={},
                    llm_client=FakeJudgeClient(blocks=case["judge_blocks"], flags=case["judge_flags"]),
                )
                categories = {issue["category"] for issue in result["blocks"] + result["flags"]}

                self.assertEqual(result["verdict"], case["expected_verdict"])
                self.assertTrue(case["expected_categories"].issubset(categories))

    def test_parse_error_becomes_error_verdict(self) -> None:
        result = judge_summary(
            report_text="Báo cáo hợp lệ với giá mục tiêu 52.000đ.",
            summary_text="• Giá mục tiêu 52.000đ.",
            skeleton_json={},
            llm_client=FakeJudgeClient(blocks=[], flags=[], parse_error=True),
        )

        self.assertEqual(result["verdict"], "ERROR")

    def test_aggregate_counts_all_hallucination_categories(self) -> None:
        store = object.__new__(SupabaseStore)
        store.fetch_eval_runs = lambda limit=1000: [
            {"verdict": "FAIL", "blocks": [{"category": "A_logic_temporal"}], "flags": []},
            {"verdict": "FAIL", "blocks": [{"category": "B_fabricated_conclusion"}], "flags": []},
            {"verdict": "FAIL", "blocks": [{"category": "buy_price_timing"}], "flags": []},
        ]

        aggregate = SupabaseStore.aggregate(store)

        self.assertEqual(aggregate["hallucination_count"], 2)
        self.assertEqual(aggregate["buy_violation_count"], 1)


if __name__ == "__main__":
    unittest.main()
