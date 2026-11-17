"""The scrubber: account names, temporary-account names and addresses leave a summary."""

from __future__ import annotations

import pytest

from editwatch.scrub import (
    REMOVED,
    is_revert_summary,
    is_temp_account,
    scrub_comment,
    summary_for_model,
)

from conftest import ACCOUNTS

# Invented summaries in the shapes the wiki software and editors write, with the exact
# text the desk keeps of each.
SUMMARIES = [
    (
        "Undid revision 123456 by [[Special:Contributions/ExampleEditor42|ExampleEditor42]] "
        "([[User talk:ExampleEditor42|talk]]) not an improvement",
        "Undid revision 123456 by [name]",
    ),
    (
        "Reverted edits by [[Special:Contribs/SampleAccount7|SampleAccount7]] "
        "([[User talk:SampleAccount7|talk]]) to last version by TestWriter2026",
        "Reverted edits by [name] to last version by [name]",
    ),
    (
        "Reverted 1 edit by ExampleEditor42 (talk) to last revision by SampleAccount7",
        "Reverted 1 edit by [name] to last revision by [name]",
    ),
    (
        "Reverted good faith edits by Invented_User_99 (talk): no source given",
        "Reverted good faith edits by [name]: no source given",
    ),
    (
        "Reverting edit(s) by ExampleEditor42 (talk) to rev. 12 by SampleAccount7",
        "Reverting edit(s) by [name] to rev. 12 by [name]",
    ),
    ("Undid revision 99 by ~2026-12345-6 (talk)", "Undid revision 99 by [name]"),
    ("rv edit by 192.0.2.17, unexplained removal", "rv edit by [name], unexplained removal"),
    ("rvv 2001:db8:0:0:0:0:0:1", "rvv [name]"),
    ("thanks @ExampleEditor42 for the pointer", "thanks [name] for the pointer"),
    (
        "copy edit per [[User:SampleAccount7|SampleAccount7]] on the talk page",
        "copy edit per [name] on the talk page",
    ),
    ("see [[User talk:TestWriter2026#Dates|this note]]", "see [name]"),
    ("/* Early life */ fixed a typo", "/* Early life */ fixed a typo"),
    ("Added a source published by Example Press", "Added a source published by Example Press"),
    (
        "fix link to [[Example Bridge]] and [[Example River|the river]]",
        "fix link to [[Example Bridge]] and [[Example River|the river]]",
    ),
    ("   spaced    out   summary  ", "spaced out summary"),
    ("", ""),
]


@pytest.mark.parametrize(("summary", "kept"), SUMMARIES)
def test_a_summary_is_scrubbed_to_exactly_this(summary, kept):
    assert scrub_comment(summary) == kept


def test_no_invented_account_name_survives_any_of_the_summaries():
    scrubbed = " ".join(scrub_comment(summary) for summary, _ in SUMMARIES)
    for name in ACCOUNTS:
        assert name in " ".join(summary for summary, _ in SUMMARIES), "the table must use the name"
        assert name not in scrubbed


def test_scrubbing_twice_changes_nothing_more():
    for _, kept in SUMMARIES:
        assert scrub_comment(kept) == kept


def test_a_missing_summary_is_an_empty_one():
    assert scrub_comment(None) == ""


def test_a_link_to_a_user_page_is_replaced_however_it_is_written():
    links = [
        "[[User:ExampleEditor42]]",
        "[[User:ExampleEditor42|a friendly editor]]",
        "[[user:ExampleEditor42]]",
        "[[ User:ExampleEditor42 ]]",
        "[[User talk:ExampleEditor42]]",
        "[[User_talk:ExampleEditor42|talk]]",
        "[[Special:Contributions/ExampleEditor42|ExampleEditor42]]",
        "[[Special:Contribs/ExampleEditor42]]",
    ]
    for link in links:
        assert scrub_comment(f"as {link} said") == f"as {REMOVED} said"


def test_a_talk_link_in_brackets_is_dropped_with_its_brackets():
    scrubbed = scrub_comment(
        "edit by [[User:SampleAccount7|SampleAccount7]] ([[User talk:SampleAccount7|talk]]) fixed"
    )
    assert scrubbed == "edit by [name] fixed"


def test_a_link_to_an_article_is_left_alone():
    assert scrub_comment("linked [[Example Lighthouse]]") == "linked [[Example Lighthouse]]"


def test_a_temporary_account_name_is_removed():
    for name in ["~2026-12345-6", "~2025-98765", "~2026-1-2-3"]:
        assert scrub_comment(f"same as {name} did before") == f"same as {REMOVED} did before"


@pytest.mark.parametrize(
    "address",
    ["192.0.2.17", "203.0.113.255", "2001:db8:0:0:0:0:0:1", "2001:DB8:85A3:0:0:8A2E:370:7334"],
)
def test_an_ip_address_is_removed(address):
    assert scrub_comment(f"fixing what {address} broke") == f"fixing what {REMOVED} broke"


@pytest.mark.parametrize("address", ["2001:db8::1", "2001:DB8:85A3::8A2E:370:7334"])
def test_a_shortened_ipv6_address_is_removed(address):
    assert address not in scrub_comment(f"fixing what {address} broke")


def test_a_mention_is_removed():
    assert (
        scrub_comment("@ExampleEditor42 please check, cc @Sample.Account-7")
        == "[name] please check, cc [name]"
    )


@pytest.mark.parametrize(
    ("summary", "kept"),
    [
        ("Undid revision 5 by SampleAccount7 (talk)", "Undid revision 5 by [name]"),
        (
            "Restored revision 5 by SampleAccount7: better wording",
            "Restored revision 5 by [name]: better wording",
        ),
        ("rv to last version by SampleAccount7", "rv to last version by [name]"),
        ("Rollback to last revision by SampleAccount7", "Rollback to last revision by [name]"),
    ],
)
def test_the_name_after_by_is_removed_from_a_revert_summary(summary, kept):
    assert scrub_comment(summary) == kept


def test_words_after_by_stay_in_a_summary_that_is_not_a_revert():
    for summary in [
        "Added a book written by Example Author",
        "figures as published by the statistics office",
        "caption supplied by SampleMuseum",
        "updated reservation figures by hand",
    ]:
        assert scrub_comment(summary) == summary


def test_ordinary_words_after_by_stay_in_a_revert_summary():
    for summary in ["Reverted by mistake, sorry", "Undone by the same editor", "rv by request"]:
        assert scrub_comment(summary) == summary


@pytest.mark.parametrize(
    "summary",
    [
        "Restored revision 5 by Placeholder Person (talk): better wording",
        "Reverted edits by ExampleEditor42 (talk) to last version by Placeholder Person",
    ],
)
def test_a_name_of_several_words_is_removed_whole_from_a_revert_summary(summary):
    assert "Person" not in scrub_comment(summary)


@pytest.mark.parametrize(
    "summary",
    [
        "Undid revision 123 by ExampleEditor42",
        "Reverted",
        "Reverting vandalism",
        "rv",
        "RV unsourced",
        "rvv",
        "Restored",
        "/* Reception */ rv, see talk",
    ],
)
def test_a_revert_summary_is_recognised(summary):
    assert is_revert_summary(summary)


@pytest.mark.parametrize(
    "summary",
    [
        "fixed a typo",
        "updated reservation figures",
        "survey results added",
        "restoration of the chapel described",
        "preserved the old wording",
    ],
)
def test_an_ordinary_summary_is_not_a_revert(summary):
    assert not is_revert_summary(summary)


def test_an_empty_or_missing_summary_is_not_a_revert():
    assert not is_revert_summary("")
    assert not is_revert_summary(None)


def test_the_model_sees_nothing_of_a_reverts_summary():
    shown = summary_for_model("Undid revision 9 by SampleAccount7: this claim is nonsense")
    assert shown == "(this edit undoes an earlier edit; its summary is not shown)"
    assert "nonsense" not in shown and "SampleAccount7" not in shown


def test_the_model_sees_an_ordinary_summary_scrubbed():
    assert summary_for_model("/* Early life */ date fixed, thanks @TestWriter2026") == (
        "/* Early life */ date fixed, thanks [name]"
    )


def test_the_summary_shown_to_the_model_is_capped():
    shown = summary_for_model("added " + "many words " * 100)
    assert len(shown) == 300
    assert shown.startswith("added many words")


@pytest.mark.parametrize(
    ("name", "temporary"),
    [
        ("~2026-12345-6", True),
        ("~2025-98765", True),
        ("  ~2026-12345-6  ", True),
        ("ExampleEditor42", False),
        ("Fan~2026-12345", False),
        ("~2026", False),
        ("", False),
        (None, False),
    ],
)
def test_a_temporary_account_is_told_from_its_name(name, temporary):
    assert is_temp_account(name) is temporary


def test_a_section_marker_survives():
    for summary in [
        "/* Early life */",
        "/* Early life */ born in an invented town",
        "/* Early life */ rv to last version by SampleAccount7",
    ]:
        assert scrub_comment(summary).startswith("/* Early life */")
