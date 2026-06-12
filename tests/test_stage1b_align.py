from __future__ import annotations

import unittest
from typing import Any

from pipeline.llm import LLMResult
from pipeline.stage1b_align import align_bullets


class FakeAlignClient:
    def __init__(self, parsed: Any, *, parse_error: bool = False) -> None:
        self.parsed = parsed
        self.parse_error = parse_error
        self.last_user_prompt = ""

    def json_chat(self, **kwargs: Any) -> LLMResult:
        self.last_user_prompt = kwargs["user_prompt"]
        return LLMResult(parsed=self.parsed, raw="{}", parse_error=self.parse_error)


class Stage1bAlignTests(unittest.TestCase):
    def test_align_bullets_accepts_wrapped_bullet_evals_object(self) -> None:
        client = FakeAlignClient(
            {
                "bullet_evals": [
                    {
                        "bullet_index": 1,
                        "bullet_text": "• Doanh thu 2025 dự kiến đạt 10.000 tỷ đồng.",
                        "report_citations": ["Doanh thu 2025 dự kiến đạt 10.000 tỷ đồng"],
                    }
                ]
            }
        )

        result = align_bullets(
            "Doanh thu 2025 dự kiến đạt 10.000 tỷ đồng.",
            "• Doanh thu 2025 dự kiến đạt 10.000 tỷ đồng.",
            llm_client=client,
        )

        self.assertEqual(result[0]["bullet_index"], 1)
        self.assertEqual(result[0]["report_citations"], ["Doanh thu 2025 dự kiến đạt 10.000 tỷ đồng"])
        self.assertIn("<REPORT>", client.last_user_prompt)
        self.assertIn("<SUMMARY>", client.last_user_prompt)

    def test_align_bullets_accepts_legacy_top_level_list(self) -> None:
        result = align_bullets(
            "Lợi nhuận cải thiện nhờ biên gộp.",
            "• Lợi nhuận cải thiện nhờ biên gộp.",
            llm_client=FakeAlignClient(
                [
                    {
                        "bullet_index": 1,
                        "bullet_text": "• Lợi nhuận cải thiện nhờ biên gộp.",
                        "report_citations": ["Lợi nhuận cải thiện nhờ biên gộp"],
                    }
                ]
            ),
        )

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["bullet_index"], 1)

    def test_align_bullets_degrades_to_empty_list_on_parse_error(self) -> None:
        result = align_bullets(
            "Report text",
            "• Summary text.",
            llm_client=FakeAlignClient({"_parse_error": True}, parse_error=True),
        )

        self.assertEqual(result, [])

    def test_align_bullets_degrades_to_empty_list_on_unexpected_shape(self) -> None:
        result = align_bullets(
            "Report text",
            "• Summary text.",
            llm_client=FakeAlignClient({"not_bullet_evals": []}),
        )

        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
