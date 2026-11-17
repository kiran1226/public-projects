"""The scoreboard's page template: one file, self-contained, and text is written as text."""

from __future__ import annotations

from pathlib import Path

import editwatch

PAGE = Path(editwatch.__file__).with_name("scoreboard.html").read_text(encoding="utf-8")
PLACEHOLDER = "/*SCOREBOARD_DATA*/null"


def test_the_template_holds_the_placeholder_for_the_data_exactly_once():
    assert PAGE.count(PLACEHOLDER) == 1
    assert PAGE.count("SCOREBOARD_DATA") == 1


def test_the_template_loads_nothing_from_the_network():
    for loads in ("http://", "https://", "<script src", "<link"):
        assert loads not in PAGE.lower(), loads


def test_the_template_writes_text_as_text_and_never_as_html():
    assert "textContent" in PAGE
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write"):
        assert sink not in PAGE
