"""Tests for check_pages.py. Standard library only:

python3 scripts/test_check_pages.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_pages as c  # noqa: E402

PAGE = "docs/example.md"  # a page path to resolve links against; it need not exist
GOOD = "../projects/rfq-to-quote/README.md"
MISSING = "../projects/no-such-project/README.md"


def problems(text: str) -> list[str]:
    return c.link_problems(PAGE, text)


def test_good_link_and_anchor_pass():
    assert problems(f"[ok]({GOOD}#5-guardrails)\n") == []


def test_web_link_is_skipped():
    assert problems("[web](https://example.com/page#part)\n") == []


def test_missing_target_is_reported():
    assert "does not exist" in problems(f"[gone]({MISSING})\n")[0]


def test_anchor_without_heading_is_reported():
    assert "no heading matches" in problems(f"[bad]({GOOD}#no-such-heading)\n")[0]


def test_anchor_on_folder_is_reported():
    assert "folder link" in problems("[folder](../projects/rfq-to-quote/#5-guardrails)\n")[0]


def test_wrong_case_is_reported():
    found = problems("[case](../projects/RFQ-to-Quote/readme.md)\n")
    assert len(found) == 1  # "spelled differently" on macOS, "does not exist" on Linux


def test_same_page_anchor_is_checked():
    text = "# Title\n\n[up](#title) [down](#missing)\n"
    assert problems(text) == [f"{PAGE}: no heading matches the anchor: #missing"]


def test_link_with_title_is_checked():
    assert "does not exist" in problems(f'[gone]({MISSING} "a title")\n')[0]


def test_reference_style_link_is_checked():
    assert "does not exist" in problems(f"[gone][ref]\n\n[ref]: {MISSING}\n")[0]


def test_html_link_is_checked():
    assert "does not exist" in problems(f'<a href="{MISSING}">gone</a>\n')[0]


def test_html_image_is_checked():
    assert "does not exist" in problems('<img src="no-such-image.png" width="400">\n')[0]


def test_link_around_an_image_checks_both_targets():
    text = f"[![diagram](../projects/rfq-to-quote/docs/agent-flow.png)]({MISSING})\n"
    assert len(problems(text)) == 1
    text = f"[![diagram](no-such-image.png)]({MISSING})\n"
    assert len(problems(text)) == 2


def test_link_inside_inline_code_is_ignored():
    assert problems(f"Write `[text]({MISSING})` to link.\n") == []


def test_link_inside_a_fence_is_ignored():
    assert problems(f"```\n[gone]({MISSING})\n```\n") == []


def test_comment_inside_a_backtick_fence_is_not_a_heading():
    assert c.anchors_in("```bash\n# not a heading\n```\n\n# Real\n") == {"real"}


def test_comment_inside_a_tilde_fence_is_not_a_heading():
    assert c.anchors_in("~~~bash\n# not a heading\n~~~\n\n# Real\n") == {"real"}


def test_comment_inside_an_indented_fence_is_not_a_heading():
    assert c.anchors_in("- item\n\n  ```bash\n# not a heading\n  ```\n\n# Real\n") == {"real"}


def test_longer_closing_fence_does_not_swallow_what_follows():
    assert c.anchors_in("```\ncode\n`````\n\n# After\n") == {"after"}


def test_slug_follows_github():
    assert c.slug("6. Where the LLM is used and where it is not") == (
        "6-where-the-llm-is-used-and-where-it-is-not"
    )
    assert c.slug("13. Rollout, impact and integration") == "13-rollout-impact-and-integration"
    assert c.slug("Scoring `eval` output") == "scoring-eval-output"


def test_unfinished_project_is_not_required_in_the_table():
    assert c.looks_finished("# Tender radar\n\nBeing built.\n") is False
    done = "# X\n\n**Status.** Built.\n\n## 1. The problem\n\n## 9. Limits and next steps\n"
    assert c.looks_finished(done) is True
    # Every section written but the status paragraph still a placeholder: not finished.
    assert c.looks_finished(done.replace("**Status.** Built.", "<!-- STATUS -->")) is False


def test_project_with_a_handover_file_has_work_outstanding():
    import tempfile

    done = "# X\n\n**Status.** Built.\n\n## 1. The problem\n\n## 9. Limits and next steps\n"
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        assert c.work_outstanding(folder) is True  # no README at all
        (folder / "README.md").write_text(done, encoding="utf-8")
        assert c.work_outstanding(folder) is False
        (folder / "HANDOVER.md").write_text("Record the model run.\n", encoding="utf-8")
        assert c.work_outstanding(folder) is True


if __name__ == "__main__":
    tests = [(name, fn) for name, fn in sorted(globals().items()) if name.startswith("test_")]
    failed = 0
    for name, fn in tests:
        try:
            fn()
        except Exception as error:  # noqa: BLE001 - report every failing test, then exit 1
            failed += 1
            print(f"FAIL {name}: {type(error).__name__}: {error}")
    print(f"{len(tests) - failed} of {len(tests)} tests passed")
    sys.exit(1 if failed else 0)
