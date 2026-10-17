# tier2-direction-V1

30 fact + 77 câu của V0 (`tier2_recall_probes.yaml`); a/b chỉ dùng model embed (0 LLM), c dùng DeepSeek `deepseek-v4-flash` đúng 30 lời gọi.

## a. Hiện tại (top-K, floor)

- top_k=5, floor=0.75
- câu đúng có fact đúng trong prompt: 56/63 (0.8889)
- câu nhiễu kéo fact vào: 12/14 (tổng 60 fact, max 6/câu)
- câu về tên có fact tên trong prompt: 7/10

## b. e5-small so e5-base

| model | dim | recall@1 | @3 | @5 | @30 | @30/430 | noise max | noise p95 | correct min | RAM sau nạp (MB) | load ms | embed p50 ms |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| intfloat/multilingual-e5-small | 384 | 0.746 | 0.8571 | 0.8889 | 1.0 | 0.8095 | 0.8343 | 0.8343 | 0.7737 | 744.5 | 10238 | 10.34 |
| intfloat/multilingual-e5-base | 768 | 0.6984 | 0.8254 | 0.8571 | 1.0 | 0.8413 | 0.8298 | 0.8298 | 0.7517 | 990.4 | 9933 | 29.61 |

Hạng fact tên trong 10 câu về tên:

- e5-small: {'q_name_01': 1, 'q_name_02': 12, 'q_name_03': 5, 'q_name_04': 7, 'q_name_05': 1, 'q_name_06': 1, 'q_name_07': 1, 'q_name_08': 1, 'q_name_09': 1, 'q_name_10': 14}
- e5-base: {'q_name_01': 11, 'q_name_02': 15, 'q_name_03': 16, 'q_name_04': 11, 'q_name_05': 8, 'q_name_06': 7, 'q_name_07': 1, 'q_name_08': 2, 'q_name_09': 3, 'q_name_10': 5}

## c. Planner chọn fact (DeepSeek)

- model: deepseek-v4-flash @ https://api.deepseek.com, calls=30, stopped=None
- recall {'hit': 10, 'total': 10, 'rate': 1.0}, precision trung bình 0.5917
- câu nhiễu có chọn id: 4/5
- needs_retrieval=false trên 10 câu ký ức: base {'count': 0, 'total': 10}, select {'count': 0, 'total': 10}
- planner ms: base {'p50': 1421.5, 'p95': 3046.9, 'n': 15}, select {'p50': 1814.5, 'p95': 3123.2, 'n': 15}
- token: base {'input': 25723, 'output': 2601, 'cache_hit': 23040}, select {'input': 35421, 'output': 3799, 'cache_hit': 32640}
- số dư trước: {'is_available': True, 'balance_infos': [{'currency': 'USD', 'total_balance': '1.70', 'granted_balance': '0.00', 'topped_up_balance': '1.70'}]}
- số dư sau: {'is_available': True, 'balance_infos': [{'currency': 'USD', 'total_balance': '1.69', 'granted_balance': '0.00', 'topped_up_balance': '1.69'}]}

## Bảng tổng hợp

| hướng | tên vào prompt (10) | câu nhiễu kéo fact (14) | recall@30 (430 fact) | planner ms / input token | RAM sau nạp | bỏ được retriever (10) |
|---|---|---|---|---|---|---|
| Hiện tại (top-5, floor 0.75) | 7/10 | 12/14 | 0.8095 | 1421.5 ms / 25723 in | 744.5 MB | 0/10 |
| e5-base thay e5-small | 4/10 | 12/14 | 0.8413 | 0 / 0 | 990.4 | — |
| Planner chọn trong 30 ứng viên | 6/6 | 4/5 | — | +393 ms / +9698 in | 0 (model sẵn có) | 0/10 |
