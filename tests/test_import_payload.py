import unittest

from pipeline.import_payload import build_report_payload, extract_items, get_summary_model, get_summary_text


class ImportPayloadTest(unittest.TestCase):
    def test_extracts_items_wrapper_and_summary_response(self):
        item = {
            "symbol": "FRT",
            "report_url": "https://cdn.simplize.vn/simplizevn/report/FRT/report.pdf",
            "response": [
                {"title": "Mở rộng chuỗi Long Châu", "content": "Long Châu mở thêm cửa hàng."},
                {"title": "Lợi nhuận tăng trưởng cao", "content": "Lãi 2026 dự kiến tăng."},
            ],
        }

        self.assertEqual(extract_items({"items": [item]}), [item])
        self.assertEqual(
            build_report_payload(item),
            {
                "ticker": "FRT",
                "report_date": None,
                "source_pdf_url": "https://cdn.simplize.vn/simplizevn/report/FRT/report.pdf",
                "pdf_storage_path": None,
                "report_text": None,
                "status": "pending",
            },
        )
        self.assertEqual(
            get_summary_text(item),
            "• Mở rộng chuỗi Long Châu: Long Châu mở thêm cửa hàng.\n"
            "• Lợi nhuận tăng trưởng cao: Lãi 2026 dự kiến tăng.",
        )
        self.assertEqual(get_summary_model(item), "precreated")

    def test_supports_original_reports_envelope(self):
        item = {
            "ticker": "PNJ",
            "issue_date": "13/05/2026",
            "attached_link": "https://cdn.simplize.vn/simplizevn/report/PNJ/report.pdf",
            "summary_text": "• PNJ tăng trưởng.",
            "summary_model": "internal-v1",
        }

        self.assertEqual(extract_items({"data": {"reports": [item]}}), [item])
        payload = build_report_payload(item)
        self.assertEqual(payload["ticker"], "PNJ")
        self.assertEqual(payload["report_date"], "2026-05-13")
        self.assertEqual(payload["source_pdf_url"], "https://cdn.simplize.vn/simplizevn/report/PNJ/report.pdf")
        self.assertEqual(get_summary_text(item), "• PNJ tăng trưởng.")
        self.assertEqual(get_summary_model(item), "internal-v1")

    def test_empty_response_means_missing_summary(self):
        self.assertIsNone(get_summary_text({"response": []}))
        self.assertIsNone(get_summary_text({"summary_text": "   "}))


if __name__ == "__main__":
    unittest.main()
