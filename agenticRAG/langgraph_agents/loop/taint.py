"""Data-leak rules for the read-only loop.

Rule 1: the turn read a source X with `data_class="personal"` and the call
goes to another destination → `needs_approval` (stage 1 refuses with
`approval_unavailable`).

Rule 2: an `egress_read` argument contains a term from a memory fact or
instruction → refuse, naming the terms
("Your call includes the user's saved details: '…'. Remove them and call
again, or stop."). Terms are word n-grams (n = 1–3) of each fact or
instruction, after one shared normal form (`_normalize`: NFKC, casefold, no format
characters, no diacritics, Cyrillic/Greek lookalikes mapped to Latin, `- _ . /`
as word breaks), with stopwords dropped; single words
need ≥ 3 characters. Terms the user typed in the current message are
allowed.

Rule 3 (non-routine verdict allows internal tools only) is enforced by the
gate, which already holds the verdict.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has",
    "he", "in", "is", "it", "its", "of", "on", "or", "that", "the", "to",
    "was", "were", "will", "with", "you", "your", "i", "me", "my", "we",
    "us", "our", "this", "that", "these", "those", "am", "do", "does",
    "did", "can", "could", "would", "should", "may", "might", "must",
    "shall", "been", "being", "have", "had", "having",
    # Facts are written about "User…", so the word carries no fact of its own.
    "user",
}
_WORD = re.compile(r"\w+", re.UNICODE)


@dataclass
class TaintSnapshot:
    """Refreshed each round by the agent node.

    `banned_terms`: n-grams from facts / instructions (the user's own words
    are subtracted). `allowed_terms`: the user's current-message terms.
    """
    banned_terms: list[str] = field(default_factory=list)
    allowed_terms: list[str] = field(default_factory=list)


# Lookalikes that a rewriter can swap in. Small on purpose: the letters of
# Cyrillic and Greek that print like a Latin letter (and đ, which NFD keeps).
_LOOKALIKES = str.maketrans({
    # Cyrillic
    "а": "a", "в": "b", "е": "e", "ё": "e", "к": "k", "м": "m", "н": "h",
    "о": "o", "р": "p", "с": "c", "т": "t", "у": "y", "х": "x", "і": "i",
    "ї": "i", "ј": "j", "ѕ": "s", "ԁ": "d", "ԛ": "q", "ԝ": "w",
    # Greek
    "α": "a", "β": "b", "γ": "y", "ε": "e", "ι": "i", "κ": "k", "ο": "o",
    "ρ": "p", "τ": "t", "υ": "u", "χ": "x", "ν": "v", "ϲ": "c",
    # Vietnamese đ has no decomposition
    "đ": "d",
})
# Characters that join words in a rewritten term: "knee-osteoarthritis".
_SEPARATORS = str.maketrans({"-": " ", "_": " ", ".": " ", "/": " "})


def _normalize(text: str) -> str:
    """One normal form for facts, instructions, the user's message and the
    call's arguments (GĐ2 review M4): NFKC and casefold, format characters
    (zero-width, soft hyphen, tag characters) dropped, diacritics dropped (so
    Vietnamese with and without them match), common Cyrillic and Greek
    lookalikes mapped to Latin, and `- _ . /` read as word breaks."""
    norm = unicodedata.normalize("NFKC", text or "").casefold()
    norm = "".join(c for c in norm if unicodedata.category(c) != "Cf")
    norm = unicodedata.normalize("NFD", norm)
    norm = "".join(c for c in norm if unicodedata.category(c) != "Mn")
    return norm.translate(_LOOKALIKES).translate(_SEPARATORS)


def _words(text: str) -> list[str]:
    """Tokenise: the normal form above, then Unicode word tokens. Drop
    digit-only tokens and stopwords. Single-word terms need ≥ 3 characters."""
    tokens = _WORD.findall(_normalize(text))
    return [t for t in tokens if t not in _STOPWORDS and not t.isdigit() and len(t) >= 3]


def _ngrams(words: list[str], n: int) -> list[str]:
    return [" ".join(words[i:i + n]) for i in range(max(0, len(words) - n + 1))]


def build_taint(
    facts: list[str],
    instructions: list[str],
    current_message: str,
) -> TaintSnapshot:
    """Build the per-turn taint snapshot.

    Normalises each fact and instruction (NFKC + casefold), tokenises, extracts
    word n-grams n=1–3 (stopwords dropped, single words ≥ 3 chars, digit-only
    tokens dropped), and subtracts the terms the user typed in the current
    message. Matching is whole-token-sequence, never substring.
    """
    banned: set[str] = set()
    for text in (*facts, *instructions):
        for n in (1, 2, 3):
            banned.update(_ngrams(_words(text), n))
    allowed = set()
    for n in (1, 2, 3):
        allowed.update(_ngrams(_words(current_message), n))
    banned -= allowed
    return TaintSnapshot(
        banned_terms=sorted(banned),
        allowed_terms=sorted(allowed),
    )


def find_banned(join_args: str, snapshot: TaintSnapshot) -> list[str]:
    """Return the banned terms that appear as whole token sequences of
    `join_args`.

    Tokenises the arguments (NFKC + casefold) and matches banned n-grams
    against contiguous token windows — never a bare substring match, so
    `"knee?"` does not match `"knee"` and `"osteoarthritis."` does not match
    `"osteoarthritis"`.
    """
    joined_words = _words(join_args or "")
    found = []
    for term in snapshot.banned_terms:
        if not term:
            continue
        term_words = term.split()
        tn = len(term_words)
        for i in range(max(0, len(joined_words) - tn + 1)):
            if " ".join(joined_words[i:i + tn]) == term:
                found.append(term)
                break
    return sorted(set(found))
