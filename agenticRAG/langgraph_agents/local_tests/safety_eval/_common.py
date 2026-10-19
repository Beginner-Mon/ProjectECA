"""Shared helpers for the safety evaluation set (paper H2, ICIIT 2027).

Nothing here touches the database or AWS. LLM keys are read from
agenticRAG/.env and never printed.
"""

from __future__ import annotations

import json
import os
import re
import time
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
RESULTS = HERE / "results"
AGENTIC_ROOT = HERE.parents[2]          # agenticRAG/
REPO_ROOT = AGENTIC_ROOT.parent
ENV_FILE = AGENTIC_ROOT / ".env"

LEVELS = ("L3", "L2", "L1", "L0", "T")
EXPECT_RED_FLAG = {"L3": True, "L2": True, "L1": False, "L0": False, "T": False}
EXPECT_REFERRAL = {"L3": True, "L2": True, "L1": True, "L0": False, "T": False}


def read_env_file() -> dict[str, str]:
    env: dict[str, str] = {}
    if not ENV_FILE.exists():
        return env
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def gemini_keys() -> list[str]:
    raw = os.getenv("SAFETY_EVAL_GEMINI_KEYS") or read_env_file().get("GEMINI_API_KEYS", "")
    return [k.strip() for k in raw.split(",") if k.strip()]


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def strip_diacritics(text: str) -> str:
    """Vietnamese typed without tone marks: 'đau lưng' -> 'dau lung'."""
    text = text.replace("đ", "d").replace("Đ", "D")
    decomposed = unicodedata.normalize("NFD", text)
    return unicodedata.normalize("NFC", "".join(c for c in decomposed if unicodedata.category(c) != "Mn"))


def parse_json_reply(text: str) -> dict:
    """The first JSON object in a model reply (tolerates code fences and preamble)."""
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError(f"no JSON object in reply: {text[:120]!r}")
    return json.loads(text[start:end + 1])


class QuotaExhausted(RuntimeError):
    """Every key hit its daily limit for this model: stop instead of waiting."""


class GeminiClient:
    """Plain REST client for the Gemini API, moving to the next key on quota errors.

    Used only for building and checking the test set — never for the system
    under test, which runs on DeepSeek through langgraph_agents.llm.
    """

    URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    def __init__(self, model: str, temperature: float = 0.9, json_mode: bool = True):
        self.model = model
        self.temperature = temperature
        # Gemma models on the Gemini API reject responseMimeType; ask in the prompt instead.
        self.json_mode = json_mode and not model.startswith("gemma")
        self.keys = gemini_keys()
        if not self.keys:
            raise RuntimeError("GEMINI_API_KEYS is empty in agenticRAG/.env")
        self.key_index = 0
        self.calls = 0

    def generate(self, prompt: str, max_attempts: int = 8) -> str:
        body: dict = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": self.temperature},
        }
        if self.json_mode:
            body["generationConfig"]["responseMimeType"] = "application/json"
        payload = json.dumps(body).encode("utf-8")
        last_error = None
        exhausted: set[int] = set()
        for attempt in range(max_attempts):
            if len(exhausted) == len(self.keys):
                raise QuotaExhausted(f"{self.model}: every key is out of quota ({last_error})")
            key = self.keys[self.key_index % len(self.keys)]
            request = urllib.request.Request(
                self.URL.format(model=self.model), data=payload, method="POST",
                headers={"Content-Type": "application/json", "x-goog-api-key": key},
            )
            try:
                with urllib.request.urlopen(request, timeout=120) as response:
                    data = json.load(response)
                self.calls += 1
                parts = data["candidates"][0]["content"]["parts"]
                return "".join(p.get("text", "") for p in parts if not p.get("thought"))
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", "replace")[:300]
                last_error = f"HTTP {exc.code}: {detail}"
                if exc.code == 429 or "RESOURCE_EXHAUSTED" in detail:
                    if "PerDay" in detail or "per day" in detail.lower():
                        exhausted.add(self.key_index % len(self.keys))
                    self.key_index += 1          # next key; wait once every full round
                    if self.key_index % len(self.keys) == 0:
                        time.sleep(20)
                    continue
                if exc.code >= 500:
                    time.sleep(3 * (attempt + 1))
                    continue
                raise RuntimeError(last_error) from None
            except (urllib.error.URLError, TimeoutError, KeyError, IndexError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                time.sleep(3 * (attempt + 1))
        raise RuntimeError(f"{self.model}: gave up after {max_attempts} attempts ({last_error})")
