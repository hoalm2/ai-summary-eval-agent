# Runbook: Seed Reports từ Simplize API vào Supabase

## Mục đích

Đổ danh sách báo cáo từ JSON response của Simplize API vào bảng `reports` trong Supabase, để pipeline eval có thể chạy Stage 0 (fetch PDF) và các stage tiếp theo.

---

## Bước 1 — Chạy migration SQL (chỉ làm 1 lần)

Vào **Supabase Dashboard → SQL Editor**, chạy:

```sql
alter table reports
  add column if not exists external_id   bigint,
  add column if not exists ticker_name   text,
  add column if not exists industry_name text,
  add column if not exists report_type   integer,
  add column if not exists source        text,
  add column if not exists title         text,
  add column if not exists file_name     text,
  add column if not exists target_price  bigint,
  add column if not exists recommend     text;

create unique index if not exists idx_reports_external_id
  on reports(external_id)
  where external_id is not null;
```

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
| Bare array | `[{...}, {...}]` |
| Objects nối tiếp (không có `[]`) | `{...}, {...}` |

---

## Bước 3 — Chạy seed script

```bash
cd "/Users/lap14895/Documents/Auto eval - AI summary"
source .venv/bin/activate
python scripts/seed_reports_from_api.py /tmp/reports.json
```

**Output mẫu:**
```
  INSERT PVS 2026-06-10 — https://cdn.simplize.vn/...pdf
  INSERT VHC 2026-06-10 — https://cdn.simplize.vn/...pdf
  EXISTS MWG 2026-06-09 — https://...   ← bỏ qua nếu đã có
  ...
Done: 95 inserted, 2 skipped, 0 errors
```

Script idempotent — chạy lại không bị duplicate vì kiểm tra `source_pdf_url` trước khi insert.

---

## Mapping JSON → DB

| JSON field | Cột trong `reports` | Ghi chú |
|---|---|---|
| `id` | `external_id` | ID từ Simplize (bigint) |
| `ticker` | `ticker` | |
| `ticker_name` | `ticker_name` | |
| `industry_name` | `industry_name` | |
| `report_type` | `report_type` | |
| `source` | `source` | Tên CTCK |
| `issue_date` | `report_date` | DD/MM/YYYY → YYYY-MM-DD tự động |
| `title` | `title` | |
| `attached_link` | `source_pdf_url` | URL PDF để Stage 0 fetch |
| `file_name` | `file_name` | |
| `target_price` | `target_price` | |
| `recommend` | `recommend` | |
| — | `status` | Set `"pending"` (default) |
| `issue_date_time_ago` | — | Không lưu (derived field) |

---

## Xử lý lỗi thường gặp

| Lỗi | Nguyên nhân | Cách sửa |
|---|---|---|
| `JSONDecodeError: Extra data` | JSON thiếu `[` `]` bọc ngoài | Script tự xử lý, không cần làm gì |
| `ModuleNotFoundError: dotenv` | Chưa activate venv | `source .venv/bin/activate` trước |
| `zsh: command not found: python` | macOS không có `python`, chỉ có `python3` | Dùng venv (đã có `python` trong venv) |
| Duplicate insert | `source_pdf_url` đã tồn tại | Script tự skip, in `EXISTS` |
