# SpeechLLm — VieNeu-TTS v3 Turbo on Lambda (container, response_stream)
#
# Built in CI, never locally (ràng buộc cứng #5 — hết RAM/ổ C:, đã có tiền lệ).
# Context = SpeechLLm/, its OWN directory — unlike agenticRAG/Dockerfile,
# which shares the repo root with config/. SpeechLLm has no such sibling
# dependency, so it gets its own build context and SpeechLLm/.dockerignore
# applies as written:
#
#   docker build -f SpeechLLm/Dockerfile -t vva-speechllm SpeechLLm
#
# Four decisions here are load-bearing, và đều fail vào lúc deploy/Cold start
# nếu làm sai — chỉ hiện trong CloudWatch, trên vòng lặp CI 10 phút.
#
# ── 1. Container image, không phải zip ───────────────────────────────────
# weights 522 MB (pnnbao-ump/VieNeu-TTS-v3-Turbo) + codec 86 MB vượt trần
# 250 MB của zip. Không đổi cách tính tiền — zip hay container đều tính
# request + GB-giây.
#
# ── 2. Bake weights vào image, HF_HUB_OFFLINE=1 ───────────────────────────
# configs/models.yaml đang để model_path: "" nghĩa là tải từ HuggingFace lúc chạy
# — được ở local, hỏng ở production vì Lambda ngoài VPC không có đường ra
# internet đảm bảo. Nướng sẵn hai repo vào HF cache của image (pnnbao-ump/
# VieNeu-TTS-v3-Turbo + OpenMOSS-Team/MOSS-Audio-Tokenizer-Nano-ONNX) để runtime
# với HF_HUB_OFFLINE=1 vẫn load được từ cache, không cần network. QUAN TRỌNG:
# HF_HOME phải là một đường dẫn CỐ ĐỊNH, ngoài /root — build chạy như root nên
# default cache (/root/.cache/huggingface/hub) load được lúc build, nhưng
# Lambda KHÔNG chạy container process bằng root, nên /root không đọc được ở
# runtime -> cache miss -> HF_HUB_OFFLINE=1 chặn download -> load fail
# (CloudWatch 19/09: OfflineModeIsEnabled ngay ở bước load model, không phải
# chậm). Note cũ "đã kiểm: load thành công" chỉ verify trên máy local (root
# thật), chưa bao giờ verify dưới runtime user của Lambda — đó là đúng chỗ nó
# fail.
#
# ── 3. Lambda Web Adapter (LWA) là runtime interface ───────────────────────
# Base là python:3.12-slim thường, KHÔNG phải public.ecr.aws/lambda/python.
# Lý do như agenticRAG/Dockerfile: Lambda base ship runtime interface client
# riêng, LWA cũng làm nhiệm vụ đó — hai thứ tranh nhau. Container dùng base
# thường + adapter ở /opt/extensions. Hệ quả: không có boto3 sẵn (image
# agenticRAG phải khai boto3 trong requirements), nhưng SpeechLLm không cần
# boto3 ở runtime (chỉ S3 ở D5d, sẽ thêm khi đó nếu cần).
#
# ── 4. Streaming ───────────────────────────────────────────────────────────
# AWS_LWA_INVOKE_MODE=response_stream + ApiGateway STREAM đã được spike 21/08
# chứng minh (infra/infra/streaming_probe_stack.py): LWA's prelude thỏa
# ResponseTransferMode: STREAM, 11 sự kiện rải 4.953s, byte đầu 2.812s.
# Thiếu biến này API Gateway trả một cục (buffered) và /synthesize/stream
# mất sạch ý nghĩa dù vẫn "chạy".
#
# ─────────────────────────────────────────────────────────────────────────────
# Stage: runtime. Không có torch — v3turbo chạy ONNX/CPU torch-free.
#
# BASE IMAGE: python:3.12-slim thường, như giải thích trên.
# ─────────────────────────────────────────────────────────────────────────────
FROM public.ecr.aws/docker/library/python:3.12-slim

# LWA as an extension. Pinned 0.9.1 — cùng bản VvaCrudApiStack/AgentStack và
# spike đã chứng minh stream. Đổi pin nghĩa bằng chứng stream không còn áp
# dụng cho thứ đang deploy.
COPY --from=public.ecr.aws/awsguru/aws-lambda-adapter:0.9.1 \
     /lambda-adapter /opt/extensions/lambda-adapter

WORKDIR /var/task

# ── System deps ─────────────────────────────────────────────────────────────
# soundfile (libsndfile) để encode FLAC/Opus mỗi chunk; vieneu_utils cần
# soxr/kaldi-native-fbank nhưng chúng đã được pip kéo theo. libsndfile1 là
# thứ duy nhất không có sẵn trên slim và thiếu nó thì _encode_chunk fail lúc
# runtime, không phải lúc build.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

# ── Python deps ─────────────────────────────────────────────────────────────
# Context là SpeechLLm/ nên đường dẫn này là tương đối trong thư mục đó.
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# ── Bake weights vào HF cache ───────────────────────────────────────────────
# Hai repo mà OnnxV3LiteEngine sẽ tải lúc runtime nếu chưa có:
#   pnnbao-ump/VieNeu-TTS-v3-Turbo  (~521 MB, gồm onnx_update/* + denoiser + speaker_encoder)
#   OpenMOSS-Team/MOSS-Audio-Tokenizer-Nano-ONNX (~86 MB, codec)
# HF_HOME set TRƯỚC bước download này, thành một đường dẫn cố định ngoài
# /root (/opt/hf-cache) — không phải vì build cần nó (build chạy root, /root
# vẫn đọc/ghi được), mà vì RUNTIME (Lambda) không chạy bằng root và không đọc
# được /root/.cache. Đặt HF_HOME ở đây để download và runtime lookup trỏ
# CÙNG một chỗ; chmod a+rX bên dưới để UID bất kỳ ở runtime đọc được nó. Không
# đặt HF_HUB_OFFLINE ở bước này: download cần network.
# huggingface_hub đã có transitively qua `vieneu`, nhưng khai rõ để không phụ
# thuộc transitively.
ENV HF_HOME=/opt/hf-cache
RUN pip install --no-cache-dir huggingface_hub \
    && python -c "from huggingface_hub import snapshot_download; \
        snapshot_download(repo_id='pnnbao-ump/VieNeu-TTS-v3-Turbo', max_workers=4); \
        snapshot_download(repo_id='OpenMOSS-Team/MOSS-Audio-Tokenizer-Nano-ONNX', max_workers=4); \
        print('weights baked')" \
    && chmod -R a+rX /opt/hf-cache

# ── App code ─────────────────────────────────────────────────────────────────
# LAYOUT: api_server.py ở WORKDIR để _MODULE_DIR = /var/task và
# configs/models.yaml resolve đúng qua _MODULE_DIR / 'configs' / 'models.yaml'.
# Chỉ copy những gì runtime cần — voices/ đi S3 (D5), data/ là output tạm,
# *.wav/*.ogg/*.mp3 là mẫu thử, __pycache__ không cần. .dockerignore đã loại
# chúng khỏi context, nhưng COPY liệt kê tường minh để không vô tình lọt khi
# .dockerignore bị quên.
COPY api_server.py ./api_server.py
COPY src ./src
COPY configs ./configs

# Bake offline flags sau khi đã tải xong. Nếu để trước bước RUN download,
# download sẽ fail vì offline.
ENV HF_HUB_OFFLINE=1
ENV TRANSFORMERS_OFFLINE=1
# HF_HOME=/opt/hf-cache vẫn giữ nguyên từ bước download ở trên (ENV kế thừa
# xuống runtime) — đây LÀ điểm sửa của lần này. Trước đây không đặt HF_HOME
# riêng, dựa vào mặc định /root/.cache/huggingface: đúng lúc build (root),
# sai lúc chạy trên Lambda (không phải root, /root không đọc được) -> cache
# miss -> OfflineModeIsEnabled. Một đường dẫn cố định, world-readable, dùng
# chung cho cả build và runtime mới đóng được khoảng lệch UID đó.

ENV PYTHONUNBUFFERED=1
ENV PORT=8080

# THE line spike đã test. Default là "buffered"; thiếu nó API Gateway nhận một
# cục và NDJSON mất tính stream dù Lamdba vẫn trả đúng.
ENV AWS_LWA_INVOKE_MODE=response_stream

# LWA probe /health — nhưng /health của service này trả 503 trong ~13-25s
# warm-up (model load + pre-enrol). Dùng ASYNC_INIT để cold start không fail
# trong budget 10s của INIT mà tràn vào invocation đầu, giống agenticRAG.
ENV AWS_LWA_ASYNC_INIT=true
ENV AWS_LWA_READINESS_CHECK_PATH=/health

# EXEC: LWA (ở /opt/extensions) tự khởi như extension; app chạy như server
# thường trên $PORT. Không dùng Mangum/ASGI adapter per-invocation — LWA proxy
# giữ một process/event loop/pool xuyên suốt container, tránh bug pool gắn với
# loop đã đóng sau mỗi invoke.
CMD ["python", "-m", "uvicorn", "api_server:app", "--host", "0.0.0.0", "--port", "8080"]
