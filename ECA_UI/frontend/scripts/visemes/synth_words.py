"""Synthesize viseme-lab word/sentence samples from a local SpeechLLm server.

Reads a words JSON (see words.en.json), sends each entry to
POST /synthesize/stream, and writes one WAV per entry. Refuses to talk to
anything that is not localhost. Only stdlib + numpy + soundfile are used.
"""

import argparse
import base64
import io
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

import numpy as np
import soundfile as sf

LOCAL_HOSTS = ("localhost", "127.0.0.1")


def fail(msg):
    print(f"synth_words: error: {msg}", file=sys.stderr)
    sys.exit(1)


def check_local(url):
    host = urllib.parse.urlparse(url).hostname
    if host not in LOCAL_HOSTS:
        fail(f"refusing non-local --url {url!r} (host {host!r})")


def http_json(method, url, payload=None, timeout=600):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        url, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read()


def check_health(base_url):
    try:
        status, body = http_json("GET", base_url + "/health", timeout=15)
    except (urllib.error.URLError, OSError) as e:
        fail(f"GET /health failed: {e} (is SpeechLLm running on {base_url}?)")
    if status != 200:
        fail(f"GET /health returned {status}: {body.decode('utf-8', 'replace')}")
    print(f"health: {body.decode('utf-8', 'replace').strip()}")


def synth_one(base_url, text, language, voice_path):
    """Return (sample_rate, mono float32 audio) or None on stream error."""
    payload = {"text": text, "language": language, "voice_path": voice_path}
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        base_url + "/synthesize/stream",
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        resp = urllib.request.urlopen(req, timeout=600)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        if e.code == 422:
            print(f"HTTP 422, stopping entirely: {body}", file=sys.stderr)
            sys.exit(2)
        print(f"HTTP {e.code} for {text!r}: {body}", file=sys.stderr)
        return None
    except (urllib.error.URLError, OSError) as e:
        print(f"request failed for {text!r}: {e}", file=sys.stderr)
        return None

    sample_rate = None
    pieces = []
    with resp:
        while True:
            raw = resp.readline()
            if not raw:
                break
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                print(f"ignoring non-JSON line: {line[:120]}", file=sys.stderr)
                continue
            etype = event.get("type")
            if etype == "start":
                sample_rate = event.get("sample_rate")
            elif etype == "chunk":
                blob = base64.b64decode(event["audio"])
                audio, _ = sf.read(io.BytesIO(blob), dtype="float32")
                if getattr(audio, "ndim", 1) == 2:
                    audio = audio[:, 0]
                pieces.append(audio)
            elif etype == "end":
                break
            elif etype == "error":
                print(f"stream error for {text!r}: {line}", file=sys.stderr)
                return None
            else:
                print(f"ignoring unknown event: {line[:120]}", file=sys.stderr)
    if sample_rate is None or not pieces:
        print(f"no audio received for {text!r}", file=sys.stderr)
        return None
    return sample_rate, np.concatenate(pieces)


def main():
    ap = argparse.ArgumentParser(description="Synthesize viseme-lab samples")
    ap.add_argument("--words", required=True, help="words JSON path")
    ap.add_argument("--out", required=True, help="output directory for WAVs")
    ap.add_argument("--url", default="http://localhost:5000", help="SpeechLLm base URL")
    ap.add_argument("--force", action="store_true", help="re-synthesize existing files")
    args = ap.parse_args()

    base_url = args.url.rstrip("/")
    check_local(base_url)
    check_health(base_url)

    with open(args.words, "r", encoding="utf-8") as f:
        spec = json.load(f)
    language = spec.get("lang", "en")
    voice_path = spec["voice_path"]
    os.makedirs(args.out, exist_ok=True)

    jobs = []
    for item in spec.get("calibration", []):
        word = item["word"]
        jobs.append((f"cal_{word}.wav", f"{word}, {word}, {word}."))
    for item in spec.get("heldout", []):
        word = item["word"]
        jobs.append((f"held_{word}.wav", f"{word}, {word}, {word}."))
    for i, sentence in enumerate(spec.get("sentences", [])):
        jobs.append((f"sent_{i}.wav", sentence))

    failed = False
    for filename, text in jobs:
        path = os.path.join(args.out, filename)
        if os.path.exists(path) and not args.force:
            print(f"skip (exists): {filename}")
            continue
        print(f"synth: {filename} <- {text!r}")
        result = synth_one(base_url, text, language, voice_path)
        if result is None:
            failed = True
            continue
        sample_rate, audio = result
        sf.write(path, audio, sample_rate, format="WAV", subtype="PCM_16")
        print(f"wrote: {filename} ({len(audio) / sample_rate:.2f}s @ {sample_rate}Hz)")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
