# Runbook: Seed Reports + Pre-created Summaries vào Supabase

## Mục đích

Đổ danh sách báo cáo từ JSON response của Simplize API vào Supabase, để pipeline eval có thể chạy Stage 0 (fetch PDF/report text) và các stage eval tiếp theo.

Flow seed mới vẫn dùng **một input JSON**, nhưng backend/script sẽ ghi vào **2 bảng**:

| Bảng | Vai trò |
|---|---|
| `reports` | Metadata report, PDF URL, optional extracted `report_text` |
| `summaries` | Pre-created `summary_text`, linked với `reports.id` qua `report_id` |

Không thêm `summary_text` vào `reports` và không drop bảng `summaries`. Bảng `summaries` vẫn là source cho summary text trong `eval_runs` + dashboard hiện tại.

---

---

## Bước 2 — Lấy JSON từ API Simplize

Gọi API lấy danh sách báo cáo, copy toàn bộ response JSON.

Lưu vào file:
```bash
pbpaste > /tmp/reports.json
```

**Định dạng JSON được hỗ trợ** (script tự detect):

| Dạng | Ví dụ |
|---|---|
| Full API envelope | `{"code": 0, "data": {"reports": [...]}}` |
| Summary response envelope | `{"items": [{"symbol": "FRT", "report_url": "...pdf", "response": [...]}]}` |
| Bare array | `[{...}, {...}]` |
| Objects nối tiếp (không có `[]`) | `{...}, {...}` |

### Enrich JSON với pre-created summary

Nếu đã có summary tạo sẵn, thêm trực tiếp vào từng report item:

```json
{
  "id": 123456,
  "ticker": "PNJ",
  "ticker_name": "CTCP Vàng bạc Đá quý Phú Nhuận",
  "issue_date": "13/05/2026",
  "title": "Khởi đầu thuận lợi",
  "attached_link": "https://cdn.simplize.vn/simplizevn/report/PNJ/Khoi_au_thuan_loi.pdf",
  "summary_text": "• PNJ ghi nhận Q1/2026 doanh thu 17.245 tỷ và LNST 1.467 tỷ, lần lượt tăng 79% và 117% YoY.\n• DSC dự báo doanh thu 2026 đạt 44.694 tỷ đồng và LNST đạt 3.576 tỷ đồng.",
  "summary_model": "precreated"
}
```

`summary_model` optional. Nếu không có, script/backend nên dùng default `"precreated"`.

Nếu API trả summary trong field `response[]` thay vì `summary_text`, không cần enrich lại thủ công. Script/backend sẽ tự convert:

```json
{
  "symbol": "FRT",
  "report_url": "https://cdn.simplize.vn/simplizevn/report/FRT/_Khoi_au_tich_cuc.pdf",
  "response": [
    {
      "title": "Mở rộng chuỗi Long Châu",
      "content": "Long Châu là động lực chính, dự kiến tăng trưởng mạnh..."
    }
  ],
  "source": "DSC",
  "target_price": "180.00",
  "created_at": "2026-06-12T16:02:18+07:00"
}
```

Thành:

```text
• Mở rộng chuỗi Long Châu: Long Châu là động lực chính, dự kiến tăng trưởng mạnh...
```

Ghi chú cho format `items[]`:

- `symbol` map vào `reports.ticker`.
- `report_url` map vào `reports.source_pdf_url`.
- `response[]` map vào `summaries.summary_text`.
- Không map `created_at` thành `report_date` vì đây là thời điểm response được tạo, không chắc là ngày phát hành report.
- `expected_profit`, `reference_price`, `target_price`, `source`, `updated_at` hiện không cần cho eval pipeline; bỏ qua an toàn nếu chưa có cột metadata tương ứng.

---

## Bước 3 — Chạy seed script

```bash
cd "/Users/lap14895/Documents/Auto eval - AI summary"
source .venv/bin/activate
python scripts/seed_reports_from_api.py /tmp/reports.json
```

Nếu reports đã được seed từ trước nhưng lúc đó chưa có summaries, chạy backfill an toàn:

```bash
python scripts/seed_reports_from_api.py /tmp/reports.json --attach-missing-summaries
```

Flag này chỉ insert summary khi report đã tồn tại **và chưa có summary nào**; nếu report đã có summary thì vẫn skip để tránh duplicate.

**Output mẫu:**
```
  INSERT REPORT PVS 2026-06-10 — https://cdn.simplize.vn/...pdf
  INSERT SUMMARY PVS 2026-06-10 — precreated
  INSERT REPORT VHC 2026-06-10 — https://cdn.simplize.vn/...pdf
  SKIP SUMMARY MISSING VHC 2026-06-10
  EXISTS MWG 2026-06-09 — https://...   ← bỏ qua report + summary nếu đã có và skip_existing=true
  ...
Done: 95 reports inserted, 72 summaries inserted, 2 skipped, 21 summaries missing, 0 errors
```

Script idempotent — chạy lại không bị duplicate vì kiểm tra `source_pdf_url` trước khi insert.

Behavior cần giữ:

| Tình huống | Kết quả mong muốn |
|---|---|
| Item có `summary_text` không rỗng | Insert `reports`, sau đó insert `summaries` với `report_id` vừa tạo |
| Item không có `summary_text` | Chỉ insert `reports`; in `SKIP SUMMARY MISSING` |
| Duplicate report và `skip_existing=true` | Skip cả report + summary để tránh duplicate |
| Duplicate report + `--attach-missing-summaries` | Không insert report; chỉ insert summary nếu report existing chưa có summary |
| `summary_model` thiếu | Dùng `"precreated"` |
| `summary_text` rỗng hoặc chỉ whitespace | Không insert summary |

---

## Mapping JSON → DB

### Report fields → `reports`

| JSON field | Cột trong `reports` | Ghi chú |
|---|---|---|
| `ticker` | `ticker` | |
| `issue_date` | `report_date` | DD/MM/YYYY → YYYY-MM-DD tự động |
| `attached_link` | `source_pdf_url` | URL PDF để Stage 0 fetch |
| — | `status` | Set `"pending"` (default) |
| `id`, `ticker_name`, `industry_name`, `report_type`, `source`, `title`, `file_name`, `target_price`, `recommend`, `issue_date_time_ago` | — | Không lưu (cột metadata không dùng trong eval pipeline) |

### Summary fields → `summaries`

| JSON field | Cột trong `summaries` | Ghi chú |
|---|---|---|
| — | `report_id` | ID của report vừa insert |
| `summary_text` | `summary_text` | Pre-created summary dùng cho Stage 1b + Stage 3 |
| `response[]` | `summary_text` | Auto-convert từng `{title, content}` thành bullet `• Title: content` |
| `summary_model` | `summary_model` | Optional, default `"precreated"` |
| — | `created_at` | Supabase tự set |

Lưu ý: `summary_text` không được lưu vào `reports`; bảng `summaries` vẫn là nơi lưu summary để dashboard và `eval_runs.summary_id` hoạt động như hiện tại.

---

## Verify sau khi seed

Chạy trong Supabase SQL Editor:

```sql
select count(*) as reports_count from reports;
select count(*) as summaries_count from summaries;

select
  r.ticker,
  r.report_date,
  r.source_pdf_url,
  s.summary_model,
  left(s.summary_text, 160) as summary_preview
from summaries s
join reports r on r.id = s.report_id
order by s.created_at desc
limit 10;
```

Kỳ vọng:

- `reports_count` tăng theo số report seed mới.
- `summaries_count` tăng theo số item có `summary_text`.
- Query join trả được `ticker`, `report_date`, `source_pdf_url`, `summary_model`, `summary_preview`.

---

## Xử lý lỗi thường gặp

| Lỗi | Nguyên nhân | Cách sửa |
|---|---|---|
| `JSONDecodeError: Extra data` | JSON thiếu `[` `]` bọc ngoài | Script tự xử lý, không cần làm gì |
| `ModuleNotFoundError: dotenv` | Chưa activate venv | `source .venv/bin/activate` trước |
| `zsh: command not found: python` | macOS không có `python`, chỉ có `python3` | Dùng venv (đã có `python` trong venv) |
| Duplicate insert | `source_pdf_url` đã tồn tại | Script tự skip, in `EXISTS` |
| JSON không có `summary_text` | Simplize API gốc chỉ có metadata/PDF | Report vẫn import được, nhưng production-like daily eval cần summary pre-created để chạy |
| `summary_text` rỗng | Field có nhưng empty/whitespace | Không insert summary; enrich JSON lại trước khi chạy eval |
| Duplicate summary | Report đã có summary hoặc report bị skip | Không insert thêm summary nếu `skip_existing=true`; tránh duplicate eval input |
| `/run-daily` không pick report | Report có PDF nhưng chưa có summary pre-created trong flow mới | Seed thêm `summary_text` vào `summaries` hoặc dùng `/run-one` để test inline |
