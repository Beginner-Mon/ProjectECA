"""Character voices: S3 keys for clone source + static audio manifest.

Revision ID: 011_character_voices
Revises: 010_preferences_into_users
Create Date: 2026-09-13

WHY _key NOT _url
-----------------
voice_vi_key / voice_en_key are S3 KEYS in a PRIVATE bucket with NO
CloudFront behavior — there is no public URL to store. Naming them _url
would lie in the schema and invite someone to paste the value into a browser,
exactly the confusion that once led to accidentally exposing clone source
material. vrm_url is different: it IS a CloudFront URL and is fetchable.

WHY static_audio IS JSONB AND voice_*_key ARE COLUMNS
-------------------------------------------------------
A character has exactly ONE reference file per language (vi/en), so two
columns are cheaper than a JSON lookup and make NULL = "no recording yet"
explicit for the fail-loudly path (VoiceResolutionError). Static clips are
many per character and the set grows (greeting, safety_warning, hold, refusal
...) — a JSONB map means adding a clip type is a new key, not a migration.

WHY NO hash / sample_rate COLUMNS
----------------------------------
encode_reference resamples internally — never pre-process the reference file.
The hash is embedded in the S3 key itself (characters/{slug}/voice/{sha256[:8]}.wav
or similar) and voice_version in the SSE stream is derived from that hash, so no
separate column is needed. _voice_version caches the hash of the resolved file.

WHAT THIS DROPS
---------------
voice_provider / voice_id were dead — nothing read them. voice_language stays:
shared/lang.py uses it as the final language guess, routes_characters returns it
to the frontend. The /chat markdown persona is a second source for the same truth;
cleaning that debt is separate (plan D5 notes it).

STATIC_AUDIO SHAPE (stored as S3 KEYS, not URLs — signed at read time):
{
  "greeting":       {"vi": "characters/anne/audio/9f2c1a4b.ogg", "en": "characters/anne/audio/1b7e08d3.ogg"},
  "safety_warning": {"vi": "characters/anne/audio/4c9d22f1.ogg", ...}
}
"""

from typing import Sequence, Union

from alembic import op

revision: str = "011_character_voices"
down_revision: Union[str, None] = "010_preferences_into_users"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # One statement per op.execute() — asyncpg rejects batched prepared statements
    # (004 never ran for exactly this reason).
    op.execute("ALTER TABLE characters DROP COLUMN IF EXISTS voice_provider")
    op.execute("ALTER TABLE characters DROP COLUMN IF EXISTS voice_id")
    op.execute("ALTER TABLE characters ADD COLUMN IF NOT EXISTS voice_vi_key TEXT")
    op.execute("ALTER TABLE characters ADD COLUMN IF NOT EXISTS voice_en_key TEXT")
    op.execute(
        "ALTER TABLE characters ADD COLUMN IF NOT EXISTS static_audio JSONB NOT NULL DEFAULT '{}'::jsonb"
    )
    op.execute(
        "COMMENT ON COLUMN characters.voice_vi_key IS "
        "'S3 key for Vietnamese reference voice in private bucket (no CloudFront). "
        "NULL = not yet recorded — fail loudly, no preset fallback.'"
    )
    op.execute(
        "COMMENT ON COLUMN characters.voice_en_key IS "
        "'S3 key for English reference voice in private bucket (no CloudFront).'"
    )
    op.execute(
        "COMMENT ON COLUMN characters.static_audio IS "
        "'Map of pre-rendered clips: {kind: {lang: s3_key}}. Keys are S3 keys behind "
        "CloudFront signed URLs (characters/*/audio/*), not URLs — signed at read time.'"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE characters DROP COLUMN IF EXISTS static_audio")
    op.execute("ALTER TABLE characters DROP COLUMN IF EXISTS voice_en_key")
    op.execute("ALTER TABLE characters DROP COLUMN IF EXISTS voice_vi_key")
    op.execute("ALTER TABLE characters ADD COLUMN IF NOT EXISTS voice_id TEXT")
    op.execute("ALTER TABLE characters ADD COLUMN IF NOT EXISTS voice_provider TEXT DEFAULT 'vieneu'")
