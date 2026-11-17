"""Issue text handling and the label set built from training history."""

from __future__ import annotations

import pytest

from triagedesk.models import Issue, LabelDefinition, LabelInfo, Taxonomy
from triagedesk.taxonomy import (
    EXAMPLES_PER_LABEL,
    build_taxonomy,
    detect_groups,
    load,
    public_form,
    save,
)
from triagedesk.text import (
    CODE_KEEP,
    PASSAGE_CHARS,
    PROMPT_BODY_CHARS,
    SEARCH_CHARS,
    clean,
    matching_passage,
    problem_text,
    prompt_body,
    search_text,
    sections,
    words,
)

from conftest import make_issue

TEMPLATE_BODY = """Opened from the project manager.

### Tested versions
Reproducible in 4.3 stable.

### System information
Linux, integrated graphics, Vulkan.

### Issue description
The editor freezes when a node is renamed.

### Steps to reproduce
Rename any node in the scene tree.

### Minimal reproduction project (MRP)
Not needed.
"""


# -- text ----------------------------------------------------------------------------


def test_clean_removes_html_comments():
    assert clean("The editor <!-- please fill in the template --> freezes.") == (
        "The editor freezes."
    )


@pytest.mark.parametrize(
    "media",
    [
        "![screenshot](https://example.org/shot.png)",
        '<img width="400" src="https://example.org/shot.png">',
        "https://github.com/user-attachments/assets/shot-1b2c3d4e",
    ],
)
def test_clean_removes_images_and_attachment_urls(media):
    cleaned = clean(f"The mesh turns black.\n{media}\nIt worked in the last release.")
    assert cleaned == "The mesh turns black.\n\nIt worked in the last release."


def test_clean_shortens_a_long_code_block_and_keeps_what_follows():
    log = "```\n" + "".join(f"frame {n}: shader cache miss\n" for n in range(100)) + "```"
    cleaned = clean(f"Log of the run:\n{log}\nThe editor closes after that.")
    assert len(cleaned) < CODE_KEEP + 80
    assert "frame 0: shader cache miss" in cleaned
    assert "frame 99" not in cleaned
    assert cleaned.count("```") == 2
    assert cleaned.endswith("The editor closes after that.")


def test_clean_keeps_a_short_code_block_whole():
    snippet = "```\nERROR: shader cache miss\nat frame 12 of the export\n```"
    assert snippet in clean(f"The export prints this:\n{snippet}\nThen it stops.")


def test_sections_split_the_body_at_its_headings():
    assert sections(TEMPLATE_BODY) == {
        "": "Opened from the project manager.",
        "tested versions": "Reproducible in 4.3 stable.",
        "system information": "Linux, integrated graphics, Vulkan.",
        "issue description": "The editor freezes when a node is renamed.",
        "steps to reproduce": "Rename any node in the scene tree.",
        "minimal reproduction project (mrp)": "Not needed.",
    }


def test_problem_text_drops_the_setup_sections():
    problem = problem_text(TEMPLATE_BODY)
    assert problem == (
        "Opened from the project manager.\n"
        "The editor freezes when a node is renamed.\n"
        "Rename any node in the scene tree."
    )


def test_problem_text_is_the_whole_body_when_there_are_no_headings():
    body = "The tilemap flickers.\n\nIt started after the last update."
    assert problem_text(body) == body


def test_words_are_lower_case_and_without_stopwords():
    found = words("The Editor CRASHES when I use a C# script on 4.3-stable")
    assert found == ["editor", "crashes", "c#", "script", "4.3-stable"]


def test_search_text_starts_with_the_title_and_leaves_the_setup_out():
    text = search_text("  Editor freezes on rename ", TEMPLATE_BODY)
    assert text.startswith("Editor freezes on rename\n")
    assert "The editor freezes when a node is renamed." in text
    assert "integrated graphics" not in text
    long_text = search_text("Tilemap flickers", "tile " * 2000)
    assert len(long_text) <= len("Tilemap flickers\n") + SEARCH_CHARS


def test_prompt_body_cuts_a_long_body_and_marks_the_cut():
    assert prompt_body("The mixer pops on resume.") == "The mixer pops on resume."
    body = " ".join(f"word{n}" for n in range(1000))
    cut = prompt_body(body)
    assert cut.endswith("[cut]")
    kept = cut.removesuffix("\n[cut]")
    assert len(kept) == PROMPT_BODY_CHARS
    assert body.startswith(kept)


EARLIER_BODY = (
    "The editor opens without trouble on my laptop. "
    "Compiling a shader ends in a crash with the vulkan renderer. "
    "I have not tried the other renderers yet."
)


def test_matching_passage_is_the_sentence_sharing_the_most_words():
    passage = matching_passage("Crash while compiling a shader on vulkan", EARLIER_BODY)
    assert passage == "Compiling a shader ends in a crash with the vulkan renderer."
    assert passage in EARLIER_BODY


def test_matching_passage_prefers_a_dense_sentence_to_a_long_one_with_the_same_words():
    body = (
        "The shader compile takes a long time and then the whole editor window closes "
        "with a crash dialog on my second monitor. Shader compile crash."
    )
    assert matching_passage("shader compile crash", body) == "Shader compile crash."


def test_matching_passage_never_quotes_a_setup_section():
    body = (
        "### System information\nVulkan shader crash driver 551.\n"
        "### Issue description\nThe shader preview stays grey after a reload."
    )
    passage = matching_passage("vulkan shader crash driver", body)
    assert passage == "The shader preview stays grey after a reload."


def test_matching_passage_falls_back_to_the_title_when_no_sentence_shares_a_word():
    for body in ("", "The mixer pops when playback resumes."):
        passage = matching_passage("shader compile crash", body, title=" Mixer pops on resume ")
        assert passage == "Mixer pops on resume"


def test_matching_passage_respects_the_length_cap():
    body = "The shader cache " + " ".join(f"entry{n}" for n in range(80)) + " is rebuilt."
    passage = matching_passage("shader cache rebuilt", body)
    assert len(passage) <= PASSAGE_CHARS
    assert passage.endswith(" …")
    assert body.startswith(passage.removesuffix(" …"))


def test_matching_passage_respects_the_length_cap_for_text_without_spaces():
    unbroken = "x" * 300
    assert len(matching_passage(unbroken, f"{unbroken}.")) <= PASSAGE_CHARS


# -- taxonomy ------------------------------------------------------------------------


def history() -> list[Issue]:
    """Seventeen invented reports: two areas, one platform, one rare label, two unrelated."""
    issues = [
        make_issue(
            n,
            f"Rendering glitch in pass {n}",
            "The shader shows banding with the vulkan backend.",
            day=n,
            labels=["topic:rendering"],
        )
        for n in range(1, 8)
    ]
    issues += [
        make_issue(
            n,
            f"Audio pops in scene {n}",
            "The mixer drops samples during playback.",
            day=n,
            labels=["topic:audio", "platform:linux"],
        )
        for n in range(8, 15)
    ]
    issues += [
        make_issue(15, "Joint drifts", "A hinge joint drifts apart.", day=15, labels=["topic:physics", "bug"]),
        make_issue(16, "Unsorted report", "Nothing to go on yet.", day=16, labels=["needs:triage", "bug"]),
        make_issue(17, "Shader and mixer fail together", "The shader and the mixer both fail.", day=17, labels=["topic:rendering", "topic:audio"]),
    ]  # fmt: skip
    return issues


@pytest.fixture
def taxonomy(settings):
    infos = [LabelInfo(name="topic:audio", description="Sound and music"), LabelInfo(name="bug")]
    return build_taxonomy(settings, history(), infos)


def definition(taxonomy: Taxonomy, name: str) -> LabelDefinition:
    return next(d for d in taxonomy.labels if d.name == name)


def test_a_label_below_min_issues_is_dropped_and_counted(taxonomy):
    assert "topic:physics" not in taxonomy.names()
    assert taxonomy.dropped == {"topic:physics": 1}
    assert taxonomy.built_from_issues == 17
    assert definition(taxonomy, "topic:rendering").training_issues == 8


def test_only_labels_with_a_configured_prefix_are_considered(taxonomy):
    assert taxonomy.names() == ["topic:audio", "topic:rendering", "platform:linux"]
    assert not {"bug", "needs:triage"} & set(taxonomy.dropped)


def test_terms_are_the_words_that_mark_a_label_and_never_its_own_name(taxonomy):
    rendering = definition(taxonomy, "topic:rendering").terms
    audio = definition(taxonomy, "topic:audio").terms
    assert {"shader", "vulkan", "banding"} <= set(rendering)
    assert {"mixer", "playback"} <= set(audio)
    assert "rendering" not in rendering
    assert "audio" not in audio
    assert not set(rendering) & set(audio)


def test_examples_come_from_issues_with_one_label_of_the_group_and_are_at_most_two(taxonomy):
    titles_with_one_area = {
        issue.title
        for issue in history()
        if sum(label.startswith("topic:") for label in issue.labels) == 1
    }
    for name in ("topic:rendering", "topic:audio"):
        examples = definition(taxonomy, name).examples
        assert len(examples) == EXAMPLES_PER_LABEL
        assert set(examples) <= titles_with_one_area
    assert definition(taxonomy, "topic:rendering").examples == [
        "Rendering glitch in pass 7",
        "Rendering glitch in pass 6",
    ]


def test_repository_descriptions_are_carried_over(taxonomy):
    assert definition(taxonomy, "topic:audio").description == "Sound and music"
    assert definition(taxonomy, "topic:rendering").description == ""


def test_public_form_strips_the_examples_and_leaves_the_rest(taxonomy):
    public = public_form(taxonomy)
    assert all(d.examples == [] for d in public.labels)
    assert public.model_dump(exclude={"labels": {"__all__": {"examples"}}}) == (
        taxonomy.model_dump(exclude={"labels": {"__all__": {"examples"}}})
    )
    assert definition(taxonomy, "topic:audio").examples, "the original keeps its examples"


def test_a_saved_taxonomy_loads_back_unchanged(taxonomy, tmp_path):
    path = tmp_path / "nested" / "taxonomy.json"
    save(taxonomy, path)
    assert load(path) == taxonomy


def test_detect_groups_finds_shared_prefixes_and_ignores_rare_ones():
    names = ["topic:audio", "topic:physics", "topic:rendering", "area/editor", "area/export",
             "area/input", "regression:4.2", "regression:4.3", "bug", "enhancement"]  # fmt: skip
    groups = detect_groups([LabelInfo(name=name) for name in names])
    assert groups == {"topic": "topic:", "area": "area/"}


def test_taxonomy_lookups_by_group_and_name(taxonomy):
    assert taxonomy.names("area") == ["topic:audio", "topic:rendering"]
    assert taxonomy.names("platform") == ["platform:linux"]
    assert taxonomy.group_of("platform:linux") == "platform"
    assert taxonomy.group_of("topic:physics") is None
    labels = ["topic:rendering", "bug", "platform:linux", "topic:physics", "topic:audio"]
    assert taxonomy.keep(labels) == ["platform:linux", "topic:audio", "topic:rendering"]


def test_words_keeps_words_written_in_other_scripts():
    assert words("Şader çöküyor") == ["şader", "çöküyor"]
    assert words("シェーダー crash") == ["シェーダー", "crash"]


def test_detect_groups_does_not_read_hyphenated_words_as_a_group():
    infos = [
        LabelInfo(name=n)
        for n in (
            "needs-info",
            "needs-work",
            "needs-testing",
            "area/gui:text",
            "area/audio",
            "area/core",
        )
    ]
    assert detect_groups(infos) == {"area": "area/"}
