from __future__ import annotations

import numpy as np

from tenderradar.awards import buyer_key
from tenderradar.embed import CachedEmbedder, HashEmbedder
from tenderradar.models import Profile
from tenderradar.rank import (
    AlertRanker,
    BuyerRanker,
    CpvRanker,
    EmbeddingRanker,
    FusedRanker,
    LexicalRanker,
    rank,
)

from conftest import make_notice


def notices():
    return [
        make_notice(
            "30-2026",
            "p3",
            **{
                "classification-cpv": ["45000000"],
                "title-proc": {"deu": "Neubau einer Turnhalle"},
                "description-proc": {"deu": "Bauarbeiten"},
                "title-lot": None,
                "description-lot": None,
            },
        ),
        make_notice(
            "10-2026",
            "p1",
            **{
                "classification-cpv": ["38434540"],
                "title-proc": {"deu": "Lieferung eines Spektrometers für das Labor"},
                "description-proc": {"deu": "Massenspektrometer"},
                "title-lot": None,
                "description-lot": None,
            },
        ),
        make_notice(
            "20-2026",
            "p2",
            **{
                "classification-cpv": ["38000000"],
                "title-proc": {"deu": "Laborbedarf allgemein"},
                "description-proc": {"deu": "Verbrauchsmaterial"},
                "title-lot": None,
                "description-lot": None,
            },
        ),
    ]


PROFILE = Profile(
    profile_id="s_test",
    cpv_weights={"38434000": 3.0, "38000000": 1.0},
    texts=["Lieferung von Spektrometern", "Massenspektrometer für das Zentrallabor"],
)


def numbers(scored):
    return [s.publication_number for s in scored]


def test_alert_lists_category_matches_in_publication_order_and_nothing_else():
    narrow = Profile(profile_id="s_narrow", cpv_weights={"38434000": 1.0})
    assert numbers(rank(AlertRanker(depth=5), narrow, notices())) == ["10-2026"]
    # At division depth both laboratory notices match; the alert does not rank them.
    assert numbers(rank(AlertRanker(depth=2), PROFILE, notices())) == ["10-2026", "20-2026"]


def test_cpv_prefers_the_deeper_and_more_frequent_category():
    scored = rank(CpvRanker(), PROFILE, notices())
    assert numbers(scored) == ["10-2026", "20-2026", "30-2026"]
    assert scored[0].score > scored[1].score > scored[2].score == 0


def test_lexical_finds_the_notice_that_shares_words_with_past_wins():
    ranker = LexicalRanker([n.text for n in notices()] + PROFILE.texts)
    assert numbers(rank(ranker, PROFILE, notices()))[0] == "10-2026"


def test_embedding_ranker_with_the_stub_model():
    ranker = EmbeddingRanker(HashEmbedder())
    assert numbers(rank(ranker, PROFILE, notices()))[0] == "10-2026"


def test_fusion_puts_first_what_the_parts_agree_on_and_is_deterministic():
    lexical = LexicalRanker([n.text for n in notices()] + PROFILE.texts)
    fused = FusedRanker([CpvRanker(), lexical, EmbeddingRanker(HashEmbedder())])
    first = rank(fused, PROFILE, notices())
    assert numbers(first)[0] == "10-2026"
    assert first == rank(fused, PROFILE, list(reversed(notices())))


def test_ties_are_broken_by_publication_number():
    empty = Profile(profile_id="s_empty")
    assert numbers(rank(CpvRanker(), empty, notices())) == ["10-2026", "20-2026", "30-2026"]


def test_fusion_ignores_a_ranker_with_nothing_to_say():
    empty = Profile(profile_id="s_empty", texts=["Massenspektrometer"])
    fused = FusedRanker([CpvRanker(), EmbeddingRanker(HashEmbedder())])
    assert numbers(rank(fused, empty, notices()))[0] == "10-2026"


def test_rank_of_no_notices_is_empty():
    assert rank(CpvRanker(), PROFILE, []) == []


def test_hash_embedder_is_unit_length_and_deterministic():
    embedder = HashEmbedder()
    vectors = embedder.embed(["Spektrometer Labor", "Spektrometer Labor", ""])
    assert np.allclose(np.linalg.norm(vectors[:2], axis=1), 1.0)
    assert np.array_equal(vectors[0], vectors[1])
    assert not vectors[2].any()


def test_cached_embedder_embeds_each_text_once(tmp_path):
    class Counting(HashEmbedder):
        calls = 0

        def embed(self, texts):
            Counting.calls += len(texts)
            return super().embed(texts)

    cached = CachedEmbedder(Counting(), tmp_path / "cache.sqlite")
    first = cached.embed(["a b", "c d", "a b"])
    assert Counting.calls == 2 and first.shape == (3, 64)
    again = CachedEmbedder(Counting(), tmp_path / "cache.sqlite")
    second = again.embed(["c d", "a b"])
    assert Counting.calls == 2 and again.misses == 0
    assert np.allclose(second[1], first[0])


def test_buyer_key_ignores_case_accents_and_differs_by_country():
    key = buyer_key("Universitätsklinikum Beispielstadt", "DEU")
    assert key == buyer_key("UNIVERSITATSKLINIKUM  Beispielstadt", "DEU")
    assert key != buyer_key("Universitätsklinikum Beispielstadt", "AUT")
    assert key.startswith("b_") and "beispiel" not in key
    assert buyer_key(None, "DEU") is None and buyer_key("", "DEU") is None


def test_buyer_ranker_scores_only_buyers_the_supplier_has_won_from():
    known = buyer_key("Universitätsklinikum Beispielstadt", "DEU")
    regular = Profile(profile_id="s_reg", buyers={known: 3})
    other = make_notice("50-2026", "p5", **{"buyer-name": {"deu": ["Stadtwerke Anderswo"]}})
    scores = BuyerRanker().scores(regular, [notices()[0], other])
    assert scores[0] > 0 and scores[1] == 0
    assert not BuyerRanker().scores(Profile(profile_id="s_new"), notices()).any()


def test_a_known_buyer_lifts_a_tender_in_the_fused_list():
    known = buyer_key("Klinikum Bekannt", "DEU")
    regular = PROFILE.model_copy(update={"buyers": {known: 2}})
    day = notices()
    day[2] = make_notice(
        "20-2026",
        "p2",
        **{"classification-cpv": ["38000000"], "buyer-name": {"deu": ["Klinikum Bekannt"]}},
    )
    fused = FusedRanker([CpvRanker(), BuyerRanker()])
    assert numbers(rank(fused, PROFILE, day))[0] == "10-2026"
    assert numbers(rank(fused, regular, day))[0] == "20-2026"
