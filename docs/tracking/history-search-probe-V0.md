# History search probe — V0

## Tóm tắt

- Backend: `torch` (intfloat/multilingual-e5-small); 40 lượt trong 10 cuộc hội thoại; 25 câu nhắc lại, 14 câu ngoài chủ đề. Dữ liệu tổng hợp.
- Tổ hợp tốt nhất: **A3** — index A (user+assistant (1500)), chấm điểm 3 (max(1,2)).
- Đúng lượt đứng đầu: **20/25 (80%)**; trong top 3: **23/25 (92%)**; MRR 0.862.
- Similarity top-1 khi đúng: 0.862-0.938 (med 0.907).
- Similarity top-1 của câu ngoài chủ đề: 0.759-0.882 (med 0.852).
- Hai nhóm **chồng lên nhau**: 4/14 câu ngoài chủ đề có top-1 >= 0.862 (đáy của nhóm đúng), và 5/20 câu đúng có top-1 <= 0.882 (đỉnh của nhóm ngoài chủ đề).
- Ngưỡng khuyến nghị: **0.87** — giữ lại đúng lượt (trong top 3) cho 21/25 (84%) câu nhắc lại (đúng ở hạng 1: 19/25); còn 3/14 câu ngoài chủ đề vẫn trả kết quả.

## 9 tổ hợp (câu nhắc lại)

| combo | index | scoring | top-1 | top-3 | MRR | top-1 sim khi đúng | off-topic top-1 sim |
|---|---|---|---|---|---|---|---|
| A1 | user+assistant (1500) | retriever_query | 13/25 (52%) | 17/25 (68%) | 0.626 | 0.833-0.916 (med 0.894) | 0.753-0.866 (med 0.805) |
| A2 | user+assistant (1500) | user_words | 18/25 (72%) | 19/25 (76%) | 0.750 | 0.847-0.938 (med 0.889) | 0.759-0.882 (med 0.852) |
| A3 **(best)** | user+assistant (1500) | max(1,2) | 20/25 (80%) | 23/25 (92%) | 0.862 | 0.862-0.938 (med 0.907) | 0.759-0.882 (med 0.852) |
| B1 | user only | retriever_query | 14/25 (56%) | 19/25 (76%) | 0.676 | 0.820-0.933 (med 0.876) | 0.745-0.844 (med 0.806) |
| B2 | user only | user_words | 18/25 (72%) | 21/25 (84%) | 0.789 | 0.865-0.952 (med 0.901) | 0.762-0.884 (med 0.841) |
| B3 | user only | max(1,2) | 19/25 (76%) | 22/25 (88%) | 0.832 | 0.865-0.952 (med 0.920) | 0.762-0.884 (med 0.841) |
| C1 | assistant only | retriever_query | 11/25 (44%) | 14/25 (56%) | 0.530 | 0.851-0.907 (med 0.878) | 0.752-0.866 (med 0.814) |
| C2 | assistant only | user_words | 15/25 (60%) | 17/25 (68%) | 0.649 | 0.838-0.928 (med 0.874) | 0.756-0.877 (med 0.846) |
| C3 | assistant only | max(1,2) | 18/25 (72%) | 21/25 (84%) | 0.778 | 0.862-0.928 (med 0.887) | 0.756-0.877 (med 0.846) |

## Ngưỡng cho A3

Một câu nhắc lại "còn trả đúng" nếu lượt đúng nằm trong top 3 và có sim >= ngưỡng (công cụ trả tối đa 3 kết quả); câu ngoài chủ đề "trả gì đó" nếu top-1 >= ngưỡng.

| ngưỡng | nhắc lại còn đúng (top 3) | nhắc lại đúng ở hạng 1 | ngoài chủ đề vẫn trả kết quả |
|---|---|---|---|
| 0.76 | 23/25 | 20/25 | 13/14 |
| 0.78 | 23/25 | 20/25 | 13/14 |
| 0.80 | 23/25 | 20/25 | 12/14 |
| 0.82 | 23/25 | 20/25 | 10/14 |
| 0.84 | 22/25 | 20/25 | 9/14 |
| 0.86 | 22/25 | 20/25 | 4/14 |
| 0.87 **(khuyến nghị)** | 21/25 | 19/25 | 3/14 |
| 0.88 | 17/25 | 16/25 | 2/14 |
| 0.90 | 13/25 | 13/25 | 0/14 |
| 0.92 | 4/25 | 4/25 | 0/14 |
| 0.94 | 0/25 | 0/25 | 0/14 |

## Cùng ngôn ngữ vs khác ngôn ngữ với lượt đã lưu

| combo | cùng ngôn ngữ: top-1 / top-3 / MRR | khác ngôn ngữ: top-1 / top-3 / MRR |
|---|---|---|
| A1 | 8/16 / 10/16 / 0.6009 | 5/9 / 7/9 / 0.6698 |
| A2 | 16/16 / 16/16 / 1.0 | 2/9 / 3/9 / 0.3058 |
| A3 **(best)** | 15/16 / 16/16 / 0.9688 | 5/9 / 7/9 / 0.6733 |
| B1 | 10/16 / 13/16 / 0.72 | 4/9 / 6/9 / 0.5971 |
| B2 | 16/16 / 16/16 / 1.0 | 2/9 / 5/9 / 0.4139 |
| B3 | 16/16 / 16/16 / 1.0 | 3/9 / 6/9 / 0.5319 |
| C1 | 6/16 / 8/16 / 0.47 | 5/9 / 6/9 / 0.6376 |
| C2 | 13/16 / 14/16 / 0.8492 | 2/9 / 3/9 / 0.2944 |
| C3 | 13/16 / 15/16 / 0.8576 | 5/9 / 6/9 / 0.6353 |

## Câu nhắc lại sai trong A3

- **r04** (khác ngôn ngữ) "what did you say about hot versus cold packs for my back?" — đúng: t04, hạng của đúng: 3; đứng đầu: t18 (sim 0.857) "What about resistance band external rotation?"
- **r05** (khác ngôn ngữ) "how many reps was that straight leg raise for my knee?" — đúng: t10, t11, hạng của đúng: 12; đứng đầu: t07 (sim 0.844) "Which stretches help with tight hips and back after lifting?"
- **r09** (cùng ngôn ngữ) "when did you say I could go back to deadlifting?" — đúng: t08, hạng của đúng: 2; đứng đầu: t06 (sim 0.884) "I tweaked my lower back deadlifting yesterday. Should I stop training completely?"
- **r13** (khác ngôn ngữ) "hôm trước bạn chỉ mình cách xử lý khi bị bong gân mắt cá chân" — đúng: t23, t24, hạng của đúng: 2; đứng đầu: t26 (sim 0.894) "How do I know it's a fracture and not just a sprain?"
- **r22** (khác ngôn ngữ) "when should I see a doctor about my back pain?" — đúng: t05, hạng của đúng: 7; đứng đầu: t26 (sim 0.866) "How do I know it's a fracture and not just a sprain?"

Dữ liệu thô: `history-search-probe-V0.json`.
