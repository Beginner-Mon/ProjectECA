"""Grader rules against the labelled answers in fixtures/grader_eval.yaml.

Only tags whose rule has been fixed against the labels are asserted here. The
rest are still measured, not enforced — `python scripts/eval_grader_rules.py`.
Move a tag into FIXED_TAGS in the same change that makes its rule agree with
every label, so it cannot quietly drift back.
"""

from pathlib import Path

import pytest
import yaml

FIXTURE = Path(__file__).parent / "fixtures" / "grader_eval.yaml"
FIXED_TAGS = {"red_flag_screen", "referral_advice", "scope_disclaimer",
              "exercise_protocol", "contraindication", "evidence_citation"}


def _items():
    data = yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))
    for a in data["answers"]:
        if a.get("lang") not in ("vi", "en"):
            continue
        for tag, expect in a["labels"].items():
            yield a["id"], tag, expect, a["text"]
    for c in data["cases"]:
        if c.get("lang") not in ("vi", "en"):
            continue
        yield c["id"], c["tag"], c["expect"], c["text"]


CASES = [pytest.param(tag, expect, text, id=f"{tag}-{item_id}")
         for item_id, tag, expect, text in _items() if tag in FIXED_TAGS]


@pytest.mark.unit
@pytest.mark.parametrize("tag,expect,text", CASES)
def test_rule_agrees_with_label(tag, expect, text):
    from langgraph_agents.nodes.grader import TAG_RULES

    _, rule, _ = TAG_RULES[tag]
    assert rule(text) is expect
