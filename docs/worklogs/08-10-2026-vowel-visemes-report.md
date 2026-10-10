---
date: 2026-10-08
tags: [worklog, report, lip-sync, vowel-visemes, avatar]
author: K
branch: feature/vowel-visemes
status: accepted
---

# Báo cáo: lip sync theo khuôn miệng, không phụ thuộc ngôn ngữ

Log thô từng lần đo và từng lần dựng: [[08-10-2026]]. Plan gốc của lip sync: [[facial-animation-plan]] §9.

## Kết luận

**Owner nghiệm thu ngày 08/10/2026**, bằng mắt, trên Anne, với cả tiếng Việt lẫn tiếng Anh.

Miệng avatar không còn chỉ ra khuôn "a". Khuôn miệng được suy trực tiếp từ audio đang phát bằng hai
đại lượng vật lý, **độ mở** và **độ sáng** của âm, tự chuẩn hoá theo giọng đang nói, rồi pha giữa
5 khuôn VRM `aa/ih/ou/ee/oh`. Không dùng mẫu theo ngôn ngữ hay theo giọng, không đụng backend.

Mức chất lượng đo được: đúng họ khuôn (mở / dẹt / tròn) ở khoảng 70–75 % frame cho cả hai ngôn ngữ.
Đây là mức "miệng sống động và hợp lý", không phải mức "đúng từng nguyên âm".

## Cách làm đã được nghiệm thu

Mỗi frame hình, đọc 1024 mẫu audio gần nhất từ `AnalyserNode` sẵn có:

1. Tính 12 hệ số MFCC (bỏ hệ số 0 nên không phụ thuộc độ to).
2. Dựng lại đường bao phổ đã làm mượt, rồi lấy hai số:
   - **độ mở**: vùng năng lượng thấp (250–1200 Hz) nằm cao hay thấp, ứng với độ mở hàm;
   - **độ sáng**: năng lượng 1.6–3.5 kHz so với 0.25–1.2 kHz, ứng với môi dẹt hay tròn.
3. Chuẩn hoá hai số theo dải giá trị của chính giọng đang nói. Dải này được học trong lúc chạy
   (phân vị 10–90 % trên histogram có quên dần) và cần khoảng 10 giây tiếng nói.
4. Pha 5 khuôn theo khoảng cách tới 5 điểm neo cố định trong mặt phẳng (độ mở, độ sáng).
5. Độ mở miệng tổng thể vẫn lấy từ RMS, với nhịp mềm hơn kiểu cũ (mở 16/s, khép 6/s, đổi khuôn 12/s)
   để miệng không khép hẳn giữa các âm tiết.

Code: `ECA_UI/frontend/src/avatar/mouthShape.ts` và `LipSyncController.ts`.

## Số đo

Từ đã biết nguyên âm, chỉ có phụ âm vô thanh, synth từ TTS local: 42 từ tiếng Anh (giọng
`anne_en`), 37 từ tiếng Việt (giọng `anne_vi`). Chạy trên chính module đã commit (`efad55b5`).

| Điều kiện | Tiếng Việt, đúng họ khuôn | Tiếng Anh, đúng họ khuôn |
|---|---|---|
| Vừa tải trang, chưa học giọng | 66 % frame (27/37 từ) | 64 % frame (27/42 từ) |
| Đã nghe giọng đủ lâu | 74 % frame (30/37 từ) | 71 % frame (33/42 từ) |
| Vừa đổi từ giọng Việt sang giọng Anh, sau 31 giây | | 70 % frame (32/42 từ) |

Theo từng âm khi đã học giọng (đúng họ khuôn): tiếng Việt a 71 %, i 87 %, u 77 %, e/ê 78 %, o/ô 58 %;
tiếng Anh a 66 %, i 96 %, u 59 %, e 48 %, o 83 %.

Chi phí tính toán khoảng 30 µs mỗi frame, tức 0.2 % một frame 60 fps.

## Những cách đã thử và bị loại

| Cách | Kết quả | Vì sao loại |
|---|---|---|
| So MFCC với mẫu từng từ (kiểu uLipSync), mẫu lấy từ giọng `anne_en` | Tiếng Anh: 69 % frame đúng khuôn, 79 % đúng họ. Owner chấp nhận bằng mắt. | Gắn chặt với một giọng. Trên giọng `anne_vi`: 68 % frame dồn về khuôn U, đúng 14/37 từ, mọi "a" thành U. |
| Như trên, thêm chuẩn hoá theo giọng (trừ phổ trung bình) | Tiếng Việt 15–19/37 từ | Không cứu được. |
| Thêm mẫu riêng cho tiếng Việt | Không làm | Owner yêu cầu một cách cho mọi ngôn ngữ, không vá riêng. |
| Bù độ trễ loa bằng `AudioContext.outputLatency` | Không làm | Trình duyệt báo 0.04 giây cho cả loa Bluetooth lẫn loa máy, nên không bù tự động được. |

Hai cổng đo ban đầu (đúng nhãn đa số ở 8/10 từ) đều không đạt với cách dùng mẫu (7/10). Cổng đó
chặt hơn mức mắt người cần; quyết định cuối cùng dựa trên việc nhìn trực tiếp.

## Giới hạn đã biết

- **Không đúng từng nguyên âm.** Âm thanh phản ánh vị trí lưỡi nhiều hơn hình môi. O dễ lẫn với A;
  "u" tiếng Anh hay bị đọc thành I. Mọi cách chỉ nghe audio đã đo đều dừng ở 55–70 % frame đúng khuôn.
- **Vài giây đầu chưa chuẩn.** Dải giá trị của giọng phải được học; đổi giọng thì mất 10–20 giây
  tiếng nói để thích nghi.
- **Loa Bluetooth làm miệng đi trước tiếng** khoảng 150–300 ms. Lỗi có từ trước, kiểu cũ cũng bị,
  và không sửa tự động được.
- Số đo mới có trên hai ngôn ngữ và hai giọng. Việc giữ được mức này ở ngôn ngữ khác là suy luận từ
  ngữ âm học, chưa phải số đo.
- Phụ âm không có khuôn riêng: m/b/p không khép môi hẳn.

Muốn vượt mức này thì phải có phoneme kèm thời điểm. VieNeu 3.6.4 đã phonemize từng đoạn văn bản
trước khi sinh audio nhưng không lộ ra alignment hay duration nào; lấy phoneme từ đó rồi ước lượng
thời điểm là hướng chưa thử.

## Sai sót trong quá trình

- Ngưỡng chọn frame trong plan đo đầu tiên quá chặt, làm lần đo 1 thiếu dữ liệu.
- K dự đoán lần đo thứ hai sẽ đạt sau khi chọn ngưỡng theo dữ liệu đã thấy; nó không đạt.
- K đề xuất chuẩn hoá theo giọng là đủ để sửa tiếng Việt; đo trên từ thật cho thấy không đủ. Từ
  đó mọi đề xuất đều được đo trên từ đã biết nguyên âm trước khi đưa vào plan.
- Cổng số do K đặt đã dẫn tới quyết định dừng sớm. Việc đưa bản thử lên avatar cho Owner nhìn nên
  được làm ngay từ đầu.

## Trạng thái code

- Nhánh `feature/vowel-visemes`, 5 commit trên `4e00d385`, đã push. Tới `efad55b5` chế độ mới còn
  chỉ bật ở bản dev, và code vẫn chứa cách dùng mẫu cùng công cụ lấy mẫu.
- Plan "B-ship" làm phần còn lại: bật chế độ này cho production, bỏ cách dùng mẫu và công cụ lấy
  mẫu, rồi merge vào `feature/langgraph-rewrite`. Chưa lên `release`; đó là quyết định riêng của Owner.
- Chỉ frontend thay đổi. Không tài nguyên AWS, không đổi backend, không thêm request.
- Bẫy deploy cần nhớ: `.github/workflows/deploy-speechllm.yml` lọc theo `SpeechLLm/**`, nên bất kỳ
  file nào thêm vào thư mục đó cũng kích hoạt build lại Lambda TTS khi lên `release`.
