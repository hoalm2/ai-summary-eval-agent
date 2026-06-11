# Kế hoạch Credit GreenNode & Test Plan

## Tóm tắt ví

| Ví | Số dư | Ghi chú |
|---|---|---|
| Ví tổng | 5,000,000 cr | Giữ tối thiểu 2,000,000 cho runtime/registry/openclaw |
| Ví MaaS | 5,000,000 cr | Dùng để gọi model AI |
| **MaaS có thể dùng** | **~5,000,000 cr** | Nạp thêm từ ví tổng nếu cần (không hoàn lại) |

---

## Ước tính chi phí per report

Mỗi báo cáo chạy qua 4 LLM calls:

| Stage | Model | Input (tok) | Output (tok) | Subtotal (tok) |
|---|---|---|---|---|
| Stage 1 — skeleton | qwen3-5-27b | ~4,000 | ~600 | ~4,600 |
| Stage 2 — summary | qwen3-5-27b | ~4,000 | ~400 | ~4,400 |
| Stage 1b — align | qwen3-5-27b | ~4,500 | ~500 | ~5,000 |
| Stage 3 — judge | gemma-4-31b-it | ~5,000 | ~700 | ~5,700 |
| **Tổng/report** | | **~17,500** | **~2,200** | **~19,700 tok** |

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

### Phase 1 — Local test: Stage 1+2+1b+3 (inline text, không fetch PDF)

Mục tiêu: xác nhận 4 stage hoạt động đúng với GreenNode.

| Test case | Endpoint | Reports | Calls | Token ước tính |
|---|---|---|---|---|
| Golden pass (text sạch) | `/run-one` | 3 | 12 | ~236k |
| FAIL case (text có lỗi) | `/run-one` | 2 | 8 | ~157k |
| **Subtotal** | | **5** | **20** | **~393k** |

**Ước tính: ~400,000 cr**

---

### Phase 2 — Local test: Stage 0 (PDF fetch + extract)

Mục tiêu: xác nhận fetch PDF từ `cdn.simplize.vn` hoạt động và text đủ dài.

| Test case | Endpoint | Reports | Calls | Token ước tính |
|---|---|---|---|---|
| Test 1 PDF cụ thể | `/run-one` + `pdf_path_or_url` | 2 | 8 | ~157k |
| Full pipeline batch nhỏ | `/run-daily` (batch=5) | 5 | 20 | ~394k |
| **Subtotal** | | **7** | **28** | **~551k** |

**Ước tính: ~600,000 cr**

---

### Phase 3 — Deploy lên AgentBase + verify prod

Mục tiêu: smoke test sau deploy, xác nhận endpoint prod hoạt động.

| Test case | Reports | Calls | Token ước tính |
|---|---|---|---|
| Health + status check | 0 | 0 | 0 |
| Smoke test /run-one | 2 | 8 | ~157k |
| Batch prod đầu tiên | 5 | 20 | ~394k |
| **Subtotal** | **7** | **28** | **~551k** |

**Ước tính: ~600,000 cr**

---

### Phase 4 — Production: 15 ngày × 5 reports/ngày

| | Số lượng | Token ước tính |
|---|---|---|
| Reports | 75 | |
| LLM calls | 300 | |
| Tokens | | ~1,477,500 |
| Buffer retry/lỗi (~20%) | | ~295,500 |
| **Subtotal** | | **~1,773,000** |

**Ước tính: ~1,800,000 cr**

---

## Tổng hợp

| Phase | Credit ước tính |
|---|---|
| Phase 0 — Connectivity spike | ~1,000 |
| Phase 1 — Local stage test | ~400,000 |
| Phase 2 — Local PDF + full pipeline | ~600,000 |
| Phase 3 — Deploy + verify prod | ~600,000 |
| Phase 4 — Production 15 ngày | ~1,800,000 |
| **Tổng** | **~3,401,000** |
| **MaaS có sẵn** | **5,000,000** |
| **Dư** | **~1,599,000** |

→ Đủ để chạy toàn bộ kế hoạch, còn ~1.6M dự phòng.
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
