from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.persist import SupabaseStore


FIXTURES = [
    {
        "ticker": "PASS",
        "report_date": "2026-06-10",
        "report_text": "Giá mục tiêu 12 tháng 52.000đ dựa trên P/E 12x so với trung vị lịch sử 10x. Lợi nhuận cải thiện nhờ biên gộp tăng. Rủi ro: giá đầu vào có thể tăng trong 2026.",
        "summary_text": "• Giá mục tiêu 12 tháng 52.000đ, P/E 12x so với trung vị lịch sử 10x. • Lợi nhuận cải thiện nhờ biên gộp tăng. • Lưu ý rủi ro giá đầu vào có thể tăng trong 2026.",
    },
    {
        "ticker": "WRONGNUM",
        "report_date": "2026-06-10",
        "report_text": "Doanh thu 2025 dự kiến đạt 10.000 tỷ đồng, tăng 12% so với cùng kỳ.",
        "summary_text": "• Doanh thu 2025 dự kiến đạt 12.000 tỷ đồng, tăng 12% so với cùng kỳ.",
    },
    {
        "ticker": "BUY",
        "report_date": "2026-06-10",
        "report_text": "Chúng tôi duy trì giá mục tiêu 12 tháng 52.000đ dựa trên P/E mục tiêu 12x.",
        "summary_text": "• Cổ phiếu có tiềm năng tăng giá +30%, nên mua ngay.",
    },
    {
        "ticker": "TONE",
        "report_date": "2026-06-10",
        "report_text": "Lợi nhuận Q3 cải thiện nhờ biên gộp tăng.",
        "summary_text": "• Lợi nhuận Q3 bứt phá mạnh mẽ và tăng vọt nhờ biên gộp.",
    },
    {
        "ticker": "TEMP",
        "report_date": "2026-06-10",
        "report_text": "Ban lãnh đạo kỳ vọng lợi nhuận 2026 có thể phục hồi nếu nhu cầu xuất khẩu tốt hơn.",
        "summary_text": "• Lợi nhuận 2026 đã phục hồi nhờ nhu cầu xuất khẩu tốt hơn.",
    },
    {
        "ticker": "FLAG",
        "report_date": "2026-06-10",
        "report_text": "Giá mục tiêu 52.000đ, định giá hấp dẫn so với lịch sử. Rủi ro chính: giá nguyên liệu đầu vào có thể tăng mạnh trong 2026.",
        "summary_text": "• Giá mục tiêu 12 tháng 52.000đ, định giá hấp dẫn so với lịch sử.",
    },
]


def main() -> None:
    load_dotenv()
    store = SupabaseStore()
    for fixture in FIXTURES:
        report = store.insert_report(
            {
                "ticker": fixture["ticker"],
                "report_date": fixture["report_date"],
                "source_pdf_url": None,
                "pdf_storage_path": None,
                "report_text": fixture["report_text"],
                "status": "ready",
            }
        )
        store.insert_summary(
            {
                "report_id": report["id"],
                "summary_text": fixture["summary_text"],
                "summary_model": "demo_seed",
            }
        )
        print(f"Seeded {fixture['ticker']}")


if __name__ == "__main__":
    main()

