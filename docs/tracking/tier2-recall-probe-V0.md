# tier2-recall-probe-V0

- facts: 30, probes: 77 (đúng: 63, nhiễu: 14)
- model: intfloat/multilingual-e5-small (embed local, không LLM/DB)

## Recall@K (fact đúng trong top-K)

| K | hit/total | rate |
|---|---|---|
| 1 | 47/63 | 0.746 |
| 2 | 51/63 | 0.8095 |
| 3 | 54/63 | 0.8571 |
| 5 | 56/63 | 0.8889 |

## Nhiễu

- max mỗi câu nhiễu: [0.7241, 0.7306, 0.7664, 0.787, 0.7997, 0.8038, 0.8075, 0.8143, 0.8144, 0.8206, 0.8282, 0.8283, 0.8313, 0.8343]
- p95 nhiễu: 0.8343 → floor (làm tròn lên 0.01): 0.84

## Tham số chọn theo luật

- K = None (nhỏ nhất đạt ≥ 90%)
- tier2_floor = 0.84 (p95 nhiễu, làm tròn lên 0.01)
- cặp đúng dưới floor: 33/63 (0.5238)
- **DỪNG VÀ BÁO TRI**: floor loại > 10% cặp đúng.
  - q_name_01 expect=f_name score=0.7833 rank=1: brother do you know my name?
  - q_name_02 expect=f_name score=0.7864 rank=12: then who am I?
  - q_name_03 expect=f_name score=0.7788 rank=5: who am I?
  - q_name_04 expect=f_name score=0.7992 rank=7: do you know my name?
  - q_name_05 expect=f_name score=0.8022 rank=1: tên tôi là gì?
  - q_name_06 expect=f_name score=0.8135 rank=1: tôi tên gì?
  - q_name_08 expect=f_name score=0.8333 rank=1: tôi thích được gọi là gì?
  - q_name_09 expect=f_name score=0.8192 rank=1: gọi tôi là gì?
  - q_name_10 expect=f_name score=0.8042 rank=14: nhắc lại tên tôi xem nào?
  - q_food_03 expect=f_pho score=0.8252 rank=3: what is my favorite food?
  - q_food_04 expect=f_pho score=0.8355 rank=1: do you remember what I like to eat?
  - q_food_09 expect=f_noodles score=0.8322 rank=1: what noodles do I like and what worries me?
  - q_health_02 expect=f_knee_rehab score=0.8089 rank=3: what week of knee rehab am I in?
  - q_health_03 expect=f_back_pain score=0.8262 rank=1: where do I hurt when I sit long?
  - q_health_04 expect=f_back_pain score=0.8141 rank=9: tôi bị đau ở đâu?
  - q_health_06 expect=f_shoulder score=0.7989 rank=2: which shoulder hurts when I lift my arm?
  - q_health_08 expect=f_run_goal score=0.803 rank=1: what is my running goal?
  - q_health_11 expect=f_knee_surgery score=0.811 rank=1: When did I have my left knee surgery?
  - q_health_12 expect=f_knee_surgery score=0.8348 rank=1: tôi mổ gối khi nào?
  - q_health_14 expect=f_ice score=0.818 rank=1: how do I ice my swollen knee?
  - q_health_15 expect=f_latex score=0.8288 rank=1: tôi có dị ứng gì không?
  - q_health_16 expect=f_latex score=0.824 rank=1: what am I allergic to?
  - q_sport_02 expect=f_badminton score=0.8016 rank=11: tôi chơi cầu lông mấy lần một tuần?
  - q_sport_05 expect=f_squat score=0.8361 rank=1: how many squats per set for my sore knee?
  - q_proj_01 expect=f_proj_serverless score=0.8291 rank=1: Cho project A thì ưu tiên gì?
  - q_proj_02 expect=f_proj_serverless score=0.7737 rank=1: what does project A prioritize?
  - q_proj_03 expect=f_proj_deadline score=0.8384 rank=1: deadline của project A là khi nào?
  - q_proj_06 expect=f_job score=0.8307 rank=1: what is my job now?
  - q_style_02 expect=f_short_reply score=0.8122 rank=1: should your answers be long or short for me?
  - q_style_04 expect=f_morning_exercise score=0.8167 rank=1: do I prefer morning or evening workouts?
  - q_multi_01 expect=f_name score=0.8071 rank=28: who am I? tôi bị đau lưng, gợi ý vài bài tập giúp tôi
  - q_multi_03 expect=f_name score=0.8039 rank=11: tôi tên gì và sáng tôi uống gì?
  - q_multi_04 expect=f_run_goal score=0.7863 rank=5: what is my running goal and how often do I play badminton?

## Từng probe (expect, rank, điểm fact đúng, top-1)

| probe | expect | rank | score | top1 |
|---|---|---|---|---|
| q_name_01 | f_name | 1 | 0.7833 | f_name:0.7833 |
| q_name_02 | f_name | 12 | 0.7864 | f_latex:0.8269 |
| q_name_03 | f_name | 5 | 0.7788 | f_latex:0.8109 |
| q_name_04 | f_name | 7 | 0.7992 | f_latex:0.8127 |
| q_name_05 | f_name | 1 | 0.8022 | f_name:0.8022 |
| q_name_06 | f_name | 1 | 0.8135 | f_name:0.8135 |
| q_name_07 | f_name | 1 | 0.8713 | f_name:0.8713 |
| q_name_08 | f_name | 1 | 0.8333 | f_name:0.8333 |
| q_name_09 | f_name | 1 | 0.8192 | f_name:0.8192 |
| q_name_10 | f_name | 14 | 0.8042 | f_vietnamese:0.8341 |
| q_food_01 | f_pho | 2 | 0.8468 | f_noodles:0.8528 |
| q_food_02 | f_pho | 2 | 0.8768 | f_noodles:0.8802 |
| q_food_03 | f_pho | 3 | 0.8252 | f_chay:0.8303 |
| q_food_04 | f_pho | 1 | 0.8355 | f_pho:0.8355 |
| q_food_05 | f_coffee | 1 | 0.8597 | f_coffee:0.8597 |
| q_food_06 | f_coffee | 1 | 0.8543 | f_coffee:0.8543 |
| q_food_07 | f_chay | 1 | 0.8972 | f_chay:0.8972 |
| q_food_08 | f_noodles | 1 | 0.8592 | f_noodles:0.8592 |
| q_food_09 | f_noodles | 1 | 0.8322 | f_noodles:0.8322 |
| q_health_01 | f_knee_rehab | 1 | 0.8837 | f_knee_rehab:0.8837 |
| q_health_02 | f_knee_rehab | 3 | 0.8089 | f_knee_surgery:0.8169 |
| q_health_03 | f_back_pain | 1 | 0.8262 | f_back_pain:0.8262 |
| q_health_04 | f_back_pain | 9 | 0.8141 | f_shoulder:0.8578 |
| q_health_05 | f_shoulder | 1 | 0.9111 | f_shoulder:0.9111 |
| q_health_06 | f_shoulder | 2 | 0.7989 | f_back_pain:0.8252 |
| q_health_07 | f_run_goal | 1 | 0.8623 | f_run_goal:0.8623 |
| q_health_08 | f_run_goal | 1 | 0.803 | f_run_goal:0.803 |
| q_health_09 | f_sleep | 1 | 0.8855 | f_sleep:0.8855 |
| q_health_10 | f_sleep | 1 | 0.8618 | f_sleep:0.8618 |
| q_health_11 | f_knee_surgery | 1 | 0.811 | f_knee_surgery:0.811 |
| q_health_12 | f_knee_surgery | 1 | 0.8348 | f_knee_surgery:0.8348 |
| q_health_13 | f_ice | 1 | 0.9006 | f_ice:0.9006 |
| q_health_14 | f_ice | 1 | 0.818 | f_ice:0.818 |
| q_health_15 | f_latex | 1 | 0.8288 | f_latex:0.8288 |
| q_health_16 | f_latex | 1 | 0.824 | f_latex:0.824 |
| q_sport_01 | f_badminton | 1 | 0.8759 | f_badminton:0.8759 |
| q_sport_02 | f_badminton | 11 | 0.8016 | f_heel_slide:0.83 |
| q_sport_03 | f_swim | 1 | 0.89 | f_swim:0.89 |
| q_sport_04 | f_squat | 1 | 0.9015 | f_squat:0.9015 |
| q_sport_05 | f_squat | 1 | 0.8361 | f_squat:0.8361 |
| q_sport_06 | f_walk | 1 | 0.8864 | f_walk:0.8864 |
| q_sport_07 | f_heel_slide | 1 | 0.88 | f_heel_slide:0.88 |
| q_sport_08 | f_plank | 1 | 0.8485 | f_plank:0.8485 |
| q_proj_01 | f_proj_serverless | 1 | 0.8291 | f_proj_serverless:0.8291 |
| q_proj_02 | f_proj_serverless | 1 | 0.7737 | f_proj_serverless:0.7737 |
| q_proj_03 | f_proj_deadline | 1 | 0.8384 | f_proj_deadline:0.8384 |
| q_proj_04 | f_proj_postgres | 1 | 0.858 | f_proj_postgres:0.858 |
| q_proj_05 | f_job | 1 | 0.8784 | f_job:0.8784 |
| q_proj_06 | f_job | 1 | 0.8307 | f_job:0.8307 |
| q_style_01 | f_short_reply | 2 | 0.8474 | f_vietnamese:0.8973 |
| q_style_02 | f_short_reply | 1 | 0.8122 | f_short_reply:0.8122 |
| q_style_03 | f_morning_exercise | 1 | 0.8846 | f_morning_exercise:0.8846 |
| q_style_04 | f_morning_exercise | 1 | 0.8167 | f_morning_exercise:0.8167 |
| q_style_05 | f_evening_exercise | 1 | 0.8732 | f_evening_exercise:0.8732 |
| q_style_06 | f_vietnamese | 1 | 0.8987 | f_vietnamese:0.8987 |
| q_life_01 | f_city | 1 | 0.8774 | f_city:0.8774 |
| q_life_02 | f_city | 1 | 0.8588 | f_city:0.8588 |
| q_life_03 | f_dog | 1 | 0.882 | f_dog:0.882 |
| q_life_04 | f_water | 1 | 0.8974 | f_water:0.8974 |
| q_multi_01 | f_name | 28 | 0.8071 | f_knee_rehab:0.8735 |
| q_multi_02 | f_pho | 3 | 0.8628 | f_noodles:0.8692 |
| q_multi_03 | f_name | 11 | 0.8039 | f_coffee:0.8377 |
| q_multi_04 | f_run_goal | 5 | 0.7863 | f_badminton:0.8433 |
| q_noise_01 | None | None | None | f_city:0.7664 |
| q_noise_02 | None | None | None | f_chay:0.8313 |
| q_noise_03 | None | None | None | f_coffee:0.7306 |
| q_noise_04 | None | None | None | f_short_reply:0.8283 |
| q_noise_05 | None | None | None | f_dog:0.8343 |
| q_noise_06 | None | None | None | f_proj_deadline:0.7241 |
| q_noise_07 | None | None | None | f_vietnamese:0.8038 |
| q_noise_08 | None | None | None | f_chay:0.8143 |
| q_noise_09 | None | None | None | f_latex:0.787 |
| q_noise_10 | None | None | None | f_knee_surgery:0.8206 |
| q_noise_11 | None | None | None | f_latex:0.7997 |
| q_noise_12 | None | None | None | f_knee_rehab:0.8282 |
| q_noise_13 | None | None | None | f_morning_exercise:0.8075 |
| q_noise_14 | None | None | None | f_job:0.8144 |
