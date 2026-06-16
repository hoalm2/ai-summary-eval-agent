# Kế hoạch Credit GreenNode & Test Plan

## Tóm tắt ví

| Ví | Số dư | Ghi chú |
|---|---|---|
| Ví tổng | 5,000,000 cr | Giữ tối thiểu 2,000,000 cho runtime/registry/openclaw |
| Ví MaaS | 5,000,000 cr | Dùng để gọi model AI |
| **MaaS có thể dùng** | **~5,000,000 cr** | Nạp thêm từ ví tổng nếu cần (không hoàn lại) |

---

## Ước tính chi phí per report

Main flow (`/run-daily`) chạy 3 LLM calls/report:

| Stage | Model | Input (tok) | Output (tok) | Subtotal (tok) | Ghi chú |
|---|---|---|---|---|---|
| Stage 1 — skeleton | gemini/gemini-3.1-pro-preview | ~4,000 | ~600 | ~4,600 | |
| Stage 1b — align | gemini/gemini-3.1-pro-preview | ~4,500 | ~500 | ~5,000 | |
| Stage 3b — judge | openai/gpt-5-mini | ~5,000 | ~700 | ~5,700 | Responses API |
| **Tổng/report** | | **~13,500** | **~1,800** | **~15,300 tok** | |

> ⚠️ Chưa biết tỉ lệ chính xác credit/token của GreenNode — cần verify trên portal (Billing → MaaS pricing) sau lần test đầu.
> Mọi số ở dưới đều dùng giả định worst-case: **1 credit = 1 token**.

---

## Phân bổ credit theo phase

### Phase 0 — Kiểm tra kết nối (1 lần)
```
spike_greennode.py: 2 calls × ~200 tok = ~400 tok
```
**Ước tính: ~400 cr** _(không đáng kể)_

---

### Phase 1 — Local test: Stage 1+1b+3 (inline text, không fetch PDF)

Mục tiêu: xác nhận 3 stage chính hoạt động đúng với GreenNode (Gemini + GPT-5 Mini).

| Test case | Endpoint | Reports | Calls | Token ước tính |
|---|---|---|---|---|
| Golden pass (text sạch) | `/run-one` | 3 | 9 | ~183k |
| FAIL case (text có lỗi) | `/run-one` | 2 | 6 | ~122k |
| **Subtotal** | | **5** | **15** | **~305k** |

**Ước tính: ~305,000 cr**

---

### Phase 2 — Local test: Stage 0 (PDF fetch + extract)

Mục tiêu: xác nhận fetch PDF từ `cdn.simplize.vn` hoạt động và text đủ dài.

| Test case | Endpoint | Reports | Calls | Token ước tính |
|---|---|---|---|---|
| Test 1 PDF cụ thể | `/run-one` + `pdf_path_or_url` | 2 | 6 | ~122k |
| Full pipeline batch nhỏ | `/run-daily` (batch=5) | 5 | 15 | ~305k |
| **Subtotal** | | **7** | **21** | **~427k** |

**Ước tính: ~430,000 cr**

---

### Phase 3 — Deploy lên AgentBase + verify prod

Mục tiêu: smoke test sau deploy, xác nhận endpoint prod hoạt động.

| Test case | Reports | Calls | Token ước tính |
|---|---|---|---|
| Health + status check | 0 | 0 | 0 |
| Smoke test /run-one | 2 | 6 | ~122k |
| Batch prod đầu tiên | 5 | 15 | ~305k |
| **Subtotal** | **7** | **21** | **~427k** |

**Ước tính: ~430,000 cr**

---

### Phase 4 — Production: 15 ngày × 5 reports/ngày

| | Số lượng | Token ước tính |
|---|---|---|
| Reports | 75 | |
| LLM calls | 225 (3 calls/report) | |
| Tokens | | ~1,147,500 |
| Buffer retry/lỗi (~20%) | | ~229,500 |
| **Subtotal** | | **~1,377,000** |

**Ước tính: ~1,380,000 cr**

---

## Tổng hợp

| Phase | Credit ước tính |
|---|---|
| Phase 0 — Connectivity spike | ~1,000 |
| Phase 1 — Local stage test | ~305,000 |
| Phase 2 — Local PDF + full pipeline | ~430,000 |
| Phase 3 — Deploy + verify prod | ~430,000 |
| Phase 4 — Production 15 ngày | ~1,380,000 |
| **Tổng** | **~2,546,000** |
| **MaaS có sẵn** | **5,000,000** |
| **Dư** | **~2,454,000** |

→ Đủ để chạy toàn bộ kế hoạch, còn ~2.45M dự phòng.
→ Chỉ cần nạp thêm từ ví tổng sang MaaS **nếu** giá thực tế tệ hơn ước tính (unlikely).

---

## Kiểm soát pipeline — tránh chạy lãng phí

### Hướng dẫn nhanh cho PM (không cần biết kỹ thuật)

Pipeline chỉ tốn credit khi chạy. Bảng dưới liệt kê các tình huống thường gặp và việc cần làm:

| Tình huống | Việc cần làm | Ai thực hiện |
|---|---|---|
| Sắp bắt đầu test / chạy batch | Nhắn dev **bật pipeline** | PM nhắn dev |
| Xong test, không cần chạy thêm | Nhắn dev **tắt pipeline ngay** | PM nhắn dev |
| Muốn chạy 1 batch ngay bây giờ | Nhắn dev **trigger thủ công** | PM nhắn dev |
| Deploy lên prod xong, chưa ready | Mặc định tắt — chưa cần làm gì | Tự động |
| Sẵn sàng chạy prod hàng ngày | Nhắn dev **bật pipeline** | PM nhắn dev |
| Hết báo cáo để chạy | Pipeline tự ngưng, không báo lỗi | Tự động |
| Muốn dừng hẳn giữa chừng | Nhắn dev **tắt pipeline** | PM nhắn dev |

> **Nguyên tắc:** mặc định nên **tắt**. Chỉ bật đúng lúc cần chạy, xong tắt lại.

---

### Cơ chế (dành cho dev)

Pipeline có kill switch qua Supabase `agent_state`. Mọi trigger (manual hay cron) đều bị chặn khi disabled.

```
agent_state.pipeline_enabled = false  →  /run-daily trả về ngay, 0 LLM call
agent_state.pipeline_enabled = true   →  /run-daily chạy bình thường
```

### Các lệnh

```bash
BASE=http://localhost:8080   # hoặc URL prod sau khi deploy
TOKEN=<DEMO_TOKEN>

# Tắt pipeline (dùng khi không test/chạy prod)
curl -X POST $BASE/pipeline/disable -H "X-Demo-Token: $TOKEN"

# Bật pipeline
curl -X POST $BASE/pipeline/enable  -H "X-Demo-Token: $TOKEN"

# Kiểm tra trạng thái + lần chạy cuối
curl $BASE/pipeline/status

# Trigger manual 1 batch (5 reports)
curl -X POST $BASE/run-daily -H "X-Demo-Token: $TOKEN"
```

### Quy tắc dùng trong test

| Thời điểm | Trạng thái |
|---|---|
| Trước khi bắt đầu test session | DISABLE trước, rồi ENABLE ngay trước khi trigger |
| Sau mỗi test session | DISABLE ngay |
| Khi deploy lên prod | DISABLE mặc định, ENABLE khi sẵn sàng chạy hàng ngày |
| Sau 15 ngày prod hoặc hết reports | Tự ngưng (code đã xử lý), không cần làm gì thêm |

---

## Checklist trước khi bắt đầu Phase 1

- [ ] `MOCK_LLM_MODE=false` trong `.env`
- [ ] Verify GreenNode API key còn hạn: `python scripts/spike_greennode.py`
- [ ] Kiểm tra giá credit/token thực tế trên GreenNode Portal → cập nhật bảng ước tính nếu lệch
- [ ] Disable pipeline trước khi start server: `curl -X POST .../pipeline/disable`
- [ ] Chỉ enable khi chuẩn bị trigger thủ công
