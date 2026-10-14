# memory-probe-V2

Runner: `local_tests/run_memory_probe.py`, role `memory` (llm.py: khoá ECA, tắt reasoning, timeout 15 s, không retry, temperature 0, trần output 512 token). 26 hội thoại tổng hợp, không đụng DB, không dựng graph.

## Tóm tắt

- Tổng số lần gọi LLM: **136** session: 26, turn: 55, live: 55; lỗi gọi (timeout/4xx): session: 0, turn: 0, live: 0

| | session | turn | live |
|---|---|---|---|
| thời gian / lần gọi p50 (s) | 0.95 | 0.83 | 0.85 |
| thời gian / lần gọi p95 (s) | 1.37 | 1.18 | 1.13 |
| thời gian / lần gọi max (s) | 1.63 | 1.24 | 1.15 |
| tổng thời gian / hội thoại p50 / p95 / max (s) | 0.95 / 1.37 / 1.63 | 1.67 / 2.25 / 3.48 | 1.77 / 2.27 / 3.64 |
| lần gọi trả danh sách rỗng | 7/26 (27%) | 34/55 (62%) | 33/55 (60%) |
| fact kỳ vọng tìm thấy | 20/20 | 20/20 | 20/20 |
| fact kỳ vọng bị bỏ sót | 0 | 0 | 0 |
| thao tác thừa (extra, người xét) | 3 | 1 | 3 |
| thao tác lặp lại fact kỳ vọng | 0 | 0 | 0 |
| thao tác ngoài kỳ vọng nhưng chấp nhận được | 3 | 2 | 2 |
| cụm CẤM xuất hiện (mỗi cái là một lỗi) | 0 | 0 | 0 |
| sai category so với kỳ vọng | 0 | 1 | 1 |
| JSON không parse được | 0 | 0 | 0 |
| output bị cắt (truncated) | 0 | 0 | 0 |
| thao tác bị code bỏ, theo lý do | 0 | 0 | 0 |
| token vào / ra trung bình (ra max) | 1341 / 42 (max 118) | 1279 / 21 (max 96) | 1251 / 22 (max 96) |
| hội thoại OK / REVIEW / FAIL | 24 / 2 / 0 | 25 / 1 / 0 | 24 / 2 / 0 |

Ghi chú: OK = tìm đủ, không thừa, không cụm cấm. REVIEW = tìm đủ, không cụm cấm, nhưng có thao tác thừa cần người xét (fact đúng vô hại hay bịa). FAIL = bỏ sót hoặc dính cụm cấm. Cụm cấm chỉ tính trên thao tác đã qua kiểm tra của code; nếu model viết số điện thoại và code bỏ nó thì ghi vào cột "bị code bỏ", không tính là dính cụm cấm (số lần như vậy nằm trong `forbidden_in_dropped` của file JSON).

## Từng hội thoại

### c01_vi_plain_condition (vi, 2026-10-13)

Tin nhắn người dùng: Chào bạn. Mình bị đau lưng dưới khoảng 2 năm nay, bác sĩ bảo là thoát vị đĩa đệm L4-L5. / Vậy mình nên tập gì buổi sáng cho đỡ cứng lưng?

Kỳ vọng: ADD [condition] (thoát vị | đĩa đệm | L4)

**session** — OK — 1.09 s, 1 lần gọi

- 1.09 s, vào/ra 1421/62: ADD [condition] "Bị đau lưng dưới khoảng 2 năm nay, bác sĩ chẩn đoán thoát vị đĩa đệm L4-L5"

  đúng kỳ vọng 1, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

**turn** — OK — 1.75 s, 2 lần gọi

- lượt 1: 1.2 s, vào/ra 1311/62: ADD [condition] "Bị đau lưng dưới khoảng 2 năm nay, bác sĩ chẩn đoán thoát vị đĩa đệm L4-L5"
- lượt 2: 0.55 s, vào/ra 1353/5: (rỗng)

  đúng kỳ vọng 1, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

**live** — OK — 1.3 s, 2 lần gọi

- lượt 1: 0.72 s, vào/ra 1200/62: ADD [condition] "Bị đau lưng dưới khoảng 2 năm nay, bác sĩ chẩn đoán thoát vị đĩa đệm L4-L5"
- lượt 2: 0.58 s, vào/ra 1363/5: (rỗng)

  đúng kỳ vọng 1, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

### c02_en_plain_surgery (en, 2026-10-13)

Tin nhắn người dùng: I had ACL reconstruction on my right knee in March 2025. / Mostly fine. What can I do to strengthen it further?

Kỳ vọng: ADD [condition] (ACL) AND (2025 | March | right knee | right)

**session** — OK — 1.05 s, 1 lần gọi

- 1.05 s, vào/ra 1284/35: ADD [condition] "Had ACL reconstruction on the right knee in March 2025"

  đúng kỳ vọng 1, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

**turn** — OK — 1.7 s, 2 lần gọi

- lượt 1: 0.94 s, vào/ra 1219/35: ADD [condition] "Had ACL reconstruction on the right knee in March 2025"
- lượt 2: 0.76 s, vào/ra 1281/5: (rỗng)

  đúng kỳ vọng 1, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

**live** — OK — 1.62 s, 2 lần gọi

- lượt 1: 0.86 s, vào/ra 1169/35: ADD [condition] "Had ACL reconstruction on the right knee in March 2025"
- lượt 2: 0.76 s, vào/ra 1263/5: (rỗng)

  đúng kỳ vọng 1, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

### c03_vi_buried_small_talk (vi, 2026-10-13)

Tin nhắn người dùng: Hôm nay trời đẹp ghê, mình vừa đi chợ về xong. À mà mình bị tiểu đường type 2 nên phải kiêng đồ ngọt lắm. Bạn gợi ý giúp mình bài tập nhẹ buổi sáng được không? / Cảm ơn bạn, mình sẽ thử.

Kỳ vọng: ADD [condition] (tiểu đường | đái tháo đường | type 2 | diabetes)
Chấp nhận được: ADD (đồ ngọt | kiêng | ngọt)
Cụm cấm: `đi chợ`

**session** — OK — 1.01 s, 1 lần gọi

- 1.01 s, vào/ra 1403/41: ADD [condition] "Bị tiểu đường type 2, phải kiêng đồ ngọt"

  đúng kỳ vọng 1, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

**turn** — OK — 1.32 s, 2 lần gọi

- lượt 1: 0.73 s, vào/ra 1354/41: ADD [condition] "Bị tiểu đường type 2, phải kiêng đồ ngọt"
- lượt 2: 0.59 s, vào/ra 1271/5: (rỗng)

  đúng kỳ vọng 1, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

**live** — OK — 1.65 s, 2 lần gọi

- lượt 1: 0.9 s, vào/ra 1232/42: ADD [condition] "Bị tiểu đường type 2 nên phải kiêng đồ ngọt"
- lượt 2: 0.75 s, vào/ra 1343/5: (rỗng)

  đúng kỳ vọng 1, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

### c04_en_buried_small_talk (en, 2026-10-13)

Tin nhắn người dùng: Ha, my cat knocked my coffee over again this morning. Anyway, I've had a rotator cuff tear in my left shoulder for a while now so anything overhead is out. Can you suggest an upper body workout? / Nice, the band rows sound good.

Kỳ vọng: ADD [condition] (rotator cuff)
Chấp nhận được: ADD [restriction] (overhead)
Cụm cấm: `cat`, `coffee`

**session** — OK — 1.02 s, 1 lần gọi

- 1.02 s, vào/ra 1300/64: ADD [condition] "Has a rotator cuff tear in the left shoulder"; ADD [restriction] "Avoids overhead movements because of the left shoulder rotator cuff tear"

  đúng kỳ vọng 1, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 1, cụm cấm 0

**turn** — OK — 1.91 s, 2 lần gọi

- lượt 1: 1.07 s, vào/ra 1264/64: ADD [condition] "Has a rotator cuff tear in the left shoulder"; ADD [restriction] "Avoids overhead movements because of the left shoulder rotator cuff tear"
- lượt 2: 0.84 s, vào/ra 1293/5: (rỗng)

  đúng kỳ vọng 1, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 1, cụm cấm 0

**live** — OK — 1.61 s, 2 lần gọi

- lượt 1: 0.92 s, vào/ra 1199/55: ADD [condition] "Has a rotator cuff tear in the left shoulder"; ADD [restriction] "Avoids overhead movements"
- lượt 2: 0.69 s, vào/ra 1306/5: (rỗng)

  đúng kỳ vọng 1, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 1, cụm cấm 0

### c05_vi_assistant_assumes (vi, 2026-10-13)

Tin nhắn người dùng: Mình hay ngồi máy tính nhiều, cổ cứng lắm. Có bài nào giãn cổ không? / Ừ, cho mình thêm vài bài giãn cơ đơn giản nữa đi.

Kỳ vọng: không có fact nào
Chấp nhận được: ADD (cổ cứng | cứng cổ | ngồi máy tính | ngồi nhiều)
Cụm cấm: `thoái hóa`, `đốt sống cổ`, `MRI`

**session** — OK — 1.04 s, 1 lần gọi

- 1.04 s, vào/ra 1393/38: ADD [condition] "Hay ngồi máy tính nhiều, cổ cứng"

  đúng kỳ vọng 0, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 1, cụm cấm 0

**turn** — OK — 1.82 s, 2 lần gọi

- lượt 1: 0.94 s, vào/ra 1310/38: ADD [condition] "Hay ngồi máy tính nhiều, cổ cứng"
- lượt 2: 0.88 s, vào/ra 1302/5: (rỗng)

  đúng kỳ vọng 0, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 1, cụm cấm 0

**live** — OK — 1.97 s, 2 lần gọi

- lượt 1: 1.12 s, vào/ra 1187/40: ADD [condition] "Ngồi máy tính nhiều, hay bị cứng cổ"
- lượt 2: 0.85 s, vào/ra 1355/5: (rỗng)

  đúng kỳ vọng 0, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 1, cụm cấm 0

### c06_en_assistant_suggests (en, 2026-10-13)

Tin nhắn người dùng: Can you give me a 10 minute morning routine? / Sounds good, thanks.

Kỳ vọng: không có fact nào
Cụm cấm: `sciatica`, `tight hips`

**session** — OK — 0.99 s, 1 lần gọi

- 0.99 s, vào/ra 1247/5: (rỗng)

  đúng kỳ vọng 0, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

**turn** — OK — 1.4 s, 2 lần gọi

- lượt 1: 0.81 s, vào/ra 1229/5: (rỗng)
- lượt 2: 0.59 s, vào/ra 1194/5: (rỗng)

  đúng kỳ vọng 0, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

**live** — OK — 1.66 s, 2 lần gọi

- lượt 1: 0.97 s, vào/ra 1166/5: (rỗng)
- lượt 2: 0.69 s, vào/ra 1229/5: (rỗng)

  đúng kỳ vọng 0, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

### c07_vi_negate (vi, 2026-10-13)

Tin nhắn người dùng: Đầu gối mình khỏi hẳn rồi, không còn đau nữa. / Vậy mình tập squat lại được chưa?

Fact đã lưu: f1: Đau đầu gối phải khi chạy bộ; f2: Muốn giảm 5 kg trong năm nay

Kỳ vọng: DELETE f1

**session** — OK — 0.55 s, 1 lần gọi

- 0.55 s, vào/ra 1398/19: DELETE f1

  đúng kỳ vọng 1, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

**turn** — OK — 1.66 s, 2 lần gọi

- lượt 1: 0.9 s, vào/ra 1328/19: DELETE f1
- lượt 2: 0.76 s, vào/ra 1285/5: (rỗng)

  đúng kỳ vọng 1, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

**live** — OK — 1.81 s, 2 lần gọi

- lượt 1: 0.98 s, vào/ra 1261/19: DELETE f1
- lượt 2: 0.83 s, vào/ra 1279/5: (rỗng)

  đúng kỳ vọng 1, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

### c08_en_negate (en, 2026-10-13)

Tin nhắn người dùng: Good news, my doctor cleared me to run again. The shin splints are completely gone. / What would a first week look like?

Fact đã lưu: f1: Avoids running because of shin splints; f2: Works as a nurse on long shifts

Kỳ vọng: DELETE f1

**session** — OK — 0.95 s, 1 lần gọi

- 0.95 s, vào/ra 1350/19: DELETE f1

  đúng kỳ vọng 1, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

**turn** — OK — 1.02 s, 2 lần gọi

- lượt 1: 0.57 s, vào/ra 1300/19: DELETE f1
- lượt 2: 0.45 s, vào/ra 1261/5: (rỗng)

  đúng kỳ vọng 1, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

**live** — OK — 1.54 s, 2 lần gọi

- lượt 1: 0.84 s, vào/ra 1249/19: DELETE f1
- lượt 2: 0.7 s, vào/ra 1255/5: (rỗng)

  đúng kỳ vọng 1, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

### c09_vi_update (vi, 2026-10-13)

Tin nhắn người dùng: Giờ mình đi bộ cả tiếng cũng không đau gối nữa, nhưng leo cầu thang vẫn còn hơi nhói. / Mình nên tập gì để leo cầu thang đỡ nhói?

Fact đã lưu: f1: Đau gối trái khi đi bộ quá 10 phút (tháng 7/2026)

Kỳ vọng: UPDATE f1 [condition] (cầu thang)

**session** — OK — 1.16 s, 1 lần gọi

- 1.16 s, vào/ra 1429/96: UPDATE f1 [condition] "Đi bộ cả tiếng không còn đau gối, nhưng leo cầu thang vẫn hơi nhói (tháng 10/2026; trước đó tháng 7/2026 đau gối trái khi đi bộ quá 10 phút)"

  đúng kỳ vọng 1, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

**turn** — OK — 2.01 s, 2 lần gọi

- lượt 1: 1.18 s, vào/ra 1342/96: UPDATE f1 [condition] "Không còn đau gối trái khi đi bộ cả tiếng, nhưng vẫn hơi nhói khi leo cầu thang (tháng 10/2026; trước đó tháng 7/2026 đau khi đi bộ quá 10 phút)"
- lượt 2: 0.83 s, vào/ra 1357/5: (rỗng)

  đúng kỳ vọng 1, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

**live** — OK — 2.0 s, 2 lần gọi

- lượt 1: 1.15 s, vào/ra 1248/96: UPDATE f1 [condition] "Đi bộ cả tiếng không còn đau gối, nhưng leo cầu thang vẫn hơi nhói (tháng 10/2026; trước đó tháng 7/2026 đau gối trái khi đi bộ quá 10 phút)"
- lượt 2: 0.85 s, vào/ra 1369/5: (rỗng)

  đúng kỳ vọng 1, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

### c10_en_update (en, 2026-10-13)

Tin nhắn người dùng: Quick update: I started seeing a physio and now the pain only comes when I lift heavy things. Sitting is fine. / Any tips for lifting groceries without it flaring up?

Fact đã lưu: f1: Has lower back pain when sitting for more than an hour

Kỳ vọng: UPDATE f1 [condition] (lift | heavy)

**session** — OK — 1.08 s, 1 lần gọi

- 1.08 s, vào/ra 1319/54: UPDATE f1 [condition] "Lower back pain now only when lifting heavy things; sitting is fine (previously pain when sitting for more than an hour)"

  đúng kỳ vọng 1, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

**turn** — OK — 1.64 s, 2 lần gọi

- lượt 1: 0.94 s, vào/ra 1274/54: UPDATE f1 [condition] "Lower back pain now only when lifting heavy things; sitting is fine (previously pain when sitting for more than an hour)"
- lượt 2: 0.7 s, vào/ra 1273/5: (rỗng)

  đúng kỳ vọng 1, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

**live** — OK — 2.11 s, 2 lần gọi

- lượt 1: 1.13 s, vào/ra 1219/53: UPDATE f1 [condition] "Lower back pain now only when lifting heavy things; sitting is fine (previously pain when sitting more than an hour)"
- lượt 2: 0.98 s, vào/ra 1278/5: (rỗng)

  đúng kỳ vọng 1, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

### c11_vi_locked_contradict (vi, 2026-10-13)

Tin nhắn người dùng: Mình mới bị bong gân mắt cá chân trái tuần trước, giờ đi lại vẫn còn sưng. / Trong lúc này mình có thể tập phần thân trên không?

Fact đã lưu: f1 (khoá): Không có chấn thương hay bệnh lý nào

Kỳ vọng: ADD [condition] (bong gân | mắt cá)
Cụm cấm: `tuần trước`

**session** — OK — 1.03 s, 1 lần gọi

- 1.03 s, vào/ra 1401/58: ADD [condition] "Bị bong gân mắt cá chân trái vào tháng 10 năm 2026, hiện vẫn còn sưng khi đi lại"

  đúng kỳ vọng 1, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

**turn** — OK — 1.65 s, 2 lần gọi

- lượt 1: 0.82 s, vào/ra 1327/58: ADD [condition] "Bị bong gân mắt cá chân trái vào tháng 10 năm 2026, hiện vẫn còn sưng khi đi lại"
- lượt 2: 0.83 s, vào/ra 1357/5: (rỗng)

  đúng kỳ vọng 1, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

**live** — OK — 1.99 s, 2 lần gọi

- lượt 1: 1.07 s, vào/ra 1232/57: ADD [condition] "Bị bong gân mắt cá chân trái vào tháng 10 năm 2026, hiện đi lại vẫn còn sưng"
- lượt 2: 0.92 s, vào/ra 1378/5: (rỗng)

  đúng kỳ vọng 1, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

### c12_en_locked_contradict (en, 2026-10-13)

Tin nhắn người dùng: I actually do HIIT three times a week and I enjoy it, so please don't just give me low-impact stuff. / Great. Give me a 20 minute session for today.

Fact đã lưu: f1 (khoá): Prefers low-impact exercise only

Kỳ vọng: ADD [preference] (HIIT | interval | high-intensity | high intensity)

**session** — OK — 0.74 s, 1 lần gọi

- 0.74 s, vào/ra 1309/34: ADD [preference] "Does HIIT three times a week and enjoys it"

  đúng kỳ vọng 1, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

**turn** — OK — 1.76 s, 2 lần gọi

- lượt 1: 0.84 s, vào/ra 1257/33: ADD [profile] "Does HIIT three times a week and enjoys it"
- lượt 2: 0.92 s, vào/ra 1302/5: (rỗng)

  đúng kỳ vọng 1, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0
  - category lệch: kỳ vọng preference, được profile

**live** — OK — 1.83 s, 2 lần gọi

- lượt 1: 0.92 s, vào/ra 1215/33: ADD [profile] "Does HIIT three times a week and enjoys it"
- lượt 2: 0.91 s, vào/ra 1289/5: (rỗng)

  đúng kỳ vọng 1, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0
  - category lệch: kỳ vọng preference, được profile

### c13_vi_passing_state (vi, 2026-10-13)

Tin nhắn người dùng: Mệt quá hôm nay, tối qua ngủ không đủ giấc. / Ừ, hôm nay chắc mình chỉ tập nhẹ thôi.

Kỳ vọng: không có fact nào

**session** — OK — 0.53 s, 1 lần gọi

- 0.53 s, vào/ra 1326/5: (rỗng)

  đúng kỳ vọng 0, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

**turn** — OK — 2.14 s, 2 lần gọi

- lượt 1: 0.9 s, vào/ra 1262/5: (rỗng)
- lượt 2: 1.24 s, vào/ra 1240/5: (rỗng)

  đúng kỳ vọng 0, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

**live** — OK — 1.67 s, 2 lần gọi

- lượt 1: 0.85 s, vào/ra 1175/5: (rỗng)
- lượt 2: 0.82 s, vào/ra 1267/5: (rỗng)

  đúng kỳ vọng 0, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

### c14_en_small_talk (en, 2026-10-13)

Tin nhắn người dùng: Hi! How's your day going? / Pretty good. It's been raining all week where I am, so I've mostly been watching movies. / Ha, maybe later. Thanks for the chat.

Kỳ vọng: không có fact nào

**session** — OK — 0.58 s, 1 lần gọi

- 0.58 s, vào/ra 1274/5: (rỗng)

  đúng kỳ vọng 0, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

**turn** — OK — 2.25 s, 3 lần gọi

- lượt 1: 0.59 s, vào/ra 1197/5: (rỗng)
- lượt 2: 0.9 s, vào/ra 1223/5: (rỗng)
- lượt 3: 0.76 s, vào/ra 1194/5: (rỗng)

  đúng kỳ vọng 0, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

**live** — OK — 2.15 s, 3 lần gọi

- lượt 1: 0.73 s, vào/ra 1163/5: (rỗng)
- lượt 2: 0.62 s, vào/ra 1216/5: (rỗng)
- lượt 3: 0.8 s, vào/ra 1218/5: (rỗng)

  đúng kỳ vọng 0, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

### c15_vi_goal (vi, 2026-10-13)

Tin nhắn người dùng: Mục tiêu của mình là chạy được 10 km vào tháng 4 năm sau. / Khoảng 3 km thôi, chạy xong hơi hụt hơi.

Kỳ vọng: ADD [goal] (10 km | 10km)
Chấp nhận được: ADD (3 km | 3km)

**session** — OK — 0.97 s, 1 lần gọi

- 0.97 s, vào/ra 1334/77: ADD [goal] "Mục tiêu là chạy được 10 km vào tháng 4 năm 2027"; ADD [profile] "Hiện chạy được khoảng 3 km mỗi lần"

  đúng kỳ vọng 1, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 1, cụm cấm 0

**turn** — OK — 1.9 s, 2 lần gọi

- lượt 1: 1.07 s, vào/ra 1235/44: ADD [goal] "Mục tiêu là chạy được 10 km vào tháng 4 năm 2027"
- lượt 2: 0.83 s, vào/ra 1324/5: (rỗng)

  đúng kỳ vọng 1, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

**live** — OK — 1.47 s, 2 lần gọi

- lượt 1: 0.74 s, vào/ra 1178/44: ADD [goal] "Mục tiêu là chạy được 10 km vào tháng 4 năm 2027"
- lượt 2: 0.73 s, vào/ra 1291/5: (rỗng)

  đúng kỳ vọng 1, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

### c16_en_preference_style (en, 2026-10-13)

Tin nhắn người dùng: Before we start: please keep your answers short and use bullet points. I really don't read long paragraphs. / A quick desk stretch for my wrists.

Kỳ vọng: ADD [preference] (short | brief | concise) AND (bullet)

**session** — OK — 0.68 s, 1 lần gọi

- 0.68 s, vào/ra 1264/35: ADD [preference] "Prefers short answers in bullet points, not long paragraphs"

  đúng kỳ vọng 1, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

**turn** — OK — 1.53 s, 2 lần gọi

- lượt 1: 0.89 s, vào/ra 1213/35: ADD [preference] "Prefers short answers in bullet points, not long paragraphs"
- lượt 2: 0.64 s, vào/ra 1267/5: (rỗng)

  đúng kỳ vọng 1, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

**live** — OK — 1.61 s, 2 lần gọi

- lượt 1: 0.69 s, vào/ra 1177/35: ADD [preference] "Prefers short answers in bullet points, not long paragraphs"
- lượt 2: 0.92 s, vào/ra 1245/5: (rỗng)

  đúng kỳ vọng 1, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

### c17_vi_phone_email (vi, 2026-10-13)

Tin nhắn người dùng: Mình bị viêm khớp gối. Bạn nhắn lịch tập qua số 0912 345 678 hoặc email tran.van.a@gmail.com nhé. / Ok, vậy cho mình lịch tập 3 buổi một tuần ở đây luôn.

Kỳ vọng: ADD [condition] (viêm khớp)
Cụm cấm: `0912`, `345 678`, `345678`, `tran.van.a`, `gmail`

**session** — OK — 0.88 s, 1 lần gọi

- 0.88 s, vào/ra 1421/32: ADD [condition] "Bị viêm khớp gối"

  đúng kỳ vọng 1, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

**turn** — OK — 1.4 s, 2 lần gọi

- lượt 1: 0.68 s, vào/ra 1312/32: ADD [condition] "Bị viêm khớp gối"
- lượt 2: 0.72 s, vào/ra 1322/5: (rỗng)

  đúng kỳ vọng 1, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

**live** — OK — 1.77 s, 2 lần gọi

- lượt 1: 0.96 s, vào/ra 1197/32: ADD [condition] "Bị viêm khớp gối"
- lượt 2: 0.81 s, vào/ra 1337/5: (rỗng)

  đúng kỳ vọng 1, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

### c18_en_relative_condition (en, 2026-10-13)

Tin nhắn người dùng: My mom has osteoporosis and I'm worried about her falling. Can you suggest some balance exercises for her? / Thanks, I'll show her those tomorrow.

Kỳ vọng: không có fact nào
Cụm cấm: `osteoporosis`, `mom`, `mother`

**session** — OK — 0.92 s, 1 lần gọi

- 0.92 s, vào/ra 1274/5: (rỗng)

  đúng kỳ vọng 0, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

**turn** — OK — 1.74 s, 2 lần gọi

- lượt 1: 0.82 s, vào/ra 1247/5: (rỗng)
- lượt 2: 0.92 s, vào/ra 1203/5: (rỗng)

  đúng kỳ vọng 0, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

**live** — OK — 1.69 s, 2 lần gọi

- lượt 1: 0.86 s, vào/ra 1176/5: (rỗng)
- lượt 2: 0.83 s, vào/ra 1241/5: (rỗng)

  đúng kỳ vọng 0, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

### c19_vi_relative_time (vi, 2026-10-13)

Tin nhắn người dùng: Mình mổ khớp vai phải cách đây 3 tháng rồi, giờ vẫn chưa nhấc tay lên quá đầu được. / Tự tập ở nhà thôi, bạn chỉ mình vài bài an toàn.

Kỳ vọng: ADD [condition] (vai) AND (tháng 7 | 7/2026 | 07/2026 | 2026-07 | tháng 07)
Cụm cấm: `3 tháng`, `ba tháng`, `cách đây`

**session** — REVIEW — 1.37 s, 1 lần gọi

- 1.37 s, vào/ra 1383/118: ADD [condition] "Đã mổ khớp vai phải vào tháng 7 năm 2026"; ADD [condition] "Hiện chưa nhấc được tay lên quá đầu"; ADD [profile] "Tự tập phục hồi chức năng tại nhà, không tập với kỹ thuật viên"

  đúng kỳ vọng 1, bỏ sót 0, thừa 2, lặp 0, chấp nhận được 0, cụm cấm 0
  - THỪA (người xét): ADD [condition] "Hiện chưa nhấc được tay lên quá đầu"
  - THỪA (người xét): ADD [profile] "Tự tập phục hồi chức năng tại nhà, không tập với kỹ thuật viên"

**turn** — REVIEW — 2.07 s, 2 lần gọi

- lượt 1: 1.09 s, vào/ra 1283/83: ADD [condition] "Đã mổ khớp vai phải vào tháng 7 năm 2026"; ADD [condition] "Hiện vẫn chưa nhấc được tay lên quá đầu sau mổ vai phải"
- lượt 2: 0.98 s, vào/ra 1376/5: (rỗng)

  đúng kỳ vọng 1, bỏ sót 0, thừa 1, lặp 0, chấp nhận được 0, cụm cấm 0
  - THỪA (người xét): ADD [condition] "Hiện vẫn chưa nhấc được tay lên quá đầu sau mổ vai phải" (lượt 1)

**live** — REVIEW — 2.27 s, 2 lần gọi

- lượt 1: 1.15 s, vào/ra 1195/77: ADD [condition] "Đã mổ khớp vai phải vào tháng 7 năm 2026"; ADD [condition] "Hiện vẫn chưa nhấc tay lên quá đầu được"
- lượt 2: 1.12 s, vào/ra 1363/49: ADD [profile] "Tự tập phục hồi chức năng tại nhà, không tập với kỹ thuật viên"

  đúng kỳ vọng 1, bỏ sót 0, thừa 2, lặp 0, chấp nhận được 0, cụm cấm 0
  - THỪA (người xét): ADD [condition] "Hiện vẫn chưa nhấc tay lên quá đầu được" (lượt 1)
  - THỪA (người xét): ADD [profile] "Tự tập phục hồi chức năng tại nhà, không tập với kỹ thuật viên" (lượt 2)

### c20_en_three_facts (en, 2026-10-13)

Tin nhắn người dùng: Hey, I'm back. I broke my wrist skiing last month and the cast just came off. / Yes, my physio told me to avoid push-ups and any weight-bearing on that hand for another six weeks. / Cool. My goal is to be back on skis by February, so I want to be strong by then. / Sounds like a plan. Show me today's mobility routine.

Fact đã lưu: f1: Works as a software developer

Kỳ vọng: ADD [condition] (wrist) AND (September | 2026-09 | 09/2026 | 9/2026) ; ADD [restriction] (push-up | weight-bearing | weight bearing) ; ADD [goal] (ski) AND (February)
Cụm cấm: `last month`

**session** — OK — 1.24 s, 1 lần gọi

- 1.24 s, vào/ra 1432/109: ADD [condition] "Broke wrist skiing in September 2026; cast removed in October 2026"; ADD [restriction] "Physio advised avoiding push-ups and weight-bearing on the injured hand for six weeks from October 2026"; ADD [goal] "Goal is to be back on skis by February 2027"

  đúng kỳ vọng 3, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

**turn** — OK — 3.48 s, 4 lần gọi

- lượt 1: 1.1 s, vào/ra 1259/43: ADD [condition] "Broke their wrist skiing in September 2026; the cast was removed in October 2026"
- lượt 2: 0.96 s, vào/ra 1309/44: ADD [restriction] "Must avoid push-ups and any weight-bearing on the injured hand for six weeks from October 2026"
- lượt 3: 0.97 s, vào/ra 1364/40: ADD [goal] "Wants to be back on skis by February 2027 and strong by then"
- lượt 4: 0.45 s, vào/ra 1396/5: (rỗng)

  đúng kỳ vọng 3, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

**live** — OK — 3.64 s, 4 lần gọi

- lượt 1: 0.98 s, vào/ra 1208/40: ADD [condition] "Broke their wrist skiing in September 2026; the cast has since been removed"
- lượt 2: 1.11 s, vào/ra 1314/45: ADD [restriction] "Physio advised avoiding push-ups and any weight-bearing on the hand for six weeks from October 2026"
- lượt 3: 0.98 s, vào/ra 1363/40: ADD [goal] "Wants to be back on skis by February 2027 and strong by then"
- lượt 4: 0.57 s, vào/ra 1403/5: (rỗng)

  đúng kỳ vọng 3, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

### c21_en_favourite_food_in_passing (en, 2026-10-14)

Tin nhắn người dùng: hey, nothing to train today, just want to chat for a bit / then lets talk about food, what is your favorite food or drinks, mine is noodles, but the nutrients are kinds ass so I try my best not to eat too much of it

Fact đã lưu: f1: Prefers to be called Mon; f2: Has back pain; f3: Does not want exercises involving the back recommended, for fear of harming himself

Kỳ vọng: ADD [preference] (noodle)
Cụm cấm: `back`

**session** — OK — 0.94 s, 1 lần gọi

- 0.94 s, vào/ra 1409/45: ADD [preference] "Favourite food is noodles, but tries not to eat too much of them because of their poor nutritional value"

  đúng kỳ vọng 1, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

**turn** — OK — 1.76 s, 2 lần gọi

- lượt 1: 0.72 s, vào/ra 1331/5: (rỗng)
- lượt 2: 1.04 s, vào/ra 1365/43: ADD [preference] "Likes noodles, but tries not to eat too much of them because of their low nutritional value"

  đúng kỳ vọng 1, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

**live** — OK — 1.84 s, 2 lần gọi

- lượt 1: 0.74 s, vào/ra 1281/5: (rỗng)
- lượt 2: 1.1 s, vào/ra 1359/43: ADD [preference] "Likes noodles, but tries not to eat too much of them because of their poor nutritional value"

  đúng kỳ vọng 1, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

### c22_vi_daily_coffee_in_passing (vi, 2026-10-14)

Tin nhắn người dùng: chào bạn, hôm nay mình rảnh nên ghé nói chuyện chơi / cũng ổn, sáng nào mình cũng phải uống một ly cà phê sữa đá mới tỉnh được, thiếu là cả ngày uể oải

Kỳ vọng: ADD [preference] (cà phê | coffee)

**session** — OK — 1.63 s, 1 lần gọi

- 1.63 s, vào/ra 1333/42: ADD [preference] "Uống một ly cà phê sữa đá mỗi sáng"

  đúng kỳ vọng 1, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

**turn** — OK — 1.22 s, 2 lần gọi

- lượt 1: 0.4 s, vào/ra 1225/5: (rỗng)
- lượt 2: 0.82 s, vào/ra 1284/55: ADD [preference] "Mỗi sáng đều uống một ly cà phê sữa đá, thiếu là cả ngày uể oải"

  đúng kỳ vọng 1, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

**live** — OK — 1.95 s, 2 lần gọi

- lượt 1: 0.85 s, vào/ra 1180/5: (rỗng)
- lượt 2: 1.1 s, vào/ra 1254/53: ADD [preference] "Uống một ly cà phê sữa đá mỗi sáng, thiếu là cả ngày uể oải"

  đúng kỳ vọng 1, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

### c23_vi_music_taste_in_passing (vi, 2026-10-14)

Tin nhắn người dùng: tối nay mình định tập nhẹ ở nhà, bạn có gợi ý gì cho vui không / mình mê nhạc trữ tình, nghe lúc tập là phê nhất, còn nhạc điện tử thì mình không chịu nổi

Kỳ vọng: ADD [preference] (trữ tình)

**session** — REVIEW — 0.93 s, 1 lần gọi

- 0.93 s, vào/ra 1342/67: ADD [preference] "Thích nhạc trữ tình, thích nghe khi tập"; ADD [preference] "Không thích nhạc điện tử"

  đúng kỳ vọng 1, bỏ sót 0, thừa 1, lặp 0, chấp nhận được 0, cụm cấm 0
  - THỪA (người xét): ADD [preference] "Không thích nhạc điện tử"

**turn** — OK — 1.43 s, 2 lần gọi

- lượt 1: 0.62 s, vào/ra 1254/5: (rỗng)
- lượt 2: 0.81 s, vào/ra 1264/51: ADD [preference] "Thích nhạc trữ tình, thích nghe khi tập; không chịu được nhạc điện tử"

  đúng kỳ vọng 1, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

**live** — REVIEW — 1.85 s, 2 lần gọi

- lượt 1: 0.76 s, vào/ra 1184/5: (rỗng)
- lượt 2: 1.09 s, vào/ra 1270/67: ADD [preference] "Thích nghe nhạc trữ tình khi tập luyện"; ADD [preference] "Không thích nhạc điện tử"

  đúng kỳ vọng 1, bỏ sót 0, thừa 1, lặp 0, chấp nhận được 0, cụm cấm 0
  - THỪA (người xét): ADD [preference] "Không thích nhạc điện tử" (lượt 2)

### c24_en_ate_and_did_today (en, 2026-10-14)

Tin nhắn người dùng: ugh, today was a mess. I had pizza for lunch and then went for a long walk by the river / fine, a bit tired. I also watched two movies in the evening, lazy day

Kỳ vọng: không có fact nào
Cụm cấm: `pizza`, `walk`, `movie`

**session** — OK — 0.59 s, 1 lần gọi

- 0.59 s, vào/ra 1253/5: (rỗng)

  đúng kỳ vọng 0, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

**turn** — OK — 1.67 s, 2 lần gọi

- lượt 1: 0.92 s, vào/ra 1219/5: (rỗng)
- lượt 2: 0.75 s, vào/ra 1210/5: (rỗng)

  đúng kỳ vọng 0, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

**live** — OK — 1.8 s, 2 lần gọi

- lượt 1: 0.92 s, vào/ra 1179/5: (rỗng)
- lượt 2: 0.88 s, vào/ra 1219/5: (rỗng)

  đúng kỳ vọng 0, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

### c25_vi_someone_elses_like (vi, 2026-10-14)

Tin nhắn người dùng: chị gái mình mê chạy bộ lắm, tuần nào cũng chạy ba buổi, còn nhà mình thì mẹ thích nấu ăn / mình chưa nghĩ ra, chắc hỏi bạn về giấc ngủ

Kỳ vọng: không có fact nào
Cụm cấm: `chạy bộ`, `nấu ăn`, `chị`

**session** — OK — 0.75 s, 1 lần gọi

- 0.75 s, vào/ra 1319/5: (rỗng)

  đúng kỳ vọng 0, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

**turn** — OK — 1.47 s, 2 lần gọi

- lượt 1: 0.82 s, vào/ra 1273/5: (rỗng)
- lượt 2: 0.65 s, vào/ra 1222/5: (rỗng)

  đúng kỳ vọng 0, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

**live** — OK — 1.13 s, 2 lần gọi

- lượt 1: 0.46 s, vào/ra 1204/5: (rỗng)
- lượt 2: 0.67 s, vào/ra 1252/5: (rỗng)

  đúng kỳ vọng 0, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

### c26_en_assistant_guess_unconfirmed (en, 2026-10-14)

Tin nhắn người dùng: what should I do this weekend to stay active / haha who knows, I'll see how the weather is

Kỳ vọng: không có fact nào
Cụm cấm: `hiking`, `hike`, `trail`

**session** — OK — 0.69 s, 1 lần gọi

- 0.69 s, vào/ra 1240/5: (rỗng)

  đúng kỳ vọng 0, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

**turn** — OK — 1.57 s, 2 lần gọi

- lượt 1: 0.71 s, vào/ra 1216/5: (rỗng)
- lượt 2: 0.86 s, vào/ra 1200/5: (rỗng)

  đúng kỳ vọng 0, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

**live** — OK — 1.62 s, 2 lần gọi

- lượt 1: 0.89 s, vào/ra 1165/5: (rỗng)
- lượt 2: 0.73 s, vào/ra 1225/5: (rỗng)

  đúng kỳ vọng 0, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

