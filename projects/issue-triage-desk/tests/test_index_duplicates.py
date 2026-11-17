"""Search over earlier issues, and the duplicate candidates built from it."""

from __future__ import annotations

import re
from datetime import timedelta

import pytest

from triagedesk.duplicates import (
    DEFAULT_FUSION_K,
    Families,
    find_candidates,
    most_similar,
    similarity_decision,
)
from triagedesk.embed import HashEmbedder
from triagedesk.index import QUERY_BODY_WORDS, IssueIndex, word_query
from triagedesk.models import Candidate, Issue

from conftest import T0, make_issue


def reports() -> list[Issue]:
    """Six invented reports. 4 repeats 1; 6 was opened in the same second as 4."""
    return [
        make_issue(1, "Shader compile crash on startup", "The shader cache is rebuilt and then the editor crashes.", day=1),
        make_issue(2, "Tilemap flickers near the camera edge", "Tiles shimmer while the camera pans.", day=2),
        make_issue(3, "Audio stutters after loading a scene", "The mixer stutters for a second after a scene loads.", day=3),
        make_issue(4, "Shader compile crash when the cache is cleared", "Clearing the shader cache makes the editor crash on the next start.", day=4),
        make_issue(5, "Gamepad input ignored in menus", "Buttons on the gamepad do nothing in menus.", day=5),
        make_issue(6, "Crash in the shader cache folder", "The editor crashes when the shader cache folder is read-only.", day=4),
    ]  # fmt: skip


class CountingEmbedder(HashEmbedder):
    """The test embedder, counting the texts it is given."""

    def __init__(self):
        super().__init__()
        self.texts = 0

    def embed(self, texts):
        self.texts += len(texts)
        return super().embed(texts)


@pytest.fixture
def index(store):
    store.add_issues(reports())
    index = IssueIndex(store, HashEmbedder())
    index.refresh()
    return index


def numbers(found) -> list[int]:
    return [number for number, _ in found]


# -- index ---------------------------------------------------------------------------


def test_word_search_returns_only_issues_opened_strictly_before(index, store):
    assert numbers(index.by_words(store.issue(4), 10)) == [1]
    assert numbers(index.by_words(store.issue(6), 10)) == [1]


def test_vector_search_returns_only_issues_opened_strictly_before(index, store):
    for number in (4, 6):
        found = numbers(index.by_vector(store.issue(number), 10))
        assert found[0] == 1
        assert sorted(found) == [1, 2, 3]


def test_a_stored_issue_is_never_its_own_match_even_when_asked_with_a_later_time(index, store):
    late = store.issue(1).model_copy(update={"created_at": T0 + timedelta(days=30)})
    by_words = numbers(index.by_words(late, 10))
    by_vector = numbers(index.by_vector(late, 10))
    assert 4 in by_words and 1 not in by_words
    assert sorted(by_vector) == [2, 3, 4, 5, 6]


def test_the_oldest_issue_has_nothing_before_it(index, store):
    assert index.by_words(store.issue(1), 10) == []
    assert index.by_vector(store.issue(1), 10) == []


def test_vector_search_is_best_first_and_respects_the_limit(index):
    new = make_issue(99, "Shader compile crash at startup", "The editor crashes.", day=10)
    found = index.by_vector(new, 3)
    scores = [score for _, score in found]
    assert len(found) == 3
    assert scores == sorted(scores, reverse=True)
    assert found[0][0] == 1


def test_embed_missing_embeds_each_issue_once(store):
    store.add_issues(reports())
    embedder = CountingEmbedder()
    index = IssueIndex(store, embedder)
    assert index.embed_missing() == 6
    assert index.embed_missing() == 0
    assert embedder.texts == 6
    store.add_issues([make_issue(7, "Export template missing", "No template is found.", day=7)])
    assert index.refresh() == 1
    assert len(index) == 7


def test_vector_search_works_for_an_issue_that_is_not_in_the_store(index, store):
    twin = reports()[1]
    new = make_issue(99, twin.title, twin.body, day=10)
    number, similarity = index.by_vector(new, 5)[0]
    assert number == 2
    assert similarity == pytest.approx(1.0, abs=1e-5)
    assert store.count() == 6 and len(index) == 6


@pytest.mark.parametrize(
    "title",
    [
        "Shader AND crash OR tilemap NOT audio",
        "NEAR(shader crash, 5)",
        'The "shader" crash, and an open quote: "cache',
        "Crash (shader) in ((cache",
        "text:shader* -crash ^cache",
    ],
)
def test_operators_in_a_title_cannot_break_the_word_search(index, title):
    body = 'It fails AND "then" (crashes) NEAR the* end OR NOT'
    query = word_query(title, body)
    assert re.fullmatch(r'"[^"\s]+"( OR "[^"\s]+")*', query)
    assert '"shader"' in query and '"crash"' in query
    found = numbers(index.by_words(make_issue(99, title, body, day=10), 10))
    assert {1, 4, 6} <= set(found)


def test_a_word_query_is_the_title_words_then_the_first_new_words_of_the_description():
    assert word_query("Shader crash", "The shader cache is rebuilt.") == (
        '"shader" OR "crash" OR "cache" OR "rebuilt"'
    )
    long_body = " ".join(f"detail{n}" for n in range(60))
    assert len(word_query("Shader crash", long_body).split(" OR ")) == 2 + QUERY_BODY_WORDS


def test_an_issue_without_words_gives_an_empty_query_and_no_results(index):
    for title, body in [("", ""), ("The issue is this", "It is what it is.")]:
        assert word_query(title, body) == ""
        assert index.by_words(make_issue(99, title, body, day=10), 5) == []


# -- duplicates ----------------------------------------------------------------------

NEW = make_issue(99, "Shader compile crash at startup", "The editor crashes at once.", day=10)


class FixedLists:
    """Stands in for the index with fixed search results, to check the fusion alone."""

    def __init__(self, by_words=(), by_vector=()):
        self._by_words = list(by_words)
        self._by_vector = list(by_vector)

    def by_words(self, issue, limit):
        return self._by_words[:limit]

    def by_vector(self, issue, limit):
        return self._by_vector[:limit]


def test_a_candidate_found_by_both_searches_outranks_one_found_by_one():
    lists = FixedLists(by_words=[(10, 7.1), (11, 5.0)], by_vector=[(12, 0.91), (11, 0.80)])
    found = find_candidates(lists, NEW)
    assert found[0] == Candidate(
        number=11, score=found[0].score, lexical_rank=2, vector_rank=2, similarity=0.80
    )
    assert found[0].score == pytest.approx(2 / (DEFAULT_FUSION_K + 2))
    assert {c.number for c in found[1:]} == {10, 12}
    assert all(c.score == pytest.approx(1 / (DEFAULT_FUSION_K + 1)) for c in found[1:])


def test_fused_search_puts_the_earlier_reports_of_the_same_problem_first(index):
    found = find_candidates(index, NEW)
    assert sorted(c.number for c in found) == [1, 2, 3, 4, 5, 6]
    by_both = [c.lexical_rank is not None and c.vector_rank is not None for c in found]
    assert by_both == [True, True, True, False, False, False]
    assert {c.number for c in found[:3]} == {1, 4, 6}
    assert [c.score for c in found] == sorted((c.score for c in found), reverse=True)


def test_candidates_respect_the_limit():
    lists = FixedLists(by_words=[(number, 1.0) for number in range(1, 31)])
    assert [c.number for c in find_candidates(lists, NEW, limit=5)] == [1, 2, 3, 4, 5]
    assert len(find_candidates(lists, NEW)) == 20


def test_an_unknown_retrieval_mode_is_refused(index):
    with pytest.raises(ValueError, match="Unknown retrieval mode: hybrid"):
        find_candidates(index, NEW, mode="hybrid")


def test_a_single_search_mode_fills_only_its_own_rank_fields(index):
    by_vector = find_candidates(index, NEW, mode="vector")
    assert [c.vector_rank for c in by_vector] == [1, 2, 3, 4, 5, 6]
    assert all(c.similarity is not None and c.lexical_rank is None for c in by_vector)
    by_words = find_candidates(index, NEW, mode="words")
    assert [c.lexical_rank for c in by_words] == [1, 2, 3]
    assert all(c.vector_rank is None and c.similarity is None for c in by_words)


def test_most_similar_is_the_closest_vector_match_wherever_fusion_placed_it():
    candidates = [
        Candidate(number=1, score=0.03, lexical_rank=1),
        Candidate(number=2, score=0.02, vector_rank=2, similarity=0.61),
        Candidate(number=3, score=0.01, vector_rank=1, similarity=0.94),
    ]
    assert most_similar(candidates).number == 3
    assert most_similar(candidates[:1]) is None
    assert most_similar([]) is None


@pytest.mark.parametrize(
    ("similarity", "expected"),
    [
        (0.97, (7, "high")),
        (0.93, (7, "high")),
        (0.90, (7, "medium")),
        (0.88, (7, "medium")),
        (0.87, (None, None)),
    ],
)
def test_the_similarity_decision_at_and_around_both_thresholds(similarity, expected):
    candidates = [
        Candidate(number=8, score=0.03, lexical_rank=1, vector_rank=2, similarity=0.40),
        Candidate(number=7, score=0.02, vector_rank=1, similarity=similarity),
    ]
    assert similarity_decision(candidates, confident=0.93, propose=0.88) == expected


def test_without_a_vector_match_there_is_no_duplicate_call():
    words_only = [Candidate(number=8, score=0.03, lexical_rank=1)]
    assert similarity_decision([], confident=0.93, propose=0.88) == (None, None)
    assert similarity_decision(words_only, confident=0.93, propose=0.88) == (None, None)


def test_family_links_are_transitive_and_symmetric():
    families = Families()
    families.link(3, 2)
    families.link(2, 1)
    families.link(5, 4)
    assert families.same(3, 1) and families.same(1, 3)
    assert not families.same(5, 1) and not families.same(1, 5)
    families.link(4, 2)
    assert families.same(5, 3) and families.same(3, 5)


def test_an_unknown_number_is_in_no_family_but_its_own():
    families = Families()
    families.link(2, 1)
    assert families.same(8, 8)
    assert not families.same(8, 9)
    assert not families.same(8, 1) and not families.same(1, 8)
    families.link(9, 8)
    assert not families.same(8, 1), "asking about 8 earlier must not have linked it"
