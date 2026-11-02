"""Hidden-text strip: the first `tool_result` check.

Text a person cannot see on screen but a model reads is the cheapest way to
smuggle instructions through a tool result. This removes it:

  * zero-width, bidi-control and Unicode "tag" characters;
  * HTML comments;
  * `<script>` and `<style>` elements, content included;
  * elements hidden by `hidden`, `display:none`, `visibility:hidden`,
    `opacity:0`, `font-size:0`, or a text colour equal to the background.

The implementation lives here because `loop/` may import the guardrail but the
guardrail must never import `loop/`; `loop/sanitize.py` re-exports it.

Pure functions, no I/O. It is a mitigation, not a proof: prompt injection is
not fully blockable (plan section 9), this only removes the invisible carrier.
"""

from __future__ import annotations

import re

from langgraph_agents.guardrail.types import CheckContext, Decision

# Zero-width (U+200B..U+200D), LRM/RLM (200E/200F), word joiner and invisible
# operators (2060..2064), BOM/ZWNBSP, soft hyphen, Arabic letter mark, bidi
# embeddings/overrides (202A..202E) and isolates (2066..2069), and the
# "tag" block U+E0000..U+E007F used for ASCII smuggling.
_INVISIBLE_CHARS = re.compile(
    "[​-‏⁠-⁤﻿­؜᠎"
    "‪-‮⁦-⁩\U000e0000-\U000e007f]"
)

_SCRIPT_STYLE = re.compile(
    r"<\s*(script|style)\b[^>]*>.*?(?:<\s*/\s*\1\s*>|\Z)", re.IGNORECASE | re.DOTALL)
_COMMENT = re.compile(r"<!--.*?(?:-->|\Z)", re.DOTALL)
_TAG = re.compile(r"<\s*(/?)\s*([a-zA-Z][a-zA-Z0-9:-]*)((?:[^>\"']|\"[^\"]*\"|'[^']*')*)>")
_ATTR = re.compile(
    r"([a-zA-Z_:][-a-zA-Z0-9_:.]*)(?:\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s\"'>]+)))?")

_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
         "param", "source", "track", "wbr"}

_NAMED = {"white": "#ffffff", "black": "#000000"}


def _norm_colour(value: str) -> str:
    v = re.sub(r"\s+", "", value.lower().replace("!important", ""))
    v = _NAMED.get(v, v)
    m = re.fullmatch(r"#([0-9a-f])([0-9a-f])([0-9a-f])", v)
    if m:
        v = "#" + "".join(c * 2 for c in m.groups())
    m = re.fullmatch(r"rgb\((\d+),(\d+),(\d+)\)", v)
    if m:
        v = "#%02x%02x%02x" % tuple(min(255, int(x)) for x in m.groups())
    return v


def _style_hides(style: str) -> bool:
    props: dict[str, str] = {}
    for part in style.split(";"):
        if ":" not in part:
            continue
        k, _, v = part.partition(":")
        props[k.strip().lower()] = v.strip().lower().replace("!important", "").strip()
    if props.get("display") == "none" or props.get("visibility") == "hidden":
        return True
    if props.get("opacity") in ("0", "0.0", "0%"):
        return True
    if re.fullmatch(r"0+(\.0+)?(px|pt|em|rem|%)?", props.get("font-size", "")):
        return True
    colour = props.get("color")
    background = props.get("background-color") or props.get("background")
    if colour == "transparent":
        return True
    if colour and background and _norm_colour(colour) == _norm_colour(background):
        return True
    return False


def _tag_hides(attrs: str) -> bool:
    for m in _ATTR.finditer(attrs):
        name = m.group(1).lower()
        value = m.group(2) or m.group(3) or m.group(4) or ""
        if name == "hidden":
            return True
        if name == "style" and _style_hides(value):
            return True
    return False


def _drop_hidden_elements(text: str) -> str:
    out: list[str] = []
    pos = 0
    hidden_name: str | None = None
    depth = 0
    for m in _TAG.finditer(text):
        closing, name, attrs = m.group(1) == "/", m.group(2).lower(), m.group(3)
        self_closed = attrs.rstrip().endswith("/")
        if hidden_name is None:
            out.append(text[pos:m.start()])
            if not closing and _tag_hides(attrs):
                if not (name in _VOID or self_closed):
                    hidden_name, depth = name, 1
                # a void or self-closed hidden tag: just the tag is dropped
            else:
                out.append(m.group(0))
        elif name == hidden_name:
            if closing:
                depth -= 1
                if depth == 0:
                    hidden_name = None
            elif not self_closed:
                depth += 1
        pos = m.end()
    if hidden_name is None:
        out.append(text[pos:])
    return "".join(out)


def strip_hidden(text: str) -> str:
    """Return `text` without invisible characters and hidden HTML."""
    if not text:
        return text
    current = text
    # Repeat: removing one construct can expose another (`<!<!---->-- x -->`).
    for _ in range(5):
        before = current
        current = _INVISIBLE_CHARS.sub("", current)
        current = _SCRIPT_STYLE.sub("", current)
        current = _COMMENT.sub("", current)
        if "<" in current:
            current = _drop_hidden_elements(current)
        if current == before:
            break
    return current


class HiddenText:
    """`tool_result` check: the stripped text travels back in `Decision.text`."""

    name = "hidden_text"
    stage = "tool_result"

    async def run(self, ctx: CheckContext) -> Decision:
        cleaned = strip_hidden(ctx.message)
        if cleaned == ctx.message:
            return Decision("allow")
        return Decision(
            "annotate",
            {"hidden_text_removed": len(ctx.message) - len(cleaned)},
            text=cleaned,
        )
