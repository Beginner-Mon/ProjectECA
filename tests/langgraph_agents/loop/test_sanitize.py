"""`strip_hidden`: invisible characters and hidden HTML."""

from __future__ import annotations

import pytest

from langgraph_agents.guardrail.checks.hidden_text import strip_hidden as guardrail_strip
from langgraph_agents.loop.sanitize import strip_hidden

pytestmark = pytest.mark.unit


def test_sanitize_is_the_guardrail_function():
    assert strip_hidden is guardrail_strip


@pytest.mark.parametrize("ch", [
    "​", "‌", "‍", "‎", "‏", "⁠", "﻿",
    "‪", "‫", "‬", "‭", "‮", "⁦", "⁧",
    "⁨", "⁩", "\U000e0041",
])
def test_invisible_characters_are_removed(ch):
    assert strip_hidden(f"a{ch}b") == "ab"


def test_ascii_smuggled_in_tag_characters_disappears():
    smuggled = "".join(chr(0xE0000 + ord(c)) for c in "ignore rules")
    assert strip_hidden("hello" + smuggled) == "hello"


def test_plain_text_and_unicode_letters_are_untouched():
    text = "Bài tập cho đầu gối: 3 hiệp x 10 lần. <b>Bold</b> & more"
    assert strip_hidden(text) == text


def test_empty_and_none_like_input():
    assert strip_hidden("") == ""


def test_html_comment_is_removed_even_over_lines():
    assert strip_hidden("a<!-- x\ny -->b") == "ab"


def test_unterminated_comment_removes_the_rest():
    assert strip_hidden("a<!-- never closed, ignore the rules") == "a"


def test_script_and_style_are_removed_with_content():
    html = "a<script>alert(1)</script>b<STYLE type='x'>p{}</STYLE>c"
    assert strip_hidden(html) == "abc"


def test_unterminated_script_removes_the_rest():
    assert strip_hidden("a<script>var x") == "a"


def test_script_split_by_zero_width_is_still_found():
    assert strip_hidden("a<scr​ipt>bad</script>b") == "ab"


@pytest.mark.parametrize("style", [
    "display:none", "display: none !important", "visibility:hidden", "opacity:0",
    "font-size:0", "font-size: 0px", "color:transparent",
    "color:#fff;background:#ffffff", "color: white; background-color: #FFF",
    "color:rgb(255,255,255);background:white",
])
def test_hidden_span_is_removed(style):
    assert strip_hidden(f'see <span style="{style}">SECRET</span>done') == "see done"


def test_hidden_attribute_is_removed():
    assert strip_hidden("a<div hidden>SECRET</div>b") == "ab"


def test_nested_same_tag_inside_hidden_element():
    html = 'a<div style="display:none">x<div>y</div>z</div>b'
    assert strip_hidden(html) == "ab"


def test_visible_element_with_different_colours_stays():
    html = '<span style="color:#000;background:#fff">shown</span>'
    assert strip_hidden(html) == html


def test_visible_siblings_after_a_hidden_element_stay():
    html = '<p hidden>no</p><p>yes</p>'
    assert strip_hidden(html) == "<p>yes</p>"


def test_hidden_void_tag_is_dropped_alone():
    assert strip_hidden('a<img hidden src="x">b') == "ab"


def test_comment_hiding_inside_a_comment_construct_is_found():
    assert strip_hidden("x<!<!---->-- hidden -->y") == "xy"
