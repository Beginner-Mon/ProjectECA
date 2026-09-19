"""Nothing a Dockerfile COPYs may be excluded by .dockerignore.

The agent image needs `text-to-motion/kimodo/vva_motion` — the Lambda and the
GPU worker have to agree byte-for-byte on the job id and the table schema, and
the Kimodo image's build context (`text-to-motion/kimodo`) cannot reach up to a
shared location, so the module lives there and both Dockerfiles COPY it.

`.dockerignore` excluded `text-to-motion/` wholesale, for a good reason: DART
lives under it and once filled a runner's disk. The COPY line was added and the
ignore file was not, so `docker build` failed with

    failed to compute cache key:
    "/text-to-motion/kimodo/vva_motion": not found

and it failed at the LAST possible moment — after both test gates had gone
green, in the build step itself. No test could see it, because the mismatch is
between two files neither of which is Python.

The check below is deliberately narrow. It does not reimplement Docker's
matcher; it answers one question: for each repo-relative path a Dockerfile
COPYs, does the last matching .dockerignore pattern exclude it?
"""

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# (dockerfile, build context) pairs, matching the workflows that build them:
# .github/workflows/deploy-agent.yml uses `context: .`
# .github/workflows/deploy-speechllm.yml uses `context: SpeechLLm` — its own
# directory, not the repo root (see that Dockerfile's header comment for why
# the context change happened: the repo-root .dockerignore excludes
# `SpeechLLm/` wholesale, written for the agent image build, so every COPY in
# SpeechLLm/Dockerfile failed with "failed to compute cache key: not found"
# when it shared the agent's root context).
DOCKERFILES = [
    (REPO_ROOT / "agenticRAG" / "Dockerfile", REPO_ROOT),
    (REPO_ROOT / "SpeechLLm" / "Dockerfile", REPO_ROOT / "SpeechLLm"),
]


def _patterns(context: Path) -> list[tuple[str, bool]]:
    """(pattern, negated) in file order. Later entries win, as Docker does.

    A dockerignore is resolved at the CONTEXT root, not next to the
    Dockerfile — Docker only ever reads `<context>/.dockerignore`. Each
    dockerfile/context pair here is checked against the ignore file that
    Docker would actually use for that build, not always the repo-root one.
    """
    dockerignore = context / ".dockerignore"
    if not dockerignore.exists():
        return []
    out: list[tuple[str, bool]] = []
    for raw in dockerignore.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        negated = line.startswith("!")
        out.append((line.lstrip("!").rstrip("/"), negated))
    return out


def _is_excluded(path: str, patterns: list[tuple[str, bool]]) -> bool:
    """Last matching pattern wins. A directory pattern matches its children."""
    excluded = False
    for pattern, negated in patterns:
        stem = pattern.rstrip("*").rstrip("/")
        if not stem:
            continue
        if path == stem or path.startswith(stem + "/"):
            excluded = not negated
    return excluded


def _copied_paths(dockerfile: Path) -> list[str]:
    """Context-relative COPY sources. Skips --from= stage copies (they come
    from another image, not the context) and absolute or bare-flag
    arguments."""
    paths: list[str] = []
    for raw in dockerfile.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line.upper().startswith("COPY "):
            continue
        parts = line.split()[1:]
        if any(p.startswith("--from=") for p in parts):
            continue
        for src in parts[:-1]:                      # last token is the destination
            if src.startswith("-") or src.startswith("/"):
                continue
            paths.append(src.rstrip("/"))
    return paths


@pytest.mark.unit
@pytest.mark.parametrize("dockerfile,context", DOCKERFILES, ids=lambda v: getattr(v, "name", ""))
def test_no_copied_path_is_ignored(dockerfile, context):
    patterns = _patterns(context)
    blocked = [
        src for src in _copied_paths(dockerfile)
        if (context / src).exists() and _is_excluded(src, patterns)
    ]
    assert not blocked, (
        f"{dockerfile.relative_to(REPO_ROOT)} (context {context.relative_to(REPO_ROOT) if context != REPO_ROOT else '.'}) COPYs "
        + ", ".join(blocked)
        + " but its context's .dockerignore excludes it, so the build fails with "
        '"failed to compute cache key: not found". Add a `!` negation for the '
        "exact subpath — do not widen the exclusion."
    )
