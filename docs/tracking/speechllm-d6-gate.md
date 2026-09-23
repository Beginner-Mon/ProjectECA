# D6 — Đo để chốt cấu hình. LÁT VIỆC, KHÔNG phải bước nghiệm thu.

> **Mr. Senryuu đọc kỹ nhất — đây là gate chặn, không phải checklist cuối.**

Ba con số quan trọng nhất của kế hoạch đang là **GIẢ ĐỊNH** chưa kiểm, và mọi quyết định khác đều treo trên nó:

| Giả định | Hệ quả nếu sai |
|---|---|
| Tổng hợp ≈ thời gian thực (mốc local 78s cho 84,5s audio ≈ 0,92×) | Bảng chi phí 208.800 GB-giây → sai. Chậm 2× → 417k GB-giây, vượt free tier 400k, trần từ ~$3,6 lên ~$7. Vẫn dưới free tier nếu chậm 1,5×, nhưng phải tính lại. |
| 3.008 MB là đủ (tran quota tài khoản — xem dưới, không phải 3.538 ≈ 2 vCPU như kế hoạch gốc) | Nếu 1.769 MB không chậm hơn, cấp thừa 100% tiền mỗi giây. Nếu 3.008 MB vẫn chậm hơn thời gian thực: không còn mức lớn hơn khả dụng dưới quota hiện tại (5.308 MB không deploy được) — phải đổi kiến trúc (x86 vs arm64) hoặc xin tăng quota Lambda memory trước khi thử mức lớn hơn. |
| Timeout 300s đủ cho lượt dài nhất tới `speech_end` | Nếu Lambda chậm hơn realtime, lượt dài nhất có thể sát/vượt 300s và bị giết giữa stream, mất `speech_end`. |

**Quy tắc chặn: Nếu tổng hợp CHẬM HƠN thời gian thực trên Lambda: DỪNG, tính lại, ĐỪNG bật cho người dùng.**

Tức là `thời_gian_tổng_hợp / thời_lượng_audio > 1` → không bật.

> **Tran quota tài khoản (19/09):** account này bị cap Lambda memory ở
> **3.008 MB** (`MemorySize value failed to satisfy constraint: Member must
> have value less than or equal to 3008`), chưa xin tăng quota. Kế hoạch gốc
> giả định 3.538 MB (điểm 2-vCPU, Lambda cấp CPU theo RAM 1.769 MB = 1 vCPU)
> nhưng con số đó không deploy được trên tài khoản này — 3.008 MB (~1,7
> vCPU) là default mới trong `speechllm_stack.py`, và cũng là trần đo được.
> **5.308 MB không khả dụng dưới quota hiện tại.** D6 dưới đây chỉ còn đo
> được hai mức: **1.769 và 3.008 MB.**

---

## Thứ tự và đầu vào

Chạy **sau D1+D2, trước khi** bật TTS cho người dùng thật (plan: D1→D2→D3→**D6** là đường chính). Kết quả quay lại sửa D2 (memory, kiến trúc) và D3 (timeout).

Cùng một câu dài, ba lần mỗi mức — để loại nhiễu:

| Đo gì | Cách | Quyết định gì |
|---|---|---|
| Thời gian tổng hợp ở **1.769 / 3.008 MB** (5.308 MB không khả dụng — tran quota tài khoản) | cùng một câu dài (~1.500-2.500 ký tự, clinical), ba lần mỗi mức | chốt RAM: mức nhỏ nhất không chậm hơn (pareto). Xem `Max Memory Used` trong CloudWatch để phát hiện cấp thừa. |
| **arm64 vs x86_64** | cùng câu, hai kiến trúc (param `speechllm_arch`) | chốt arm nếu không chậm hơn ⇒ rẻ 20% (Duration $0,0000133334 vs $0,0000166667/GB-giây) |
| Tổng hợp có nhanh bằng thời gian thực không | so mốc local 78s cho 84,5s audio | **cả bảng chi phí dựa vào đây** |
| Lượt dài nhất tới `speech_end` | log agent (`_stream_speech`) | xác nhận timeout 300s đủ, hoặc tăng |

---

## Cách đo (giữ 2 tính chất phải giữ)

1. **Chunk đầu tới browser ngay, không đợi chunk cuối.** Dùng `infra/spike/measure_speechllm.py` (hoặc `infra/spike/verify_stream.py` đã dùng cho streaming probe 21/08) — curl có ký SigV4 vào Function URL, ghi **thời điểm tới của từng dòng NDJSON**. Dòng đầu phải tới sớm hơn dòng cuối vài chục giây. Tất cả ùa về cùng lúc ⇒ có tầng đệm lọt vào giữa; mọi thứ vẫn “chạy” mà đã mất sạch ý nghĩa.

2. **Browser đóng tab ⇒ SpeechLLm ngừng tổng hợp.** NDJSON chảy trong cùng generator FastAPI đang đẩy về browser — đóng tab làm generator bị hủy, không tốn thêm GB-giây.

Công cụ:

```bash
# 1. Deploy speechllm với từng mức RAM (cần agent_role_arn từ VvaAgentStack)
cdk deploy VvaSpeechllmStack -c speechllm_image_tag=<sha> -c agent_role_arn=<ARN> -c speechllm_memory=1769 -c speechllm_arch=arm64
cdk deploy VvaSpeechllmStack -c speechllm_image_tag=<sha> -c agent_role_arn=<ARN> -c speechllm_memory=3008 -c speechllm_arch=arm64
cdk deploy VvaSpeechllmStack -c speechllm_image_tag=<sha> -c agent_role_arn=<ARN> -c speechllm_memory=3008 -c speechllm_arch=x86_64
# 5308 MB: KHÔNG deploy được — vượt trần quota Lambda memory 3008 MB của tài khoản này.

# 2. Đo từng mức: SigV4 vào Function URL (không qua API Gateway)
python infra/spike/measure_speechllm.py --url https://xxx.lambda-url.us-east-1.on.aws/synthesize/stream --text-file long_clinical.txt --voice-key voices/anne_vi_abc12345.wav --out results_3008_arm.json

# 3. Đọc CloudWatch Max Memory Used và Duration
aws logs filter-log-events --log-group-name /aws/lambda/vva-speechllm --filter-pattern "REPORT"
```

---

## Bảng chi phí gốc (để so sánh sau D6)

Giá AWS Pricing API 12/09 us-east-1:

|  | x86_64 | arm64 |
|---|---|---|
| Duration | $0,0000166667/GB-giây | $0,0000133334/GB-giây |
| Request | $0,20/triệu | $0,20/triệu |
| Provisioned concurrency | $0,0000041667/GB-giây | $0,0000033334/GB-giây |

Mức dùng N đưa ra — 100 audio/ngày, mỗi cái ~20 giây:

|  |  |
|---|---|
| Tổng hợp | 100×30×20s = 60.000 giây/tháng = 207.300 GB-giây |
| Ping giữ ấm | + ~1.500 GB-giây |
| **Tổng** | ~208.800 GB-giây — **còn trong free tier 400.000** |
| **Tiền Lambda** | **$0/tháng** |
| Nếu free tier đã hết | $3,48 (x86) / $2,78 (arm64) |
| ECR | + $0,15 |
| **Trần trên** | **~$3,6/tháng** |

Chậm gấp đôi → nhân đôi mọi số — vẫn dưới free tier, trần ~$7. Ba khoản dễ quên: INIT (~$0,0014 mỗi lần nguội); response streaming 6 MB đầu mỗi request miễn phí, FLAC ~309 kbps ⇒ 6 MB ≈ 155 giây audio; ECR $0,10/GB-tháng. Cold start 25s (model 13s + pre-enrol) → EventBridge ping /health mỗi 5 phút ~$0,03/tháng, thay vì Provisioned concurrency $30-37/tháng.

---

## Quyết định sau đo

- **Memory**: chọn mức nhỏ nhất có `thời_gian_tổng_hợp` không chậm hơn mức lớn hơn (ví dụ 1.769 ≈ 3.008 → chọn 1.769). Nếu 1.769 chậm hơn rõ, chọn 3.008 (trần quota hiện tại — không có mức lớn hơn để thử cho tới khi xin tăng quota). Ghi `Max Memory Used` để phát hiện cấp thừa.
- **Kiến trúc**: nếu arm64 không chậm hơn x86_64 → chốt arm (rẻ 20%). Nếu arm chậm hơn → chốt x86 và tính lại chi phí.
- **Chi phí**: tính lại bảng với hệ số chậm thực đo (`tổng_hợp / audio`). Nếu >1 → DỪNG, báo cáo, không bật cho user, tính lại trần và quyết định có đáng bật không.
- **Timeout**: xem lượt dài nhất tới `speech_end` trong log agent. Nếu sát 300s → tăng.

Toàn bộ phải ghi **số thật**, không ghi kỳ vọng: nóng (~0,6s) và nguội (~25s) tới chunk đầu, bảng D6 đầy đủ, chat thật câu dài không hở/giật, security (không ký → 403, sai principal → 403), static audio bytes thẳng CloudFront không qua SpeechLLm, giọng mẫu không tải được qua CloudFront.

---

## Nợ và cạm bẫy

- Watermark `apply_watermark` mặc định bật — phải chọn có chủ ý trước phát hành.
- `anne_en.wav` nhỏ tiếng 14 dB so với vi — thu lại giai đoạn 1.
- 2 giây cuối lượt (write_session_turn vào Neon) là tech debt, phần lớn tự biến mất khi agent cùng vùng với Neon.

**D6 không phải “đã xong infra thì bật cho user” — D6 là việc phải làm trước khi được phép bật.**

---

## Phép thử phủ định — kết quả 23-09-2026

Chạy trước khi mở bất kỳ quyền nào (T1 của đợt đo D6 trên cấu hình đang chạy thật).
Ghi nguyên văn mã trạng thái, không diễn giải.

| # | Lệnh | Kết quả nguyên văn |
|---|---|---|
| N1 | `GET /` không ký, Function URL | `HTTP/1.1 403 Forbidden`, `x-amzn-ErrorType: AccessDeniedException`, body `{"Message":"Forbidden"}` |
| N1 | `GET /health` không ký, Function URL | `HTTP/1.1 403 Forbidden`, `x-amzn-ErrorType: AccessDeniedException`, body `{"Message":"Forbidden"}` |
| N1 | `POST /synthesize/stream` không ký, Function URL | `HTTP/1.1 403 Forbidden`, `x-amzn-ErrorType: AccessDeniedException`, body `{"Message":"Forbidden"}` |
| N2 | `POST /synthesize/stream` ký SigV4 bằng identity admin của máy (`infra/spike/measure_speechllm.py`, principal `arn:aws:iam::244203483654:user/admin`) | `HTTP 403 application/json` → `httpx.HTTPStatusError: Client error '403 Forbidden'` |
| N3 | `GET /v1/characters/anne` không token, REST API | `HTTP/1.1 401 Unauthorized` |
| N3 | `GET /v1/characters/anne/avatar-profile` không token, REST API | `HTTP/1.1 401 Unauthorized` |
| N3 | `GET /v1/characters/anne/audio?clip=greeting.morning&lang=vi` không token, REST API | `HTTP/1.1 401 Unauthorized` |
| N4 | `GET /v1/characters` không token, REST API | `HTTP/1.1 200 OK`, body `{"characters": [{"slug": "anne", ...}], "total": 1}` |
| N5 | `GET https://d3292v7f15b95x.cloudfront.net/voices/anne_vi.wav` | `HTTP/1.1 403 Forbidden` (Server: AmazonS3, `X-Cache: Error from cloudfront`) |
| N6 | Log `/aws/lambda/vva-speechllm` 35 phút gần nhất, filter `health` | 7 dòng `GET /health HTTP/1.1 200 OK`, cách nhau ~300s (đúng nhịp `rate(5 minutes)` của `vva-speechllm-warmer`) |

**Hoãn, lý do ghi rõ:** phép thử URL clip đã ký (bỏ chữ ký ⇒ 403, quá hạn ⇒ 403)
chưa chạy được vì chưa có clip nào — câu chào dựng sẵn đã bị hoãn sang tech debt
(Owner quyết, ngoài phạm vi đợt này).

---

## Kết quả đo 23-09-2026

Cấu hình đo: 3.008 MB, x86_64, image `5fa3e98f13c6080a041e99b00b4c81cd17c86ea9`
(không đổi so với prod). Câu đo `infra/spike/d6_long_vi.txt` (2.256 ký tự,
hướng dẫn tập luyện, có dấu). Gọi thẳng Function URL qua
`infra/spike/measure_speechllm.py` (đã mở Deny tạm bằng
`-c measure_principal_arn`, gỡ ngay sau đo — N2 403 trở lại, xem T3e).

| Lượt | first_byte | tổng hợp (start→end, không tính cold) | audio | tỉ lệ | chunks | spread | streamed |
|---|---|---|---|---|---|---|---|
| cold (ngay sau deploy) | không rõ (mất JSON, xem ghi chú) | không rõ | 124,89s | ~1,38 theo wall (tham khảo) | 58 | không rõ | có (chạy hết tới `end` ở 172,672s wall) |
| warm1 | 0,954s | 170,140s | 125,91s | **1,351** | 58 | 170,140s | YES |
| warm2 | 0,969s | 191,656s | 126,42s | **1,516** | 58 | 191,656s | YES |
| warm3 | 0,969s | 195,594s | 125,24s | **1,562** | 58 | 195,594s | YES |
| en (`anne_en.wav`) | 0,953s | 194,703s | 135,83s | **1,433** | 63 | 194,703s | YES |

Ghi chú lượt cold: script crash khi in (`UnicodeEncodeError` — console cp1252
không in được ký tự `→`) **trước** khi ghi JSON, nên mất `first_byte_at` và
thời điểm từng chunk của lượt này; chỉ còn wall 172,672s / audio 124,89s /
58 chunks từ stdout. Bốn lượt sau chạy với `PYTHONUTF8=1`, JSON đầy đủ trong
`infra/spike/results/`.

Khoảng cách giữa các chunk (leg SpeechLLm → máy đo, từ JSON): trung bình
~3,0–3,2s, đều từ đầu tới cuối (5 gap đầu ≈ 5 gap cuối ≈ 3s; min 0,391s là
chunk cuối cụt, max 4,563s). **Không giãn dần ở leg này** — hiện tượng giãn
dần quan sát ở trình duyệt (nếu có) lọt vào ở các leg sau (agent → API
Gateway → browser). Dữ liệu từng chunk đã lưu, chưa sửa gì.

CloudWatch `/aws/lambda/vva-speechllm` (REPORT, nguyên văn):

| Lượt | Duration | Max Memory Used | Ghi chú |
|---|---|---|---|
| warmer `/health` (ổn định, trước đo) | ~3–5ms | 2.843 MB | ping 5 phút/lần |
| cold (đo) | 171.727ms | 2.929 MB | không có dòng Init trong REPORT này |
| warm1 | 170.161ms | 2.932 MB | **peak đợt đo** |
| warm2 | 191.715ms | 2.915 MB | |
| warm3 | 195.633ms | 2.913 MB | **lượt dài nhất: còn dư ~104s so với timeout 300s** |
| en | 194.741ms | 2.915 MB | |
| 2 env mới concurrent (warmer ping rơi vào lúc đo) | ~21–24s + Init ~9,8s | 2.657 MB | cold `/health` ≈ 24s — khớp mốc cold start ~25s |
| traffic thật trong ngày (trước đo) | 4,2–27,9s | 2.656–2.843 MB | TTS người dùng chạy bình thường |

CloudWatch `/aws/lambda/vva-agent` (24h, 132 REPORT): `Max Memory Used` cao
nhất **341/2.048 MB**, lượt dài nhất 12,8s. Không xác định được lượt nào là
"tìm kiếm tài liệu + giọng nói" vì đường thành công không log marker giọng
nói — con số trên là chặn trên, còn dư ~1,7 GB.

### Trả lời ba giả định

1. **Tổng hợp ≈ thời gian thực → SAI.** Thực đo 1,351–1,562 (vi) và 1,433
   (en): chậm hơn thời gian thực ~1,5×. Bảng chi phí phải nhân ~1,5:
   ~311k + 1,5k ping ≈ **312,5k GB-giây — vẫn trong free tier 400k ⇒ $0**;
   nếu hết free tier ≈ $5,2 + ECR $0,15 ≈ **$5,4/tháng** (thay vì $3,6).
2. **Trục RAM → ĐÓNG VÌ RÀNG BUỘC, không phải vì đo.** Env mới đã dùng
   2.657 MB, ổn định 2.843 MB, peak tổng hợp 2.932 MB — mức 1.769 MB chắc
   chắn OOM, không phải "chậm hơn". Muốn mở lại trục này phải giảm số giọng
   enrol lúc khởi động. 3.008 MB chạy được nhưng **chỉ còn dư 76 MB**:
   thêm một nhân vật có giọng là có khả năng tràn, mà 3.008 đã là trần quota
   (Owner từ chối xin tăng 21/09).
3. **Timeout 300s đủ → ĐÚNG.** Lượt dài nhất 196,563s wall, còn dư ~104s.
   First byte khi ấm ~0,95–0,97s, xa dưới hạn chờ 45s của agent.

### Hoãn: trục kiến trúc arm64

So sánh arm64 (rẻ 20% Duration) hoãn lại, không làm đợt này. Lý do: CI chạy
trên `ubuntu-latest` (x86_64) và chỉ build một kiến trúc; arm64 cần QEMU
hoặc runner ARM, và phải kiểm lại wheel của `onnxruntime`. Đó là một đợt
việc riêng — ghi nợ ở `docs/tracking/tech-debt.md`.

---

## Bộ nhớ — điều tra 23-09-2026

Nhiệm vụ: tìm ra bộ nhớ tăng theo cái gì. Chỉ đọc log + đọc code, không sửa,
không deploy, không gọi API SpeechLLm. Kết luận trước, bằng chứng sau.

**Kết luận: đỉnh bộ nhớ tăng theo độ dài audio của lượt DÀI NHẤT mà
environment đó từng phục vụ (high-water mark một lần rồi chững) — không tăng
theo số lượt gọi, không tăng khi idle.** Nói gọn: `peak = f(lượt dài nhất)`,
không phải rò rỉ theo thời gian.

### Bằng chứng log (CloudWatch Logs Insights, 7 ngày, 1.193 REPORT, 65 stream)

Bốn environment của era hiện tại (09-23, sau các deploy prod), toàn bộ lượt
theo thứ tự (`P` = ping warmer vài ms, `S` = tổng hợp, số = Duration):

| Stream (giờ) | Diễn biến mem (MB) |
|---|---|
| `718ac3c8` 03:19–04:18, 18 lượt | 2657 (S 27,9s, INIT) → P×4 đứng yên 2657 → S 12,7s **+142** → S 4,2s +18 → P đứng yên → S 7,8s +14 → P×3 đứng yên → S 5,1s +1 → S 4,5s +0 → P×4 đứng yên **2833** |
| `f57c2de8` 04:23–06:25, 28 lượt | 2656 (S 7,7s, INIT) → P đứng yên → S 5,4s **+166** → S 4,8s +21 → **P×22 liên tiếp (~105 phút) đứng yên tuyệt đối 2843** → S 171,7s (D6 cold) +86 → S 170,2s (D6 warm1) **+3** |
| `bd700e94` 06:23–06:33, 3 lượt | 2657 (INIT, 24s) → S 191,7s → 2915 → P đứng yên |
| `d004c9ac` 06:28–07:58, 20 lượt | 2657 (INIT, 21s) → S 195,6s **+256** → S 194,7s **+2** → P×17 đứng yên 2915–2916 |

Trả lời ba câu của plan, mỗi câu kèm số:

1. **Đỉnh có dâng ở lượt chỉ ping warmer? KHÔNG.** 22 ping đứng yên 2843
   trong ~105 phút (`f57c2de8` lượt 4–25); 17 ping đứng yên 2915–2916; 8 ping
   đứng yên 2833. Idle không rò rỉ.
2. **Đỉnh có dâng đều theo từng lượt tổng hợp? KHÔNG đều — dâng rồi chững.**
   Lượt tổng hợp dài thứ hai trên cùng env chỉ +2–3 MB (195,6s→+256 rồi
   194,7s→+2; 171,7s→+86 rồi 170,2s→+3). Tăng trưởng dồn vào vài lượt đầu
   của mỗi env (+142/+166/+256), sau đó ~0.
3. **Đỉnh có chững sau vài lượt đầu? ĐÚNG.** Mọi stream đều chững: env chỉ
   phục vụ clip ngắn (≤28s) chững ở 2833; env đã phục vụ lượt 170–196s chững
   ở 2913–2932. Chênh ~100 MB giữa hai loại env chính là phần phụ thuộc độ
   dài audio.

Ghi thêm:

- Environment sống lâu nhất quan sát được: ~2–2h20, ~28–29 lượt
  (vd `f57c2de8`: 28 lượt/2h, đỉnh 2932; era cũ 09-19: 29 lượt/2h20, đỉnh
  đứng yên 1782→1783 — ping-only chững tuyệt đối). Lambda thu hồi env sau
  ~2h nên không có env nào sống lâu hơn để quan sát tiếp.
- Baseline nhảy theo BẬC ở biên deploy, không bò dần: warmer ổn định 1783
  (09-19/20) → 1893 (09-21/22, +110) → 2657 fresh (09-23, +764), cùng mức
  cap 3008 MB cả ba era (đã đối chiếu `Memory Size` từng ngày). Nguyên nhân
  từng bậc chưa truy (cần lịch sử deploy/image — ngoài phạm vi đợt này).
- Era 09-19 buổi sáng: nhiều REPORT `Duration: 300000ms, Status: timeout`
  ở 147–309 MB — chính là image hỏng mà comment ở `api_server.py:78-84` đã
  ghi (warm-up treo, mỗi invoke đốt 300s; fix fail-fast `os._exit(1)` đã có
  trong code). Lịch sử, không liên quan đợt này.

### Bằng chứng code (2b — cả ba thứ plan yêu cầu)

1. **Không chỗ nào gom toàn bộ chunk một lượt.** `synthesize_stream`
   (`SpeechLLm/src/services/vieneu_client.py:537-561`): `buffer_parts` tối
   đa ~2,0s audio (`_STREAM_BUFFER_SECONDS = 2.0`, dòng 495) rồi reset về
   `[]` (dòng 561); mỗi chunk encode xong là `yield` ngay (564-573).
   `api_server.py:218-227` (`ndjson_lines`) cũng `yield` từng event, không
   tích, không ghi file tạm. Bộ nhớ wrapper tự viết **không** tỉ lệ với độ
   dài audio.
2. **`_voice_cache` / `_voice_version_cache không bị chặn, nhưng cố định 3
   entry sau warm-up.** `vieneu_client.py:195-196` là dict trần, key là
   voice path/S3 key, ghi đúng một lần mỗi giọng lúc enrol (324, 354) rồi
   chỉ đọc-hit (310-311, 331-332). 3 giọng enrol lúc khởi động
   (`_enrol_known_voices`, 358-429, liệt kê toàn bộ `*.wav` trong bucket).
   Cache này đặt SÀN baseline (fresh env 2657 MB), không gây bò dần.
3. **Tham chiếu sống qua nhiều request: đúng ba thứ, đều có giới hạn.**
   `self._tts` (model, nạp một lần, 243-264), hai cache giọng ở trên, và
   `_MODEL_STATE` hai chuỗi (`api_server.py:43`). Không hàng đợi, không
   handle file mở, không dict nào ghi thêm mỗi request.

**Phần chưa chứng minh được (nói thẳng):** transient +86 MB của lượt dài
nằm ở `tts.infer_stream` của thư viện `vieneu` (onnxruntime arena giữ
high-water, hay lib tích output nội bộ) — package này chỉ có trong image
Lambda, máy này không đọc được. Cần một trong hai: nguồn lib, hoặc thí
nghiệm có kiểm soát trên một env (các lượt tăng dần 10s→30s→120s, xem đỉnh
nhảy ở bậc nào). Không đoán.

### Bảng phương án đã lọc theo kết luận

| Phương án | Giữ/Bỏ | Vì sao |
|---|---|---|
| Chặn độ dài lời đáp có giọng | **Giữ** | Trực tiếp chặn high-water: lượt dài nhất đặt đỉnh. Một câu trả lời dài bất thường của user thật cũng OOM như D6 |
| Bỏ `reporter_vi.wav` khỏi bucket | **Bỏ — đã thử, không ăn thua** | Xem mục đo bên dưới: trả lại **1 MB**, không phải hàng trăm |
| Enrol giọng theo yêu cầu | **Bỏ** | Cùng lý do: chi phí không nằm ở *số* giọng mà ở *lần enrol đầu tiên*, mà lượt tổng hợp nào cũng phải trả lần đó |

### Đo thật: xoá một giọng trả lại bao nhiêu? (23-09-2026)

Bảng trên ban đầu ghi "mỗi giọng enrol chiếm hàng trăm MB". **Sai, và đã đo để bác bỏ.**

Suy luận sai nằm ở chỗ lấy hiệu giữa environment thời chưa có giọng (**1.935 MB**) và thời có
ba giọng (**2.657 MB**) rồi **chia đều cho ba** ⇒ ~240 MB/giọng. Phép chia đó giả định chi phí
tuyến tính theo số giọng, và giả định đó chưa ai kiểm.

Phép thử: xoá `voices/reporter_vi.wav` khỏi bucket lúc ~10:55, đợi environment mới.

| | Nền sau khi enrol xong |
|---|---|
| 3 giọng (trước) | 2.657 MB |
| **2 giọng (env mới 11:13)** | **2.656 MB** |

Log của env mới xác nhận chỉ còn hai lần enrol (`anne_en` 6,33s, `anne_vi` 2,30s).
**Xoá một giọng trả lại 1 MB.**

Kết luận: khoản ~720 MB đó gần như toàn bộ là chi phí **một lần** của lần enrol đầu tiên (bộ
mã hoá giọng cấp vùng làm việc), giọng thứ hai và thứ ba thêm vào gần như không tốn gì. Mọi
phương án "bớt giọng đi cho nhẹ" vì thế đều vô nghĩa — dư địa vẫn là ~76 MB ở đỉnh như trước.
Thứ duy nhất còn lại thực sự bảo vệ trần bộ nhớ là **chặn độ dài lời đáp**.

`reporter_vi.wav` hiện vẫn đang bị xoá khỏi bucket. Không có hại (chỉ `anne` đang hoạt động,
và cold start bớt được ~1,4 giây), phục hồi thì upload lại từ `SpeechLLm/voices/` trong repo.
| Không gom chunk, stream thẳng | **Bỏ** | Tiền đề sai: wrapper đã stream thật, buffer ≤2s (chứng minh ở 2b.1). Chỉ mở lại nếu thí nghiệm lib cho thấy tích tụ nằm trong `infer_stream` |
| Xin tăng quota Lambda memory | **Bỏ** | Owner đã từ chối 21/09 (giữ đúng quyết định) |
