# memory-probe-V0

Runner: `local_tests/run_memory_probe.py`, role `memory` (llm.py: khoá ECA, tắt reasoning, timeout 15 s, không retry, temperature 0, trần output 512 token). 20 hội thoại tổng hợp, không đụng DB, không dựng graph.

## Tóm tắt

- Tổng số lần gọi LLM: **63** session: 20, turn: 43; lỗi gọi (timeout/4xx): session: 0, turn: 0

| | session | turn |
|---|---|---|
| thời gian / lần gọi p50 (s) | 0.75 | 0.70 |
| thời gian / lần gọi p95 (s) | 1.21 | 0.98 |
| thời gian / lần gọi max (s) | 1.62 | 0.99 |
| tổng thời gian / hội thoại p50 / p95 / max (s) | 0.75 / 1.21 / 1.62 | 1.54 / 1.77 / 3.05 |
| lần gọi trả danh sách rỗng | 4/20 (20%) | 25/43 (58%) |
| fact kỳ vọng tìm thấy | 17/17 | 17/17 |
| fact kỳ vọng bị bỏ sót | 0 | 0 |
| thao tác thừa (extra, người xét) | 2 | 1 |
| thao tác lặp lại fact kỳ vọng | 0 | 0 |
| thao tác ngoài kỳ vọng nhưng chấp nhận được | 3 | 2 |
| cụm CẤM xuất hiện (mỗi cái là một lỗi) | 0 | 0 |
| sai category so với kỳ vọng | 0 | 1 |
| JSON không parse được | 0 | 0 |
| output bị cắt (truncated) | 0 | 0 |
| thao tác bị code bỏ, theo lý do | 0 | 0 |
| token vào / ra trung bình (ra max) | 1307 / 47 (max 120) | 1244 / 22 (max 86) |
| hội thoại OK / REVIEW / FAIL | 19 / 1 / 0 | 19 / 1 / 0 |

Ghi chú: OK = tìm đủ, không thừa, không cụm cấm. REVIEW = tìm đủ, không cụm cấm, nhưng có thao tác thừa cần người xét (fact đúng vô hại hay bịa). FAIL = bỏ sót hoặc dính cụm cấm. Cụm cấm chỉ tính trên thao tác đã qua kiểm tra của code; nếu model viết số điện thoại và code bỏ nó thì ghi vào cột "bị code bỏ", không tính là dính cụm cấm (số lần như vậy nằm trong `forbidden_in_dropped` của file JSON).

## Từng hội thoại

### c01_vi_plain_condition (vi, 2026-10-13)

Tin nhắn người dùng: Chào bạn. Mình bị đau lưng dưới khoảng 2 năm nay, bác sĩ bảo là thoát vị đĩa đệm L4-L5. / Vậy mình nên tập gì buổi sáng cho đỡ cứng lưng?

Kỳ vọng: ADD [condition] (thoát vị | đĩa đệm | L4)

**session** — OK — 1.06 s, 1 lần gọi

- 1.06 s, vào/ra 1380/62: ADD [condition] "Bị đau lưng dưới khoảng 2 năm nay, bác sĩ chẩn đoán thoát vị đĩa đệm L4-L5"

  đúng kỳ vọng 1, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

**turn** — OK — 1.62 s, 2 lần gọi

- lượt 1: 0.96 s, vào/ra 1270/62: ADD [condition] "Bị đau lưng dưới khoảng 2 năm nay, bác sĩ chẩn đoán thoát vị đĩa đệm L4-L5"
- lượt 2: 0.66 s, vào/ra 1312/5: (rỗng)

  đúng kỳ vọng 1, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

### c02_en_plain_surgery (en, 2026-10-13)

Tin nhắn người dùng: I had ACL reconstruction on my right knee in March 2025. / Mostly fine. What can I do to strengthen it further?

Kỳ vọng: ADD [condition] (ACL) AND (2025 | March | right knee | right)

**session** — OK — 0.79 s, 1 lần gọi

- 0.79 s, vào/ra 1243/35: ADD [condition] "Had ACL reconstruction on the right knee in March 2025"

  đúng kỳ vọng 1, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

**turn** — OK — 1.27 s, 2 lần gọi

- lượt 1: 0.66 s, vào/ra 1178/35: ADD [condition] "Had ACL reconstruction on the right knee in March 2025"
- lượt 2: 0.61 s, vào/ra 1240/5: (rỗng)

  đúng kỳ vọng 1, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

### c03_vi_buried_small_talk (vi, 2026-10-13)

Tin nhắn người dùng: Hôm nay trời đẹp ghê, mình vừa đi chợ về xong. À mà mình bị tiểu đường type 2 nên phải kiêng đồ ngọt lắm. Bạn gợi ý giúp mình bài tập nhẹ buổi sáng được không? / Cảm ơn bạn, mình sẽ thử.

Kỳ vọng: ADD [condition] (tiểu đường | đái tháo đường | type 2 | diabetes)
Chấp nhận được: ADD (đồ ngọt | kiêng | ngọt)
Cụm cấm: `đi chợ`

**session** — OK — 0.96 s, 1 lần gọi

- 0.96 s, vào/ra 1362/41: ADD [condition] "Bị tiểu đường type 2, phải kiêng đồ ngọt"

  đúng kỳ vọng 1, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

**turn** — OK — 1.77 s, 2 lần gọi

- lượt 1: 0.99 s, vào/ra 1313/41: ADD [condition] "Bị tiểu đường type 2, phải kiêng đồ ngọt"
- lượt 2: 0.78 s, vào/ra 1230/5: (rỗng)

  đúng kỳ vọng 1, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

### c04_en_buried_small_talk (en, 2026-10-13)

Tin nhắn người dùng: Ha, my cat knocked my coffee over again this morning. Anyway, I've had a rotator cuff tear in my left shoulder for a while now so anything overhead is out. Can you suggest an upper body workout? / Nice, the band rows sound good.

Kỳ vọng: ADD [condition] (rotator cuff)
Chấp nhận được: ADD [restriction] (overhead)
Cụm cấm: `cat`, `coffee`

**session** — OK — 0.66 s, 1 lần gọi

- 0.66 s, vào/ra 1259/64: ADD [condition] "Has a rotator cuff tear in the left shoulder"; ADD [restriction] "Avoids overhead movements because of the left shoulder rotator cuff tear"

  đúng kỳ vọng 1, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 1, cụm cấm 0

**turn** — OK — 1.47 s, 2 lần gọi

- lượt 1: 0.93 s, vào/ra 1223/64: ADD [condition] "Has a rotator cuff tear in the left shoulder"; ADD [restriction] "Avoids overhead movements because of the left shoulder rotator cuff tear"
- lượt 2: 0.54 s, vào/ra 1252/5: (rỗng)

  đúng kỳ vọng 1, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 1, cụm cấm 0

### c05_vi_assistant_assumes (vi, 2026-10-13)

Tin nhắn người dùng: Mình hay ngồi máy tính nhiều, cổ cứng lắm. Có bài nào giãn cổ không? / Ừ, cho mình thêm vài bài giãn cơ đơn giản nữa đi.

Kỳ vọng: không có fact nào
Chấp nhận được: ADD (cổ cứng | cứng cổ | ngồi máy tính | ngồi nhiều)
Cụm cấm: `thoái hóa`, `đốt sống cổ`, `MRI`

**session** — OK — 0.65 s, 1 lần gọi

- 0.65 s, vào/ra 1352/40: ADD [condition] "Ngồi máy tính nhiều, hay bị cứng cổ"

  đúng kỳ vọng 0, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 1, cụm cấm 0

**turn** — OK — 1.62 s, 2 lần gọi

- lượt 1: 0.99 s, vào/ra 1269/40: ADD [condition] "Ngồi máy tính nhiều, hay bị cứng cổ"
- lượt 2: 0.63 s, vào/ra 1263/5: (rỗng)

  đúng kỳ vọng 0, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 1, cụm cấm 0

### c06_en_assistant_suggests (en, 2026-10-13)

Tin nhắn người dùng: Can you give me a 10 minute morning routine? / Sounds good, thanks.

Kỳ vọng: không có fact nào
Cụm cấm: `sciatica`, `tight hips`

**session** — OK — 0.52 s, 1 lần gọi

- 0.52 s, vào/ra 1206/5: (rỗng)

  đúng kỳ vọng 0, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

**turn** — OK — 1.36 s, 2 lần gọi

- lượt 1: 0.68 s, vào/ra 1188/5: (rỗng)
- lượt 2: 0.68 s, vào/ra 1153/5: (rỗng)

  đúng kỳ vọng 0, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

### c07_vi_negate (vi, 2026-10-13)

Tin nhắn người dùng: Đầu gối mình khỏi hẳn rồi, không còn đau nữa. / Vậy mình tập squat lại được chưa?

Fact đã lưu: f1: Đau đầu gối phải khi chạy bộ; f2: Muốn giảm 5 kg trong năm nay

Kỳ vọng: DELETE f1

**session** — OK — 0.72 s, 1 lần gọi

- 0.72 s, vào/ra 1357/19: DELETE f1

  đúng kỳ vọng 1, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

**turn** — OK — 1.61 s, 2 lần gọi

- lượt 1: 0.86 s, vào/ra 1287/19: DELETE f1
- lượt 2: 0.75 s, vào/ra 1244/5: (rỗng)

  đúng kỳ vọng 1, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

### c08_en_negate (en, 2026-10-13)

Tin nhắn người dùng: Good news, my doctor cleared me to run again. The shin splints are completely gone. / What would a first week look like?

Fact đã lưu: f1: Avoids running because of shin splints; f2: Works as a nurse on long shifts

Kỳ vọng: DELETE f1

**session** — OK — 0.58 s, 1 lần gọi

- 0.58 s, vào/ra 1309/19: DELETE f1

  đúng kỳ vọng 1, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

**turn** — OK — 1.5 s, 2 lần gọi

- lượt 1: 0.55 s, vào/ra 1259/19: DELETE f1
- lượt 2: 0.95 s, vào/ra 1220/5: (rỗng)

  đúng kỳ vọng 1, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

### c09_vi_update (vi, 2026-10-13)

Tin nhắn người dùng: Giờ mình đi bộ cả tiếng cũng không đau gối nữa, nhưng leo cầu thang vẫn còn hơi nhói. / Mình nên tập gì để leo cầu thang đỡ nhói?

Fact đã lưu: f1: Đau gối trái khi đi bộ quá 10 phút (tháng 7/2026)

Kỳ vọng: UPDATE f1 [condition] (cầu thang)

**session** — OK — 0.93 s, 1 lần gọi

- 0.93 s, vào/ra 1388/96: UPDATE f1 [condition] "Đi bộ cả tiếng không còn đau gối, nhưng leo cầu thang vẫn hơi nhói (tháng 10/2026; trước đó tháng 7/2026 đau gối trái khi đi bộ quá 10 phút)"

  đúng kỳ vọng 1, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

**turn** — OK — 1.58 s, 2 lần gọi

- lượt 1: 0.98 s, vào/ra 1301/86: UPDATE f1 [condition] "Đi bộ cả tiếng không còn đau gối, nhưng leo cầu thang vẫn hơi nhói (tháng 7/2026: đau gối trái khi đi bộ quá 10 phút)"
- lượt 2: 0.6 s, vào/ra 1306/5: (rỗng)

  đúng kỳ vọng 1, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

### c10_en_update (en, 2026-10-13)

Tin nhắn người dùng: Quick update: I started seeing a physio and now the pain only comes when I lift heavy things. Sitting is fine. / Any tips for lifting groceries without it flaring up?

Fact đã lưu: f1: Has lower back pain when sitting for more than an hour

Kỳ vọng: UPDATE f1 [condition] (lift | heavy)

**session** — OK — 0.93 s, 1 lần gọi

- 0.93 s, vào/ra 1278/58: UPDATE f1 [condition] "Lower back pain now only when lifting heavy things; sitting is fine (in October 2026: pain when sitting for more than an hour)"

  đúng kỳ vọng 1, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

**turn** — OK — 1.09 s, 2 lần gọi

- lượt 1: 0.57 s, vào/ra 1233/54: UPDATE f1 [condition] "Lower back pain now only when lifting heavy things; sitting is fine (previously pain when sitting for more than an hour)"
- lượt 2: 0.52 s, vào/ra 1232/5: (rỗng)

  đúng kỳ vọng 1, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

### c11_vi_locked_contradict (vi, 2026-10-13)

Tin nhắn người dùng: Mình mới bị bong gân mắt cá chân trái tuần trước, giờ đi lại vẫn còn sưng. / Trong lúc này mình có thể tập phần thân trên không?

Fact đã lưu: f1 (khoá): Không có chấn thương hay bệnh lý nào

Kỳ vọng: ADD [condition] (bong gân | mắt cá)
Cụm cấm: `tuần trước`

**session** — OK — 0.73 s, 1 lần gọi

- 0.73 s, vào/ra 1360/58: ADD [condition] "Bị bong gân mắt cá chân trái vào tháng 10 năm 2026, hiện vẫn còn sưng khi đi lại"

  đúng kỳ vọng 1, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

**turn** — OK — 1.62 s, 2 lần gọi

- lượt 1: 0.83 s, vào/ra 1286/58: ADD [condition] "Bị bong gân mắt cá chân trái vào tháng 10 năm 2026, hiện vẫn còn sưng khi đi lại"
- lượt 2: 0.79 s, vào/ra 1316/5: (rỗng)

  đúng kỳ vọng 1, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

### c12_en_locked_contradict (en, 2026-10-13)

Tin nhắn người dùng: I actually do HIIT three times a week and I enjoy it, so please don't just give me low-impact stuff. / Great. Give me a 20 minute session for today.

Fact đã lưu: f1 (khoá): Prefers low-impact exercise only

Kỳ vọng: ADD [preference] (HIIT | interval | high-intensity | high intensity)

**session** — OK — 0.68 s, 1 lần gọi

- 0.68 s, vào/ra 1268/42: ADD [preference] "Does HIIT three times a week and enjoys it; does not want only low-impact exercise"

  đúng kỳ vọng 1, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

**turn** — OK — 1.61 s, 2 lần gọi

- lượt 1: 0.85 s, vào/ra 1216/33: ADD [profile] "Does HIIT three times a week and enjoys it"
- lượt 2: 0.76 s, vào/ra 1261/5: (rỗng)

  đúng kỳ vọng 1, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0
  - category lệch: kỳ vọng preference, được profile

### c13_vi_passing_state (vi, 2026-10-13)

Tin nhắn người dùng: Mệt quá hôm nay, tối qua ngủ không đủ giấc. / Ừ, hôm nay chắc mình chỉ tập nhẹ thôi.

Kỳ vọng: không có fact nào

**session** — OK — 0.75 s, 1 lần gọi

- 0.75 s, vào/ra 1285/5: (rỗng)

  đúng kỳ vọng 0, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

**turn** — OK — 1.53 s, 2 lần gọi

- lượt 1: 0.64 s, vào/ra 1221/5: (rỗng)
- lượt 2: 0.89 s, vào/ra 1199/5: (rỗng)

  đúng kỳ vọng 0, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

### c14_en_small_talk (en, 2026-10-13)

Tin nhắn người dùng: Hi! How's your day going? / Pretty good. It's been raining all week where I am, so I've mostly been watching movies. / Ha, maybe later. Thanks for the chat.

Kỳ vọng: không có fact nào

**session** — OK — 0.8 s, 1 lần gọi

- 0.8 s, vào/ra 1233/5: (rỗng)

  đúng kỳ vọng 0, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

**turn** — OK — 1.74 s, 3 lần gọi

- lượt 1: 0.65 s, vào/ra 1156/5: (rỗng)
- lượt 2: 0.54 s, vào/ra 1182/5: (rỗng)
- lượt 3: 0.55 s, vào/ra 1153/5: (rỗng)

  đúng kỳ vọng 0, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

### c15_vi_goal (vi, 2026-10-13)

Tin nhắn người dùng: Mục tiêu của mình là chạy được 10 km vào tháng 4 năm sau. / Khoảng 3 km thôi, chạy xong hơi hụt hơi.

Kỳ vọng: ADD [goal] (10 km | 10km)
Chấp nhận được: ADD (3 km | 3km)

**session** — OK — 0.94 s, 1 lần gọi

- 0.94 s, vào/ra 1293/77: ADD [goal] "Mục tiêu là chạy được 10 km vào tháng 4 năm 2027"; ADD [profile] "Hiện chạy được khoảng 3 km mỗi lần"

  đúng kỳ vọng 1, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 1, cụm cấm 0

**turn** — OK — 1.53 s, 2 lần gọi

- lượt 1: 0.91 s, vào/ra 1194/44: ADD [goal] "Mục tiêu là chạy được 10 km vào tháng 4 năm 2027"
- lượt 2: 0.62 s, vào/ra 1283/5: (rỗng)

  đúng kỳ vọng 1, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

### c16_en_preference_style (en, 2026-10-13)

Tin nhắn người dùng: Before we start: please keep your answers short and use bullet points. I really don't read long paragraphs. / A quick desk stretch for my wrists.

Kỳ vọng: ADD [preference] (short | brief | concise) AND (bullet)

**session** — OK — 0.83 s, 1 lần gọi

- 0.83 s, vào/ra 1223/35: ADD [preference] "Prefers short answers in bullet points, not long paragraphs"

  đúng kỳ vọng 1, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

**turn** — OK — 1.62 s, 2 lần gọi

- lượt 1: 0.7 s, vào/ra 1172/35: ADD [preference] "Prefers short answers in bullet points, not long paragraphs"
- lượt 2: 0.92 s, vào/ra 1226/5: (rỗng)

  đúng kỳ vọng 1, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

### c17_vi_phone_email (vi, 2026-10-13)

Tin nhắn người dùng: Mình bị viêm khớp gối. Bạn nhắn lịch tập qua số 0912 345 678 hoặc email tran.van.a@gmail.com nhé. / Ok, vậy cho mình lịch tập 3 buổi một tuần ở đây luôn.

Kỳ vọng: ADD [condition] (viêm khớp)
Cụm cấm: `0912`, `345 678`, `345678`, `tran.van.a`, `gmail`

**session** — OK — 0.67 s, 1 lần gọi

- 0.67 s, vào/ra 1380/32: ADD [condition] "Bị viêm khớp gối"

  đúng kỳ vọng 1, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

**turn** — OK — 1.54 s, 2 lần gọi

- lượt 1: 0.97 s, vào/ra 1271/32: ADD [condition] "Bị viêm khớp gối"
- lượt 2: 0.57 s, vào/ra 1281/5: (rỗng)

  đúng kỳ vọng 1, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

### c18_en_relative_condition (en, 2026-10-13)

Tin nhắn người dùng: My mom has osteoporosis and I'm worried about her falling. Can you suggest some balance exercises for her? / Thanks, I'll show her those tomorrow.

Kỳ vọng: không có fact nào
Cụm cấm: `osteoporosis`, `mom`, `mother`

**session** — OK — 0.53 s, 1 lần gọi

- 0.53 s, vào/ra 1233/5: (rỗng)

  đúng kỳ vọng 0, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

**turn** — OK — 1.33 s, 2 lần gọi

- lượt 1: 0.58 s, vào/ra 1206/5: (rỗng)
- lượt 2: 0.75 s, vào/ra 1162/5: (rỗng)

  đúng kỳ vọng 0, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

### c19_vi_relative_time (vi, 2026-10-13)

Tin nhắn người dùng: Mình mổ khớp vai phải cách đây 3 tháng rồi, giờ vẫn chưa nhấc tay lên quá đầu được. / Tự tập ở nhà thôi, bạn chỉ mình vài bài an toàn.

Kỳ vọng: ADD [condition] (vai) AND (tháng 7 | 7/2026 | 07/2026 | 2026-07 | tháng 07)
Cụm cấm: `3 tháng`, `ba tháng`, `cách đây`

**session** — REVIEW — 1.21 s, 1 lần gọi

- 1.21 s, vào/ra 1342/120: ADD [condition] "Đã mổ khớp vai phải vào tháng 7 năm 2026"; ADD [condition] "Hiện vẫn chưa nhấc tay lên quá đầu được"; ADD [profile] "Tự tập phục hồi chức năng tại nhà, không tập với kỹ thuật viên"

  đúng kỳ vọng 1, bỏ sót 0, thừa 2, lặp 0, chấp nhận được 0, cụm cấm 0
  - THỪA (người xét): ADD [condition] "Hiện vẫn chưa nhấc tay lên quá đầu được"
  - THỪA (người xét): ADD [profile] "Tự tập phục hồi chức năng tại nhà, không tập với kỹ thuật viên"

**turn** — REVIEW — 1.51 s, 2 lần gọi

- lượt 1: 0.97 s, vào/ra 1242/77: ADD [condition] "Đã mổ khớp vai phải vào tháng 7 năm 2026"; ADD [condition] "Hiện vẫn chưa nhấc được tay lên quá đầu"
- lượt 2: 0.54 s, vào/ra 1329/5: (rỗng)

  đúng kỳ vọng 1, bỏ sót 0, thừa 1, lặp 0, chấp nhận được 0, cụm cấm 0
  - THỪA (người xét): ADD [condition] "Hiện vẫn chưa nhấc được tay lên quá đầu" (lượt 1)

### c20_en_three_facts (en, 2026-10-13)

Tin nhắn người dùng: Hey, I'm back. I broke my wrist skiing last month and the cast just came off. / Yes, my physio told me to avoid push-ups and any weight-bearing on that hand for another six weeks. / Cool. My goal is to be back on skis by February, so I want to be strong by then. / Sounds like a plan. Show me today's mobility routine.

Fact đã lưu: f1: Works as a software developer

Kỳ vọng: ADD [condition] (wrist) AND (September | 2026-09 | 09/2026 | 9/2026) ; ADD [restriction] (push-up | weight-bearing | weight bearing) ; ADD [goal] (ski) AND (February)
Cụm cấm: `last month`

**session** — OK — 1.62 s, 1 lần gọi

- 1.62 s, vào/ra 1391/119: ADD [condition] "Broke their wrist skiing in September 2026; the cast came off in October 2026"; ADD [restriction] "Physio told them to avoid push-ups and any weight-bearing on the broken wrist for six weeks from October 2026"; ADD [goal] "Goal is to be back on skis by February 2027 and strong by then"

  đúng kỳ vọng 3, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

**turn** — OK — 3.05 s, 4 lần gọi

- lượt 1: 0.53 s, vào/ra 1218/43: ADD [condition] "Broke their wrist skiing in September 2026; the cast was removed in October 2026"
- lượt 2: 0.91 s, vào/ra 1268/47: ADD [restriction] "Physio told them to avoid push-ups and any weight-bearing on the hand for six weeks from October 2026"
- lượt 3: 0.98 s, vào/ra 1326/40: ADD [goal] "Goal is to be back on skis by February 2027 and strong by then"
- lượt 4: 0.63 s, vào/ra 1358/5: (rỗng)

  đúng kỳ vọng 3, bỏ sót 0, thừa 0, lặp 0, chấp nhận được 0, cụm cấm 0

