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
