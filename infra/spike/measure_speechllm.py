#!/usr/bin/env python3
"""Measure SpeechLLm streaming — D6 gate.

    python infra/spike/measure_speechllm.py \
        --url https://xxx.lambda-url.us-east-1.on.aws/synthesize/stream \
        --text "Xin chào, đây là câu dài để đo..." \
        --voice-key voices/anne_vi_abc12345.wav \
        --out results.json

Or with a file:

    python infra/spike/measure_speechllm.py \
        --url https://xxx.lambda-url.us-east-1.on.aws/synthesize/stream \
        --text-file long_clinical.txt \
        --voice-key characters/anne/voice/a1b2c3d4.wav

Uses SigV4 (botocore) when --url looks like a Lambda Function URL
(*.lambda-url.*.on.aws, *.on.aws), plain HTTP otherwise (localhost).
Records arrival time of each NDJSON line via httpx streaming — a buffered
response would deliver all lines at once (spread <0.5s), a streamed one
spreads across tens of seconds, tracking emit stamps.

D6 gate: if synthesis_time / audio_duration > 1 → SLOWER THAN REALTIME →
STOP, do not enable for users, recalc cost table.

Outputs JSON with per-line arrival, FirstByte, total, chunks, audio duration,
and whether streamed vs buffered.

Requires: httpx, botocore, boto3 (already in agenticRAG image and in
requirements-langgraph.txt). No extra install for Lambda measurement.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path


def _is_lambda_url(url: str) -> bool:
    low = url.lower()
    if "localhost" in low or "127.0.0.1" in low:
        return False
    return "lambda-url" in low or ".on.aws" in low


_LAMBDA_URL_RE = re.compile(r"lambda-url\.([^.]+)\.on\.aws", re.IGNORECASE)


def _extract_region(url: str) -> str:
    m = _LAMBDA_URL_RE.search(url)
    if m:
        return m.group(1)
    return os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1"


def _sign_headers(method: str, url: str, body: bytes) -> dict:
    """Return SigV4 headers for Lambda Function URL, or {} if not needed / no creds."""
    if not _is_lambda_url(url):
        return {}
    try:
        import boto3
        import botocore.auth
        import botocore.awsrequest
        session = boto3.Session()
        creds = session.get_credentials()
        if creds is None:
            import botocore.session
            creds = botocore.session.Session().get_credentials()
            if creds is None:
                return {}
        frozen = creds.get_frozen_credentials() if hasattr(creds, "get_frozen_credentials") else creds
        region = _extract_region(url)
        from urllib.parse import urlparse
        parsed = urlparse(url)
        headers = {"host": parsed.netloc, "content-type": "application/json"}
        req = botocore.awsrequest.AWSRequest(method=method, url=url, data=body, headers=headers)
        botocore.auth.SigV4Auth(frozen, "lambda", region).add_auth(req)
        return dict(req.headers)
    except Exception as e:
        print(f"[warn] SigV4 signing failed ({e}) — sending unsigned", file=sys.stderr)
        return {}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", required=True, help="Function URL + /synthesize/stream")
    ap.add_argument("--text", help="Text to synthesize")
    ap.add_argument("--text-file", help="File containing long clinical text")
    ap.add_argument("--voice-key", required=True, help="S3 key or local voices/... path")
    ap.add_argument("--language", default="vi", help="vi or en")
    ap.add_argument("--out", help="Write JSON results to file")
    ap.add_argument("--timeout-first-byte", type=float, default=45, help="First byte timeout (D3: 45s covers 25s cold start)")
    ap.add_argument("--timeout-gap", type=float, default=30, help="Gap between NDJSON lines (D3: 30s)")
    args = ap.parse_args()

    text = args.text
    if args.text_file:
        text = Path(args.text_file).read_text(encoding="utf-8")
    if not text:
        ap.error("need --text or --text-file")
    text = text.strip()
    if not text:
        ap.error("text is empty")

    payload = {"text": text, "voice_path": args.voice_key, "language": args.language}
    body = json.dumps(payload).encode("utf-8")
    headers = {"content-type": "application/json"}
    sig_headers = _sign_headers("POST", args.url, body)
    headers.update(sig_headers)

    print(f"[measure] POST {args.url}")
    print(f"[measure] voice_key={args.voice_key} language={args.language} text_len={len(text)}")
    print(f"[measure] payload hash {hashlib.sha256(body).hexdigest()[:8]}")
    if sig_headers:
        print(f"[measure] SigV4 signed (region={_extract_region(args.url)}, key_id={sig_headers.get('Authorization','')[:50]}...)")
    else:
        print(f"[measure] unsigned (localhost or no creds)")

    import httpx

    # Read timeout is None — we enforce first_byte vs gap via our own timers
    # Connect timeout is the D3 connect budget (10s)
    timeout = httpx.Timeout(connect=10, read=None, write=10, pool=10)
    started = time.monotonic()
    arrivals = []
    total_audio = 0.0
    chunks = 0
    codec = None
    sample_rate = None
    voice_version = None
    first_byte_at = None

    try:
        with httpx.Client(timeout=timeout) as client:
            with client.stream("POST", args.url, content=body, headers=headers) as resp:
                print(f"[measure] HTTP {resp.status_code} {resp.headers.get('content-type')}")
                resp.raise_for_status()
                # Manual per-line timeout: first line 45s, subsequent 30s
                import asyncio  # not used, but keep for parity with client.py's wait_for logic
                # Use wall clock, not httpx read timeout
                deadline_first = started + args.timeout_first_byte
                deadline_gap = None
                buffer = b""
                # httpx iter_lines would hide the raw arrival timing; use iter_bytes
                # and split on \n while tracking time per line
                for chunk in resp.iter_bytes():
                    if not chunk:
                        continue
                    buffer += chunk
                    while b"\n" in buffer:
                        line, buffer = buffer.split(b"\n", 1)
                        line = line.strip()
                        if not line:
                            continue
                        at = time.monotonic() - started
                        if first_byte_at is None:
                            first_byte_at = at
                            print(f"[measure] first byte at {at:.3f}s")
                            if at > args.timeout_first_byte:
                                print(f"[warn] first byte {at:.1f}s exceeded first_byte budget {args.timeout_first_byte}s", file=sys.stderr)
                        try:
                            evt = json.loads(line)
                        except json.JSONDecodeError as e:
                            print(f"[error] malformed NDJSON: {e} line={line[:200]!r}", file=sys.stderr)
                            return 1
                        arrivals.append({"at": round(at, 3), "event": evt})
                        t = evt.get("type")
                        if t == "start":
                            codec = evt.get("codec")
                            sample_rate = evt.get("sample_rate")
                            voice_version = evt.get("voice_version")
                            print(f"[measure] start at {at:.3f}s codec={codec} sr={sample_rate} vv={voice_version}")
                            deadline_gap = at + args.timeout_gap
                        elif t == "chunk":
                            chunks += 1
                            d = evt.get("duration", 0)
                            total_audio += float(d or 0)
                            seq = evt.get("seq")
                            print(f"[measure] chunk seq={seq} at {at:.3f}s duration={d} total_audio={total_audio:.2f}s")
                            deadline_gap = at + args.timeout_gap
                        elif t in ("end", "error"):
                            print(f"[measure] {t} at {at:.3f}s chunks={evt.get('chunks')} duration={evt.get('duration')} msg={evt.get('message','')[:80]}")
                            # check gap timeout for terminal line too
                            if deadline_gap and at > deadline_gap:
                                print(f"[warn] gap {at - (deadline_gap - args.timeout_gap):.1f}s exceeded gap budget", file=sys.stderr)
                            break
                    # gap timeout check while waiting for next chunk
                    # (if no \n for a long time, the for loop is blocked on iter_bytes)
                    # httpx iter_bytes blocks on read; our read=None means it will block
                    # indefinitely, so we cannot enforce gap here without asyncio.
                    # For this CLI we rely on httpx not timing out and just report
                    # what we got; the gate is synthesis_time vs audio_duration.
                # Handle terminal line if loop exited without seeing end/error
    except httpx.HTTPStatusError as e:
        print(f"[measure] HTTP error {e.response.status_code}: {e.response.text[:500]}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"[measure] failed: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1

    ended_at = time.monotonic() - started
    synthesis_time = ended_at  # wall time from request send to end
    spread = (arrivals[-1]["at"] - arrivals[0]["at"]) if len(arrivals) >= 2 else 0
    streamed = spread >= 0.5
    # D6 gate: synthesis_time / audio_duration
    realtime_ratio = (synthesis_time / total_audio) if total_audio else None
    slower_than_realtime = (realtime_ratio is not None and realtime_ratio > 1.0)

    print()
    print(f"[measure] done in {synthesis_time:.3f}s, {chunks} chunks, {total_audio:.3f}s audio, spread {spread:.3f}s")
    print(f"[measure] streamed={'YES' if streamed else 'NO (BUFFERED — all lines at once)'}")
    if realtime_ratio is not None:
        print(f"[measure] realtime_ratio synthesis/audio = {realtime_ratio:.3f} ({'SLOWER THAN REALTIME — STOP' if slower_than_realtime else 'faster than realtime — OK'})")
    if first_byte_at is not None:
        print(f"[measure] first_byte {first_byte_at:.3f}s (budget {args.timeout_first_byte}s, cold start 25s + model 13s)")

    if slower_than_realtime:
        print()
        print("⛔ D6 GATE: Slower than realtime — DO NOT enable for users. Recalc cost table (x ratio) and consider larger memory (5308 MB) or x86 vs arm64.", file=sys.stderr)

    result = {
        "url": args.url,
        "voice_key": args.voice_key,
        "language": args.language,
        "text_len": len(text),
        "text_hash": hashlib.sha256(text.encode()).hexdigest()[:8],
        "started_at_monotonic": started,
        "first_byte_at": first_byte_at,
        "ended_at": ended_at,
        "synthesis_time": round(synthesis_time, 3),
        "audio_duration": round(total_audio, 3),
        "realtime_ratio": round(realtime_ratio, 3) if realtime_ratio else None,
        "slower_than_realtime": slower_than_realtime,
        "chunks": chunks,
        "codec": codec,
        "sample_rate": sample_rate,
        "voice_version": voice_version,
        "arrivals": arrivals,
        "spread": round(spread, 3),
        "streamed": streamed,
    }
    if args.out:
        Path(args.out).write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"[measure] wrote {args.out}")

    # Exit codes: 0 streamed+fast, 1 buffered or slower-than-realtime, 2 error
    if not streamed:
        print("[measure] BUFFERED — check AWS_LWA_INVOKE_MODE=response_stream and ResponseTransferMode STREAM", file=sys.stderr)
        return 1
    if slower_than_realtime:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
