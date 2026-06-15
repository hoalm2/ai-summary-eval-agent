from __future__ import annotations

GOLDEN_EVAL_CASES = [
    {
        "name": "pass_supported_target_price",
        "report_text": (
            "Giá mục tiêu 12 tháng 52.000đ dựa trên P/E 12x so với trung vị lịch sử 10x. "
            "Lợi nhuận cải thiện nhờ biên gộp tăng. Rủi ro: giá đầu vào có thể tăng trong 2026."
        ),
        "summary_text": (
            "• Giá mục tiêu 12 tháng 52.000đ, P/E 12x so với trung vị lịch sử 10x.\n"
            "• Lợi nhuận cải thiện nhờ biên gộp tăng.\n"
            "• Lưu ý rủi ro giá đầu vào có thể tăng trong 2026."
        ),
        "judge_blocks": [],
        "judge_flags": [],
        "expected_verdict": "PASS",
        "expected_categories": set(),
    },
    {
        "name": "wrong_number_type_a",
        "report_text": "Doanh thu 2025 dự kiến đạt 10.000 tỷ đồng, tăng 12% so với cùng kỳ.",
        "summary_text": "• Doanh thu 2025 dự kiến đạt 12.000 tỷ đồng, tăng 12% so với cùng kỳ.",
        "judge_blocks": [],
        "judge_flags": [],
        "expected_verdict": "FAIL",
        "expected_categories": {"A_factual"},
    },
    {
        "name": "temporal_distortion_type_a",
        "report_text": "Ban lãnh đạo kỳ vọng lợi nhuận 2026 có thể phục hồi nếu nhu cầu xuất khẩu tốt hơn.",
        "summary_text": "• Lợi nhuận 2026 đã phục hồi nhờ nhu cầu xuất khẩu tốt hơn.",
        "judge_blocks": [
            {
                "category": "A_logic_temporal",
                "summary_quote": "đã phục hồi",
                "report_evidence": "kỳ vọng lợi nhuận 2026 có thể phục hồi",
                "explanation": "Summary trình bày kỳ vọng tương lai như sự thật đã xảy ra.",
            }
        ],
        "judge_flags": [],
        "expected_verdict": "FAIL",
        "expected_categories": {"A_logic_temporal"},
    },
    {
        "name": "causal_misattribution_type_a",
        "report_text": "Biên lợi nhuận cải thiện nhờ giá nguyên liệu giảm, trong khi doanh thu đi ngang.",
        "summary_text": "• Biên lợi nhuận cải thiện nhờ doanh thu tăng mạnh.",
        "judge_blocks": [
            {
                "category": "A_logic_causal_wrong",
                "summary_quote": "nhờ doanh thu tăng mạnh",
                "report_evidence": "nhờ giá nguyên liệu giảm",
                "explanation": "Summary gán kết quả đúng cho nguyên nhân sai.",
            }
        ],
        "judge_flags": [],
        "expected_verdict": "FAIL",
        "expected_categories": {"A_logic_causal_wrong"},
    },
    {
        "name": "fabricated_causal_link_type_a",
        "report_text": "Doanh thu tăng 8%. Biên gộp cải thiện trong quý nhờ cơ cấu sản phẩm tốt hơn.",
        "summary_text": "• Doanh thu tăng 8% giúp biên gộp cải thiện.",
        "judge_blocks": [
            {
                "category": "A_logic_causal_fabricated",
                "summary_quote": "Doanh thu tăng 8% giúp biên gộp cải thiện",
                "report_evidence": "Doanh thu tăng 8%. Biên gộp cải thiện",
                "explanation": "Report nêu hai quan sát nhưng không nói doanh thu là nguyên nhân.",
            }
        ],
        "judge_flags": [],
        "expected_verdict": "FAIL",
        "expected_categories": {"A_logic_causal_fabricated"},
    },
    {
        "name": "unsupported_claim_type_b",
        "report_text": "Công ty đặt kế hoạch doanh thu 2026 tăng 8% nhờ mở rộng kênh phân phối.",
        "summary_text": "• Công ty sẽ mở rộng sang thị trường Indonesia trong 2026.",
        "judge_blocks": [
            {
                "category": "B_unsupported",
                "summary_quote": "mở rộng sang thị trường Indonesia",
                "report_evidence": "not present in report",
                "explanation": "Report không đề cập thị trường Indonesia.",
            }
        ],
        "judge_flags": [],
        "expected_verdict": "FAIL",
        "expected_categories": {"B_unsupported"},
    },
    {
        "name": "fabricated_conclusion_type_b",
        "report_text": "Kịch bản phục hồi lợi nhuận có thể xảy ra nếu nhu cầu xuất khẩu cải thiện.",
        "summary_text": "• Lợi nhuận chắc chắn phục hồi nhờ nhu cầu xuất khẩu cải thiện.",
        "judge_blocks": [
            {
                "category": "B_fabricated_conclusion",
                "summary_quote": "chắc chắn phục hồi",
                "report_evidence": "có thể xảy ra nếu nhu cầu xuất khẩu cải thiện",
                "explanation": "Summary biến giả thuyết có điều kiện thành kết luận chắc chắn.",
            }
        ],
        "judge_flags": [],
        "expected_verdict": "FAIL",
        "expected_categories": {"B_fabricated_conclusion"},
    },
    {
        "name": "tone_escalation_type_b",
        "report_text": "Lợi nhuận Q3 cải thiện nhờ biên gộp tăng.",
        "summary_text": "• Lợi nhuận Q3 bứt phá mạnh mẽ và tăng vọt nhờ biên gộp.",
        "judge_blocks": [
            {
                "category": "B_tone_escalation",
                "summary_quote": "bứt phá mạnh mẽ và tăng vọt",
                "report_evidence": "cải thiện",
                "explanation": "Summary dùng qualifier mạnh hơn mức report hỗ trợ.",
            }
        ],
        "judge_flags": [],
        "expected_verdict": "FAIL",
        "expected_categories": {"B_tone_escalation"},
    },
    {
        "name": "buy_price_timing_block",
        "report_text": "Chúng tôi duy trì giá mục tiêu 12 tháng 52.000đ dựa trên P/E mục tiêu 12x.",
        "summary_text": "• Cổ phiếu có tiềm năng tăng giá +30%, nên mua ngay.",
        "judge_blocks": [
            {
                "category": "buy_price_timing",
                "summary_quote": "nên mua ngay",
                "report_evidence": "not present in report",
                "explanation": "Summary đưa ra framing thời điểm mua cụ thể.",
            }
        ],
        "judge_flags": [],
        "expected_verdict": "FAIL",
        "expected_categories": {"buy_price_upside", "buy_price_timing"},
    },
    {
        "name": "single_disclaimer_omission_flag",
        "report_text": (
            "Giá mục tiêu 52.000đ, định giá hấp dẫn so với lịch sử. "
            "Rủi ro chính: giá nguyên liệu đầu vào có thể tăng mạnh trong 2026."
        ),
        "summary_text": "• Giá mục tiêu 52.000đ, định giá hấp dẫn so với lịch sử.",
        "judge_blocks": [],
        "judge_flags": [
            {
                "category": "C_disclaimer_omission",
                "summary_quote": "",
                "report_evidence": "Rủi ro chính: giá nguyên liệu đầu vào có thể tăng mạnh trong 2026.",
                "explanation": "Summary bỏ caveat quan trọng làm thay đổi cách diễn giải.",
            }
        ],
        "expected_verdict": "FLAG",
        "expected_categories": {"C_disclaimer_omission"},
    },
    {
        "name": "truncation_plus_format_flags_fail",
        "report_text": "Lợi nhuận có thể phục hồi nếu nhu cầu xuất khẩu cải thiện, nhưng rủi ro tỷ giá vẫn cao.",
        "summary_text": "Lợi nhuận có thể phục hồi.",
        "judge_blocks": [],
        "judge_flags": [
            {
                "category": "A_truncation",
                "summary_quote": "Lợi nhuận có thể phục hồi",
                "report_evidence": "nếu nhu cầu xuất khẩu cải thiện, nhưng rủi ro tỷ giá vẫn cao",
                "explanation": "Summary bỏ điều kiện và rủi ro làm thay đổi mức confidence.",
            },
            {
                "category": "format",
                "summary_quote": "Lợi nhuận có thể phục hồi.",
                "report_evidence": "FORMAT_SPEC yêu cầu bullet summary",
                "explanation": "Summary không dùng bullet point.",
            },
        ],
        "expected_verdict": "FAIL",
        "expected_categories": {"A_truncation", "format"},
    },
    {
        "name": "single_render_flag",
        "report_text": "Giá mục tiêu 52.000đ dựa trên P/E 12x.",
        "summary_text": "• Giá mục tiêu 52.000đ dựa trên P/E 12x. �",
        "judge_blocks": [],
        "judge_flags": [
            {
                "category": "render",
                "summary_quote": "�",
                "report_evidence": "visible replacement character",
                "explanation": "Summary có ký tự render lỗi.",
            }
        ],
        "expected_verdict": "FLAG",
        "expected_categories": {"render"},
    },
]
