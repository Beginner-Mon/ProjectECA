# memory-probe-V3

Runner: `local_tests/run_memory_probe.py`, role `memory` (llm.py: khoá ECA, tắt reasoning, timeout 15 s, không retry, temperature 0, trần output 512 token). 7 hội thoại tổng hợp, không đụng DB, không dựng graph.

## Tóm tắt

- Tổng số lần gọi LLM: **8** turn: 8; lỗi gọi (timeout/4xx): turn: 0

| | turn |
|---|---|
| thời gian / lần gọi p50 (s) | 0.65 |
| thời gian / lần gọi p95 (s) | 1.29 |
| thời gian / lần gọi max (s) | 1.29 |
| tổng thời gian / hội thoại p50 / p95 / max (s) | 1.13 / 1.29 / 1.29 |
| lần gọi trả danh sách rỗng | 2/8 (25%) |
| fact kỳ vọng tìm thấy | 5/6 |
| fact kỳ vọng bị bỏ sót | 1 |
| thao tác thừa (extra, người xét) | 2 |
| thao tác lặp lại fact kỳ vọng | 0 |
| thao tác ngoài kỳ vọng nhưng chấp nhận được | 0 |
| cụm CẤM xuất hiện (mỗi cái là một lỗi) | 0 |
| sai category so với kỳ vọng | 1 |
| JSON không parse được | 0 |
| output bị cắt (truncated) | 0 |
| thao tác bị code bỏ, theo lý do | 0 |
| token vào / ra trung bình (ra max) | 1742 / 36 (max 100) |
| hội thoại OK / REVIEW / FAIL | 6 / 0 / 1 |

Ghi chú: OK = tìm đủ, không thừa, không cụm cấm. REVIEW = tìm đủ, không cụm cấm, nhưng có thao tác thừa cần người xét (fact đúng vô hại hay bịa). FAIL = bỏ sót hoặc dính cụm cấm. Cụm cấm chỉ tính trên thao tác đã qua kiểm tra của code; nếu model viết số điện thoại và code bỏ nó thì ghi vào cột "bị code bỏ", không tính là dính cụm cấm (số lần như vậy nằm trong `forbidden_in_dropped` của file JSON).

## Từng hội thoại

### c27_vi_general_instruction (vi, 2026-10-14)

Tin nhắn người dùng: Từ giờ trả lời mình ngắn gọn thôi nhé.

Kỳ vọng: ADD [style] (ngắn gọn)

**turn** — OK — 1.29 s, 1 lần gọi

- lượt 1: 1.29 s, vào/ra 1707/42: ADD [instruction/style] "Trả lời ngắn gọn"

  đúng kỳ vọng 1, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

### c28_en_project_instruction (en, 2026-10-14)

Tin nhắn người dùng: For my marathon training, always suggest low-impact cross-training on rest days.

Kỳ vọng: ADD [project] (marathon) AND (low-impact | low impact)

**turn** — OK — 0.87 s, 1 lần gọi

- lượt 1: 0.87 s, vào/ra 1718/54: ADD [instruction/style] "Always suggest low-impact cross-training on rest days for marathon training"

  đúng kỳ vọng 1, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0
  - category lệch: kỳ vọng project, được style

### c29_vi_repeat_stored (vi, 2026-10-14)

Tin nhắn người dùng: Bác sĩ dặn mình không được ngồi xổm sâu.

Fact đã lưu: f1: Tránh ngồi xổm sâu

Kỳ vọng: REPEAT f1

**turn** — OK — 1.18 s, 1 lần gọi

- lượt 1: 1.18 s, vào/ra 1745/24: REPEAT f1

  đúng kỳ vọng 1, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

### c30_vi_rename (vi, 2026-10-14)

Tin nhắn người dùng: Thôi, từ giờ gọi tôi là Long nhé.

Fact đã lưu: f1: Gọi tôi là Du

Kỳ vọng: UPDATE f1 [style] (Long)
Cụm cấm: `Du`

**turn** — OK — 0.51 s, 1 lần gọi

- lượt 1: 0.51 s, vào/ra 1745/39: UPDATE f1 [style] "Gọi tôi là Long"

  đúng kỳ vọng 1, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

### c31_en_forget_all (en, 2026-10-14)

Tin nhắn người dùng: Please forget everything you noted about me.

Fact đã lưu: f1: Prefers morning sessions; f2: Allergic to latex

Kỳ vọng: FORGET_ALL

**turn** — OK — 1.13 s, 1 lần gọi

- lượt 1: 1.13 s, vào/ra 1778/13: FORGET_ALL

  đúng kỳ vọng 1, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

### c32_vi_accepted_plan (vi, 2026-10-14)

Tin nhắn người dùng: Mình quyết định tập chạy 5km vào mùa xuân, mỗi tuần 3 buổi sáng. Bạn thấy sao? / Ừ, chốt vậy nhé.

Kỳ vọng: ADD [project] (5km | 5 km) AND (mùa xuân)

**turn** — FAIL — 1.16 s, 2 lần gọi

- lượt 1: 0.65 s, vào/ra 1730/100: ADD [fact/goal] "Goal: run 5km in spring 2027"; ADD [fact/profile] "Plans to run 3 mornings per week"
- lượt 2: 0.51 s, vào/ra 1798/6: (rỗng)

  đúng kỳ vọng 0, bỏ sót 1, thừa 2, lặp 0, chấp nhận được 0, cụm cấm 0
  - BỎ SÓT: ADD [project] (5km | 5 km) AND (mùa xuân)
  - THỪA (người xét): ADD [fact/goal] "Goal: run 5km in spring 2027" (lượt 1)
  - THỪA (người xét): ADD [fact/profile] "Plans to run 3 mornings per week" (lượt 1)

### c33_vi_session_only_decision (vi, 2026-10-14)

Tin nhắn người dùng: Hôm nay mình tập bài 1 trước nhé, mai tính tiếp.

Kỳ vọng: không có fact nào

**turn** — OK — 0.42 s, 1 lần gọi

- lượt 1: 0.42 s, vào/ra 1711/6: (rỗng)

  đúng kỳ vọng 0, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

