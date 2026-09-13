#!/usr/bin/env python3
"""Upload VRM models to S3 and seed the `characters` table.

For each model in ECA_UI/frontend/src/asset/models/*.vrm:

    1. parse the GLB for humanoid + blendShape metadata
    2. upload to s3://<bucket>/characters/{slug}/{sha256[:8]}.vrm  (models/ prefix, not characters/)
    3. read personas/{slug}.md via the backend's own parser
    4. read the avatar profile via the frontend's own module graph
    5. UPSERT one row into characters

Re-runnable: keyed on slug, and an unchanged file produces the same content
hash and therefore the same S3 key.

    python scripts/upload_characters_to_s3.py --dry-run    # no AWS, no DB
    python scripts/upload_characters_to_s3.py --bucket vva-assets-123456789012 \\
        --cdn https://d111111abcdef8.cloudfront.net

--dry-run needs nothing but Node and the repo, so metadata extraction can be
checked against ECA_UI/frontend/src/avatar/vrmManifest.ts before any
credentials exist.

Requires VVA_PG_DSN for the real run — the same variable the backend and
Alembic read, so there is no way to seed one database while the app reads
another.

D5e — voices + static audio (2026-09):
    Voices (reference .wav for cloning) go to a PRIVATE bucket with NO
    CloudFront behavior. Keys are content-hashed so re-running is idempotent:
        voices/{slug}_{lang}_{sha256[:8]}.wav  (or characters/{slug}/voice/{hash}.wav)
    Stored in characters.voice_vi_key / voice_en_key (S3 keys, not URLs).

    Static audio (greeting, safety_warning, ...) is pre-rendered once per
    character per language by calling SpeechLLm, then uploaded to the ASSET
    bucket at:
        characters/{slug}/audio/{sha256[:8]}.ogg
    Stored in characters.static_audio JSONB as S3 keys, signed at read time
    (characters/*/audio/* via CloudFront trusted key group, same as motions/*).
    Regenerating a clip with unchanged text+voice produces the same hash and
    does not create a new object.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
import struct
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MODELS_DIR = REPO_ROOT / "ECA_UI" / "frontend" / "src" / "asset" / "models"
PROFILE_EXPORTER = REPO_ROOT / "ECA_UI" / "frontend" / "scripts" / "export-avatar-profiles.mjs"
MANIFEST_PATH = Path(__file__).resolve().parent / "characters.seed.json"
VOICES_DIR = REPO_ROOT / "SpeechLLm" / "voices"

sys.path.insert(0, str(REPO_ROOT / "agenticRAG"))

# ── VRM 0.x preset categories ──────────────────────────────────────────────
# Kept identical to ECA_UI/frontend/scripts/extract-vrm-meta.mjs so the numbers
# this script writes to the DB match the manifest the frontend already ships.

EMOTION_PRESETS = {"joy", "angry", "sorrow", "fun", "neutral"}
VISEME_PRESETS = {"a", "i", "u", "e", "o"}
BLINK_PRESETS = {"blink", "blink_l", "blink_r"}
LOOKAT_PRESETS = {"lookup", "lookdown", "lookleft", "lookright"}

# Torso chain used for the retarget-compatibility check. Motion retargeting
# distributes rotation across these; a rig with fewer than three of them cannot
# reproduce a spine curve and produces visibly broken playback.
SPINE_CHAIN = ("hips", "spine", "chest", "upperChest")

GLB_MAGIC = 0x46546C67  # "glTF"

# ── Static audio phrases (D5e) ────────────────────────────────────────────
# Each kind is rendered once per character per language where a reference voice
# exists. Text is intentionally short (2-5s audio) so one NDJSON `chunk` is
# usually the whole clip, but the collector handles multi-chunk streams anyway.
# Uses the character's display_name for greeting personalization.

STATIC_PHRASES: dict[str, dict[str, str]] = {
    "greeting": {
        "vi": "Xin chào! Mình là {name}, rất vui được gặp bạn.",
        "en": "Hello! I'm {name}, nice to meet you.",
    },
    "safety_warning": {
        "vi": "Lưu ý: Đây không phải lời khuyên y tế chuyên môn. Hãy tham khảo bác sĩ trước khi tập.",
        "en": "Note: This is not professional medical advice. Please consult your doctor before exercising.",
    },
}


def classify(preset_name: str | None) -> str:
    if not preset_name or preset_name == "unknown":
        return "custom"
    if preset_name in EMOTION_PRESETS:
        return "emotion"
    if preset_name in VISEME_PRESETS:
        return "viseme"
    if preset_name in BLINK_PRESETS:
        return "blink"
    if preset_name in LOOKAT_PRESETS:
        return "lookAt"
    return "custom"


def read_glb_json(path: Path) -> dict:
    """Return the JSON chunk of a GLB file.

    Same layout the frontend's extract-vrm-meta.mjs walks: a 12-byte header,
    then chunk length at offset 12 and the JSON payload from offset 20.
    """
    data = path.read_bytes()
    if len(data) < 20:
        raise ValueError("file too short to be a GLB")
    magic, _version, _length = struct.unpack_from("<III", data, 0)
    if magic != GLB_MAGIC:
        raise ValueError("not a GLB file (bad magic)")
    json_len, chunk_type = struct.unpack_from("<II", data, 12)
    if chunk_type != 0x4E4F534A:  # "JSON"
        raise ValueError("first GLB chunk is not JSON")
    return json.loads(data[20:20 + json_len].decode("utf-8"))


def extract_vrm_metadata(path: Path) -> dict:
    """Build the characters.vrm_metadata payload for one .vrm file."""
    glb = read_glb_json(path)
    extensions = glb.get("extensions") or {}

    # VRM 0.x lives under "VRM"; VRM 1.0 under "VRMC_vrm". All four current
    # models are 0.x, but reading both keeps a 1.0 model from silently
    # extracting as an empty rig.
    vrm0 = extensions.get("VRM")
    vrm1 = extensions.get("VRMC_vrm")
    ext = vrm0 or vrm1 or {}
    spec_version = ext.get("specVersion") or ("0.0" if vrm0 else "1.0" if vrm1 else "unknown")

    # Joint count: the real skeleton size, i.e. the union of every skin's joint
    # list — not the humanoid bone map, which only covers standard bones and
    # would undercount every model with hair or skirt bones.
    joints: set[int] = set()
    for skin in glb.get("skins") or []:
        joints.update(skin.get("joints") or [])

    if vrm0:
        human_bones = (ext.get("humanoid") or {}).get("humanBones") or []
        bone_names = {b.get("bone") for b in human_bones if b.get("bone")}
        groups = (ext.get("blendShapeMaster") or {}).get("blendShapeGroups") or []
    else:
        human_bones = (ext.get("humanoid") or {}).get("humanBones") or {}
        bone_names = set(human_bones.keys()) if isinstance(human_bones, dict) else set()
        groups = []  # VRM 1.0 uses expressions, not blendShapeGroups

    counts = {"emotions": 0, "visemes": 0, "blinks": 0, "look_ats": 0, "customs": 0}
    key = {
        "emotion": "emotions", "viseme": "visemes",
        "blink": "blinks", "lookAt": "look_ats", "custom": "customs",
    }
    for g in groups:
        counts[key[classify(g.get("presetName"))]] += 1

    spine_count = sum(1 for b in SPINE_CHAIN if b in bone_names)

    reasons: list[str] = []
    if spine_count < 3:
        reasons.append("spine_count < 3")
    if sum(counts.values()) == 0:
        reasons.append("blendshape_groups.total == 0")
    if not bone_names:
        reasons.append("no humanoid rig")

    return {
        "joint_count": len(joints),
        "spine_count": spine_count,
        "has_humanoid_rig": bool(bone_names),
        "blendshape_groups": {"total": sum(counts.values()), **counts},
        "has_blink": counts["blinks"] > 0,
        "has_look_at": counts["look_ats"] > 0,
        "incompatible_reasons": reasons,
        "vrm_version": spec_version,
        "file_size_bytes": path.stat().st_size,
        "extracted_at": datetime.now(timezone.utc).isoformat(),
    }


def load_avatar_profiles(slugs: list[str]) -> dict:
    """Resolve each slug's avatar profile by running the frontend's own loader.

    Shelling out to Node rather than parsing the .ts: bronya.ts spreads
    defaultProfile, so any file-at-a-time parser yields a profile with no
    recipes and no visemes, and the avatar renders expressionless.
    """
    proc = subprocess.run(
        ["node", str(PROFILE_EXPORTER), *slugs],
        capture_output=True, text=True, encoding="utf-8",
        cwd=str(PROFILE_EXPORTER.parent.parent),
    )
    if proc.returncode != 0:
        raise RuntimeError(f"export-avatar-profiles.mjs failed:\n{proc.stderr}")
    return json.loads(proc.stdout)


def load_persona(slug: str) -> dict:
    """Parse personas/{slug}.md with the backend's own loader.

    Importing _load_persona rather than reimplementing it means the JSONB in
    the DB cannot drift from what get_persona() produces at runtime.
    """
    from langgraph_agents.nodes._persona_loader import PersonaError, _load_persona

    try:
        return _load_persona(slug)
    except PersonaError as exc:
        # There is no fallback persona to detect any more — the loader raises.
        # Re-raised as FileNotFoundError so this script's existing error handling
        # and message shape are unchanged for whoever runs it.
        raise FileNotFoundError(
            f"personas/{slug}/ is missing or unparseable ({exc}) — refusing to "
            f"seed character '{slug}' without one"
        )


def display_name_for(slug: str, persona: dict) -> str:
    """Prefer the persona's declared Name, fall back to a title-cased slug."""
    identity = persona.get("identity", "")
    for field in identity.split("|"):
        label, _, value = field.partition(":")
        if label.strip().lower() == "name" and value.strip():
            return value.strip()
    return slug.replace("-", " ").replace("_", " ").title()


def build_records_from_manifest(cdn_base: str) -> list[dict]:
    """Seed from scripts/characters.seed.json when the .vrm files are absent.

    The models were removed from the repo once they lived on the CDN, which left
    this script unable to seed a fresh database — the exact thing it exists for.
    Only two facts actually require the binary: the content hash in the S3 key
    and the extracted vrm_metadata. Both are committed here, so persona (from
    personas/*.md) and avatar_profile (from the frontend modules) still come from
    their real sources and cannot drift.

    Regenerate with --write-manifest after adding or replacing a model.
    """
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    profiles = load_avatar_profiles(sorted(manifest))

    records = []
    for slug, entry in sorted(manifest.items(), key=lambda kv: kv[1].get("sort_order", 0)):
        persona = load_persona(slug)
        key = f"models/{slug}/{entry['s3_digest']}.vrm"
        records.append({
            "slug": slug,
            "display_name": entry.get("display_name") or display_name_for(slug, persona),
            "description": None,
            "local_path": None,
            "s3_key": key,
            "vrm_url": f"{cdn_base.rstrip('/')}/{key}" if cdn_base else key,
            "vrm_metadata": entry["vrm_metadata"],
            "avatar_profile": profiles.get(slug, {}),
            "persona": persona,
            "voice_language": persona.get("voice_identity", {}).get("language", "vi"),
            "sort_order": entry.get("sort_order", 0),
        })
    return records


def build_records(cdn_base: str) -> list[dict]:
    vrm_files = sorted(MODELS_DIR.glob("*.vrm"))
    if not vrm_files:
        if MANIFEST_PATH.exists():
            print(f"No .vrm files in {MODELS_DIR} — seeding from {MANIFEST_PATH.name}")
            return build_records_from_manifest(cdn_base)
        raise SystemExit(
            f"No .vrm files in {MODELS_DIR} and no {MANIFEST_PATH.name} to fall\n"
            f"back on. The models were removed from the repo once they lived on the\n"
            f"CDN, so seeding a fresh database needs one of:\n"
            f"  git checkout <commit-before-removal> -- {MODELS_DIR.relative_to(REPO_ROOT)}\n"
            f"  then re-run with --write-manifest to recreate {MANIFEST_PATH.name}\n"
            f"or copy the characters rows from a database that already has them."
        )

    slugs = [f.stem for f in vrm_files]
    profiles = load_avatar_profiles(slugs)

    records = []
    for order, path in enumerate(vrm_files):
        slug = path.stem
        digest = hashlib.sha256(path.read_bytes()).hexdigest()[:8]
        # `models/`, not `characters/`: CloudFront routes /characters* to the
        # catalog Lambda, so an object at characters/anne/<hash>.vrm would be
        # served by the API instead of S3 — the Lambda sees a third path segment
        # that is not "avatar-profile" and answers 404. Keeping the object prefix
        # out of the API's namespace is what stops the two colliding.
        key = f"models/{slug}/{digest}.vrm"
        persona = load_persona(slug)

        records.append({
            "slug": slug,
            "display_name": display_name_for(slug, persona),
            "description": None,
            "local_path": path,
            "s3_key": key,
            "vrm_url": f"{cdn_base.rstrip('/')}/{key}" if cdn_base else key,
            "vrm_metadata": extract_vrm_metadata(path),
            "avatar_profile": profiles.get(slug, {}),
            "persona": persona,
            "voice_language": persona.get("voice_identity", {}).get("language", "vi"),
            "sort_order": order,
        })
    return records


def upload(records: list[dict], bucket: str) -> None:
    import boto3

    s3 = boto3.client("s3")
    for rec in records:
        if rec["local_path"] is None:
            print(f"  skip upload {rec['s3_key']} (seeded from manifest, no local file)")
            continue
        print(f"  uploading {rec['local_path'].name} -> s3://{bucket}/{rec['s3_key']}")
        s3.upload_file(
            str(rec["local_path"]), bucket, rec["s3_key"],
            ExtraArgs={
                "ContentType": "model/gltf-binary",
                # The key is content-addressed, so the object at this URL can
                # never change. Cache it for a year.
                "CacheControl": "public, max-age=31536000, immutable",
            },
        )


# ── D5e: voices + static audio ─────────────────────────────────────────────

def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:8]


def upload_voices(records: list[dict], voice_bucket: str) -> dict[str, dict[str, str]]:
    """Upload reference voices to the private voice bucket.

    For each character and each language vi/en, looks for
    SpeechLLm/voices/{slug}_{lang}.wav locally. If present, hashes content,
    uploads to s3://voice_bucket/voices/{slug}_{lang}_{hash}.wav (content-
    addressed, re-runnable) and returns a map slug -> {lang: s3_key}.

    Keys are returned even on --dry-run (as would-be keys) so the DB upsert
    can be previewed. Actual S3 upload is skipped on dry-run.
    """
    import boto3

    s3 = boto3.client("s3") if voice_bucket else None
    voice_keys: dict[str, dict[str, str]] = {}

    for rec in records:
        slug = rec["slug"]
        keys: dict[str, str] = {}
        for lang in ("vi", "en"):
            local = VOICES_DIR / f"{slug}_{lang}.wav"
            # Fallback: legacy single file without lang suffix (e.g. anne_vi.mp3)
            # Only .wav is used for cloning; mp3 is ignored.
            if not local.is_file():
                # Try alternative naming: {slug}_{lang}.wav only
                continue
            h = _hash_file(local)
            # Chose voices/{slug}_{lang}_{hash}.wav over characters/... to keep
            # voice keys visually distinct from audio clips (characters/*/audio/*)
            s3_key = f"voices/{slug}_{lang}_{h}.wav"
            keys[lang] = s3_key
            if voice_bucket:
                # Check if already exists (idempotent)
                try:
                    s3.head_object(Bucket=voice_bucket, Key=s3_key)
                    print(f"  voice exists {slug}_{lang} -> s3://{voice_bucket}/{s3_key} (skip)")
                except Exception:
                    print(f"  uploading voice {local.name} -> s3://{voice_bucket}/{s3_key}")
                    s3.upload_file(
                        str(local), voice_bucket, s3_key,
                        ExtraArgs={
                            "ContentType": "audio/wav",
                            "CacheControl": "private, max-age=31536000, immutable",
                        },
                    )
            else:
                print(f"  [dry-run] voice {local.name} -> {s3_key}")
        if keys:
            voice_keys[slug] = keys
        else:
            print(f"  no voice file for {slug} (looked in {VOICES_DIR})")
    return voice_keys


def _collect_speechllm_audio(text: str, voice_path: str, language: str, speechllm_url: str) -> bytes:
    """Call SpeechLLm /synthesize/stream and return concatenated PCM as single OGG bytes.

    SpeechLLm streams NDJSON lines: start, chunk (base64 FLAC), end. Each chunk's
    audio is a complete FLAC file (see vieneu_client._encode_chunk). We decode
    each chunk to PCM, concatenate, and re-encode once to OGG/Opus for the static
    clip. Using httpx streaming keeps first-chunk latency low even here, but the
    clip is only 2-5s so re-encoding is cheap.
    """
    import httpx
    import soundfile as sf

    url = speechllm_url.rstrip("/") + "/synthesize/stream"
    payload = {"text": text, "voice_path": voice_path, "language": language}
    # Use stream to avoid holding 78s for long answers — static phrases are short
    # but we reuse the same path the agent uses.
    pcm_parts = []
    sr = None
    with httpx.Client(timeout=60) as client:
        with client.stream("POST", url, json=payload) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line.strip():
                    continue
                evt = json.loads(line)
                if evt.get("type") == "chunk":
                    audio_b64 = evt.get("audio", "")
                    if not audio_b64:
                        continue
                    audio_bytes = base64.b64decode(audio_b64)
                    # Each chunk is a FLAC file; decode to PCM
                    try:
                        import io as _io
                        data, _sr = sf.read(_io.BytesIO(audio_bytes), dtype="float32")
                        if sr is None:
                            sr = _sr
                        pcm_parts.append(data)
                    except Exception as e:
                        raise RuntimeError(f"failed to decode chunk audio: {e}") from e
                elif evt.get("type") == "error":
                    raise RuntimeError(f"SpeechLLm error: {evt.get('message')}")
                elif evt.get("type") == "end":
                    break
    if not pcm_parts:
        raise RuntimeError(f"no audio chunks for text={text[:30]!r}")
    import numpy as np
    pcm = pcm_parts[0] if len(pcm_parts) == 1 else np.concatenate(pcm_parts)
    # Encode once to OGG/Opus for the static clip (small, browser-playable)
    out = io.BytesIO()
    # Use 48k as SpeechLLm's native rate; sr from first chunk should already be 48000
    out_sr = sr or 48000
    sf.write(out, pcm, out_sr, format="OGG", subtype="OPUS", compression_level=0.5)
    return out.getvalue()


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:8]


def build_static_audio(
    records: list[dict],
    voice_keys: dict[str, dict[str, str]],
    asset_bucket: str | None,
    speechllm_url: str,
    dry_run: bool = False,
) -> dict[str, dict]:
    """Render static clips for each character+language where a voice exists.

    Returns slug -> {kind: {lang: s3_key}} map for DB upsert. Uploads to
    s3://asset_bucket/characters/{slug}/audio/{hash}.ogg when not dry-run.
    """
    import boto3

    s3 = boto3.client("s3") if asset_bucket and not dry_run else None
    static_map: dict[str, dict] = {}

    for rec in records:
        slug = rec["slug"]
        display_name = rec["display_name"]
        langs = voice_keys.get(slug, {})
        if not langs:
            continue
        clip_map: dict[str, dict[str, str]] = {}
        for kind, lang_texts in STATIC_PHRASES.items():
            lang_keys: dict[str, str] = {}
            for lang, template in lang_texts.items():
                voice_key = langs.get(lang)
                if not voice_key:
                    continue
                text = template.format(name=display_name)
                # Derive the voice_path SpeechLLm expects: for S3 voices it is
                # the S3 key itself (D5d), for local dev it would be voices/... but
                # we are in private bucket mode here, so use the S3 key.
                voice_path = voice_key
                if dry_run:
                    # Hash the text as placeholder so key is stable and previewable
                    h = hashlib.sha256(f"{voice_path}:{text}".encode()).hexdigest()[:8]
                    s3_key = f"characters/{slug}/audio/{h}.ogg"
                    lang_keys[lang] = s3_key
                    print(f"  [dry-run] static {slug}/{kind}/{lang} -> {s3_key} text={text[:40]!r}")
                    continue
                # Real render
                print(f"  rendering {slug}/{kind}/{lang} voice={voice_path} text={text[:40]!r} ...", end=" ", flush=True)
                try:
                    audio_bytes = _collect_speechllm_audio(text, voice_path, lang, speechllm_url)
                except Exception as e:
                    print(f"FAILED: {e}")
                    continue
                h = _hash_bytes(audio_bytes)
                s3_key = f"characters/{slug}/audio/{h}.ogg"
                # Check existence
                try:
                    s3.head_object(Bucket=asset_bucket, Key=s3_key)
                    print(f"exists -> s3://{asset_bucket}/{s3_key}")
                except Exception:
                    print(f"uploading -> s3://{asset_bucket}/{s3_key} ({len(audio_bytes)/1024:.1f} KB)")
                    s3.put_object(
                        Bucket=asset_bucket,
                        Key=s3_key,
                        Body=audio_bytes,
                        ContentType="audio/ogg",
                        CacheControl="public, max-age=31536000, immutable",
                    )
                lang_keys[lang] = s3_key
            if lang_keys:
                clip_map[kind] = lang_keys
        if clip_map:
            static_map[slug] = clip_map
    return static_map


async def upsert(records: list[dict], voice_keys: dict[str, dict[str, str]] | None = None, static_audio_map: dict[str, dict] | None = None) -> None:
    from langgraph_agents.shared import get_pg_client

    pg = get_pg_client()
    await pg.connect()
    for rec in records:
        slug = rec["slug"]
        vk = (voice_keys or {}).get(slug, {})
        sa = (static_audio_map or {}).get(slug, {})
        # Merge with any existing static_audio in DB? For re-run, keep old keys
        # not in this batch — but static clip set only grows, so overwrite is fine.
        # If the script is used to ADD a new clip kind, old kinds stay unless we
        # fetch and merge. Simpler: overwrite with what we just built for this slug.
        # If dry-run built nothing, keep empty dict so INSERT doesn't null it.
        await pg.execute(
            """
            INSERT INTO characters (
                slug, display_name, description, vrm_url, vrm_metadata,
                avatar_profile, persona, voice_language, sort_order,
                voice_vi_key, voice_en_key, static_audio
            )
            VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb, $7::jsonb, $8, $9, $10, $11, $12::jsonb)
            ON CONFLICT (slug) DO UPDATE SET
                display_name   = EXCLUDED.display_name,
                vrm_url        = EXCLUDED.vrm_url,
                vrm_metadata   = EXCLUDED.vrm_metadata,
                avatar_profile = EXCLUDED.avatar_profile,
                persona        = EXCLUDED.persona,
                voice_language = EXCLUDED.voice_language,
                sort_order     = EXCLUDED.sort_order,
                voice_vi_key   = COALESCE(EXCLUDED.voice_vi_key, characters.voice_vi_key),
                voice_en_key   = COALESCE(EXCLUDED.voice_en_key, characters.voice_en_key),
                static_audio   = CASE
                    WHEN EXCLUDED.static_audio::text = '{}' THEN characters.static_audio
                    ELSE EXCLUDED.static_audio
                END,
                updated_at     = now()
            """,
            rec["slug"], rec["display_name"], rec["description"], rec["vrm_url"],
            json.dumps(rec["vrm_metadata"]), json.dumps(rec["avatar_profile"]),
            json.dumps(rec["persona"]), rec["voice_language"], rec["sort_order"],
            vk.get("vi"), vk.get("en"), json.dumps(sa),
        )
        extra = ""
        if vk:
            extra += f" voices={vk}"
        if sa:
            extra += f" static_audio={list(sa.keys())}"
        print(f"  upserted {rec['slug']}{extra}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bucket", help="S3 bucket (AssetStack output AssetBucketName)")
    ap.add_argument("--voice-bucket", help="S3 bucket for voices (AssetStack output VoiceBucketName)")
    ap.add_argument("--cdn", default="", help="CloudFront base URL (AssetStack output AssetBaseUrl)")
    ap.add_argument("--speechllm-url", default=os.getenv("VIENEU_TTS_URL", "http://localhost:5000"),
                    help="SpeechLLm URL for static clip rendering (D5e)")
    ap.add_argument("--dry-run", action="store_true", help="extract and print only; no AWS, no DB")
    ap.add_argument("--write-manifest", action="store_true",
                    help="refresh characters.seed.json from the local .vrm files, then exit")
    ap.add_argument("--skip-voices", action="store_true", help="skip voice upload (D5e)")
    ap.add_argument("--skip-audio", action="store_true", help="skip static audio rendering (D5e)")
    args = ap.parse_args()

    if args.write_manifest:
        records = build_records(args.cdn)
        manifest = {
            r["slug"]: {
                "display_name": r["display_name"],
                "s3_digest": r["s3_key"].rsplit("/", 1)[-1].removesuffix(".vrm"),
                "sort_order": r["sort_order"],
                "vrm_metadata": r["vrm_metadata"],
            }
            for r in records
        }
        MANIFEST_PATH.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8")
        print(f"wrote {MANIFEST_PATH} ({len(manifest)} characters)")
        return

    if not args.dry_run and not args.cdn:
        ap.error("--cdn is required unless --dry-run")

    records = build_records(args.cdn)

    print(f"\n{len(records)} character(s) from {MODELS_DIR}\n")
    for rec in records:
        m = rec["vrm_metadata"]
        bs = m["blendshape_groups"]
        warn = " ".join(f"[{r}]" for r in m["incompatible_reasons"]) or "compatible"
        print(f"  {rec['slug']:14s} {rec['display_name']:12s} "
              f"{m['file_size_bytes'] / 1e6:5.1f}MB  vrm={m['vrm_version']}  "
              f"joints={m['joint_count']:3d} spine={m['spine_count']}  "
              f"bs={bs['total']:2d} (E{bs['emotions']} V{bs['visemes']} B{bs['blinks']} "
              f"L{bs['look_ats']} C{bs['customs']})  {warn}")
        print(f"  {'':14s} {rec['vrm_url']}")

    # ── D5e: voices ─────────────────────────────────────────────────────
    voice_keys: dict[str, dict[str, str]] = {}
    if not args.skip_voices:
        print(f"\nVoices from {VOICES_DIR} "
              f"-> {'s3://'+args.voice_bucket if args.voice_bucket else '[dry-run, no bucket]'}")
        # In dry-run without voice_bucket, still compute would-be keys
        vb = args.voice_bucket if not args.dry_run else None
        # If dry-run and no --voice-bucket given, pass None to compute keys without upload
        # upload_voices handles dry-run internally (prints would-be keys)
        # For real dry-run with --voice-bucket we still want to not upload, so force None
        if args.dry_run:
            vb = None
        voice_keys = upload_voices(records, vb)
        if voice_keys:
            print(f"  voice keys: {voice_keys}")
        else:
            print("  (no voice keys)")

    # ── D5e: static audio ───────────────────────────────────────────────
    static_map: dict[str, dict] = {}
    if not args.skip_audio:
        if not args.skip_voices and not voice_keys:
            print("\nSkipping static audio — no voice keys available")
        else:
            print(f"\nStatic audio via {args.speechllm_url} "
                  f"-> {'s3://'+args.bucket if args.bucket else '[dry-run]'}")
            # If dry-run, build_static_audio will hash text rather than call TTS
            sb = args.bucket if not args.dry_run else None
            static_map = build_static_audio(
                records, voice_keys, sb, args.speechllm_url, dry_run=args.dry_run,
            )
            if static_map:
                print(f"  static_audio map: {json.dumps(static_map, ensure_ascii=False)}")
            else:
                print("  (no static audio)")

    if args.dry_run:
        print("\n--dry-run: nothing uploaded, nothing written. Preview:")
        for rec in records:
            slug = rec["slug"]
            vk = voice_keys.get(slug, {})
            sa = static_map.get(slug, {})
            print(f"  {slug}: voice_vi_key={vk.get('vi')} voice_en_key={vk.get('en')} static_audio={sa}")
        return

    print(f"\nUploading VRM to s3://{args.bucket} ...")
    upload(records, args.bucket)

    if not args.skip_voices and voice_keys and args.voice_bucket:
        # upload_voices already uploaded when bucket given; just log
        pass

    # static audio already uploaded in build_static_audio when not dry-run

    print("\nSeeding characters ...")
    asyncio.run(upsert(records, voice_keys, static_map))
    print("\nDone. Voices and static audio are now data, not derived from persona_id.")
    print("Add a new voice: upload + one UPDATE characters SET voice_vi_key=... .")
    print("Add a new clip kind: add key to STATIC_PHRASES and re-run.")


if __name__ == "__main__":
    main()
