# memory-probe-V3b

Runner: `local_tests/run_memory_probe.py`, role `memory` (llm.py: khoá ECA, tắt reasoning, timeout 15 s, không retry, temperature 0, trần output 512 token). 3 hội thoại tổng hợp, không đụng DB, không dựng graph.

## Tóm tắt

- Tổng số lần gọi LLM: **3** turn: 3; lỗi gọi (timeout/4xx): turn: 0

| | turn |
|---|---|
| thời gian / lần gọi p50 (s) | 0.61 |
| thời gian / lần gọi p95 (s) | 0.65 |
| thời gian / lần gọi max (s) | 0.65 |
| tổng thời gian / hội thoại p50 / p95 / max (s) | 0.61 / 0.65 / 0.65 |
| lần gọi trả danh sách rỗng | 0/3 (0%) |
| fact kỳ vọng tìm thấy | 3/3 |
| fact kỳ vọng bị bỏ sót | 0 |
| thao tác thừa (extra, người xét) | 0 |
| thao tác lặp lại fact kỳ vọng | 0 |
| thao tác ngoài kỳ vọng nhưng chấp nhận được | 0 |
| cụm CẤM xuất hiện (mỗi cái là một lỗi) | 0 |
| sai category so với kỳ vọng | 0 |
| JSON không parse được | 0 |
| output bị cắt (truncated) | 0 |
| thao tác bị code bỏ, theo lý do | 0 |
| token vào / ra trung bình (ra max) | 1787 / 35 (max 42) |
| hội thoại OK / REVIEW / FAIL | 3 / 0 / 0 |

Ghi chú: OK = tìm đủ, không thừa, không cụm cấm. REVIEW = tìm đủ, không cụm cấm, nhưng có thao tác thừa cần người xét (fact đúng vô hại hay bịa). FAIL = bỏ sót hoặc dính cụm cấm. Cụm cấm chỉ tính trên thao tác đã qua kiểm tra của code; nếu model viết số điện thoại và code bỏ nó thì ghi vào cột "bị code bỏ", không tính là dính cụm cấm (số lần như vậy nằm trong `forbidden_in_dropped` của file JSON).

## Từng hội thoại

### c27_vi_general_instruction (vi, 2026-10-14)

Tin nhắn người dùng: Từ giờ trả lời mình ngắn gọn thôi nhé.

Kỳ vọng: ADD [style] (ngắn gọn)

**turn** — OK — 0.65 s, 1 lần gọi

- lượt 1: 0.65 s, vào/ra 1762/42: ADD [instruction/style] "Trả lời ngắn gọn"

  đúng kỳ vọng 1, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

### c29_vi_repeat_stored (vi, 2026-10-14)

Tin nhắn người dùng: Bác sĩ dặn mình không được ngồi xổm sâu.

Fact đã lưu: f1: Tránh ngồi xổm sâu

Kỳ vọng: REPEAT f1

**turn** — OK — 0.54 s, 1 lần gọi

- lượt 1: 0.54 s, vào/ra 1800/24: REPEAT f1

  đúng kỳ vọng 1, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

### c30_vi_rename (vi, 2026-10-14)

Tin nhắn người dùng: Thôi, từ giờ gọi tôi là Long nhé.

Fact đã lưu: f1: Gọi tôi là Du

Kỳ vọng: UPDATE f1 [style] (Long)
Cụm cấm: `Du`

**turn** — OK — 0.61 s, 1 lần gọi

- lượt 1: 0.61 s, vào/ra 1800/39: UPDATE f1 [style] "Gọi tôi là Long"

  đúng kỳ vọng 1, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

