"""The matcher scored on outside data. The rows below are written for these tests in the
format of the Billiger.de Products pair files; they are not taken from that dataset."""

from __future__ import annotations

import gzip
import hashlib
import json

import pytest
from typer.testing import CliRunner

from rfq2quote import outside
from rfq2quote.cli import app
from rfq2quote.llm import CallStats, LLMError
from rfq2quote.match import LLMMatcher
from rfq2quote.models import MatchDecision, Usage

DRILL = (10, "Bosch", "Bosch Akku-Bohrschrauber GSR 18V-55")
SAME_DRILL = (20, "BOSCH", "Bosch Professional GSR 18V-55 Akku-Bohrschrauber")
SMALLER_DRILL = (21, "Bosch", "Bosch Akku-Bohrschrauber GSR 12V-15")
TYRE = (11, None, "Winterreifen 205/55 R16 91H")
OTHER_TYRE = (22, "Goodyear", "Goodyear UltraGrip 205/55 R16 91H")
JACKET = (12, "VAUDE", "Air Jacket III grün S")
PUMP = (23, "Gardena", "Tauchpumpe 16000")


def pair(left, right, label, *, same_product=False):
    (lid, lbrand, lname), (rid, rbrand, rname) = left, right
    return {
        "id_left": lid, "brand_left": lbrand, "name_left": lname,
        "desc_left": "left description that is not sent", "price_left": 9.5,
        "product_id_left": 700,
        "id_right": rid, "brand_right": rbrand, "name_right": rname,
        "desc_right": "right description that is not sent", "price_right": 9.9,
        "product_id_right": 700 if same_product else 701,
        "pair_id": f"{lid}#{rid}", "label": label, "is_hard_negative": not label,
    }  # fmt: skip


ROWS = [
    pair(DRILL, SAME_DRILL, 1, same_product=True),
    pair(DRILL, SMALLER_DRILL, 0),
    # Same product id, but the dataset's audit says these are not the same product.
    pair(TYRE, OTHER_TYRE, 0, same_product=True),
    pair(JACKET, PUMP, 0),
]


def write_pairs(path, rows=ROWS):
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return path


@pytest.fixture
def pairs_file(tmp_path):
    return write_pairs(tmp_path / "pairs.json.gz")


@pytest.fixture
def queries(pairs_file):
    return outside.load_queries(pairs_file)


def decision(sku, confidence="high"):
    return MatchDecision(sku=sku, confidence=confidence, reason="a sentence naming the product")


def llm(scripted, *answers):
    provider = scripted({MatchDecision: list(answers)})
    return provider, lambda catalog: LLMMatcher(catalog, provider)


# --- loading -----------------------------------------------------------------


def test_pairs_become_one_query_per_left_offer_in_file_order(queries):
    assert [q.offer.id for q in queries] == ["10", "11", "12"]
    assert [c.id for c in queries[0].candidates] == ["20", "21"]
    assert queries[0].gold == {"20"}


def test_gold_comes_from_the_audited_label_not_from_the_product_id(queries):
    assert queries[1].gold == frozenset()


@pytest.mark.parametrize(
    "brand, name, title",
    [
        ("VAUDE", "Air Jacket III grün S", "VAUDE Air Jacket III grün S"),
        ("BOSCH", "Bosch Professional GSR 18V-55", "Bosch Professional GSR 18V-55"),
        ("", "Winterreifen 205/55 R16 91H", "Winterreifen 205/55 R16 91H"),
    ],
)
def test_title_adds_the_brand_only_when_the_name_lacks_it(brand, name, title):
    assert outside.Offer(id="1", brand=brand, name=name).title == title


# --- one query ---------------------------------------------------------------


def test_picking_the_labelled_offer_is_correct(queries, scripted):
    _, make = llm(scripted, decision("20"))
    outcome = outside.run_query(queries[0], make)
    assert (outcome.kind, outcome.confidence, outcome.n_gold) == ("correct", "high", 1)


def test_picking_another_offer_is_wrong(queries, scripted):
    _, make = llm(scripted, decision("21", "medium"))
    outcome = outside.run_query(queries[0], make)
    assert (outcome.kind, outcome.confidence) == ("wrong", "medium")


def test_declining_when_a_match_was_shown_is_a_miss(queries, scripted):
    _, make = llm(scripted, decision(None, "low"))
    outcome = outside.run_query(queries[0], make)
    assert (outcome.kind, outcome.gold_shown) == ("missed", True)


def test_declining_when_no_candidate_matches_is_a_correct_decline(queries, scripted):
    _, make = llm(scripted, decision(None, "low"))
    assert outside.run_query(queries[1], make).kind == "correct_decline"


def test_picking_when_no_candidate_matches_is_wrong(queries, scripted):
    _, make = llm(scripted, decision("22"))
    assert outside.run_query(queries[1], make).kind == "wrong"


def test_nothing_retrieved_means_no_model_call(queries, scripted):
    provider, make = llm(scripted)
    outcome = outside.run_query(queries[2], make)
    assert outcome.kind == "correct_decline"
    assert provider.calls == [] and outcome.usage.llm_calls == 0


def test_the_model_sees_titles_only(queries, scripted):
    provider, make = llm(scripted, decision("20"))
    outside.run_query(queries[0], make)
    _, user = provider.calls[0]
    assert "Bosch Akku-Bohrschrauber GSR 18V-55" in user
    assert "Bosch Professional GSR 18V-55 Akku-Bohrschrauber" in user
    assert "description that is not sent" not in user and "9.9" not in user


def test_a_failed_model_call_is_no_pick_and_is_counted(queries, scripted):
    _, make = llm(scripted, LLMError("rate limited"))
    outcome = outside.run_query(queries[0], make)
    assert (outcome.kind, outcome.failed) == ("missed", True)


def test_a_wrong_pick_whose_title_equals_the_query_is_flagged(scripted):
    query = outside.Query(
        outside.Offer("1", "Melon", "Melon Helm Milan grün 46-52"),
        (outside.Offer("2", "MELON", "Melon Helm »Milan«, grün 46-52"),),
        frozenset(),
    )
    _, make = llm(scripted, decision("2"))
    outcome = outside.run_query(query, make)
    assert (outcome.kind, outcome.same_title) == ("wrong", True)


def test_a_wrong_pick_with_a_different_title_is_not_flagged(queries, scripted):
    _, make = llm(scripted, decision("21"))
    assert outside.run_query(queries[0], make).same_title is False


def test_confident_wrong_picks_with_the_same_title_are_counted():
    same = outside.Outcome(
        query_id="q", kind="wrong", confidence="high", n_gold=0, gold_shown=False,
        failed=False, usage=Usage(), same_title=True,
    )  # fmt: skip
    scores = outside.score([same, outcome("wrong", "high"), outcome("wrong", "low")])
    assert (scores["wrong_high"], scores["wrong_high_same_title"]) == (2, 1)


def test_calls_needed_counts_the_queries_that_reach_the_model(queries):
    assert outside.calls_needed(queries) == 2


# --- scoring -----------------------------------------------------------------


def outcome(kind, confidence="low", n_gold=0, gold_shown=False, failed=False):
    return outside.Outcome(
        query_id="q", kind=kind, confidence=confidence, n_gold=n_gold,
        gold_shown=gold_shown, failed=failed, usage=Usage(),
    )  # fmt: skip


def test_scores_are_counted_over_the_labelled_pairs():
    scores = outside.score(
        [
            outcome("correct", "high", n_gold=1, gold_shown=True),
            outcome("correct", "medium", n_gold=2, gold_shown=True),
            outcome("wrong", "high"),
            outcome("wrong", "low", n_gold=1, gold_shown=True),
            outcome("missed", n_gold=1),
            outcome("correct_decline"),
        ]
    )
    assert (scores["queries"], scores["with_match"], scores["matches"]) == (6, 4, 5)
    assert (scores["correct"], scores["wrong"], scores["missed"]) == (2, 2, 1)
    assert (scores["correct_declines"], scores["without_match"]) == (1, 2)
    assert scores["missed_not_shown"] == 1
    assert scores["precision"] == pytest.approx(2 / 4)
    assert scores["recall"] == pytest.approx(2 / 5)
    assert scores["f1"] == pytest.approx(2 * 0.5 * 0.4 / 0.9)


def test_high_confidence_picks_are_scored_on_their_own():
    scores = outside.score(
        [
            outcome("correct", "high", n_gold=1, gold_shown=True),
            outcome("correct", "medium", n_gold=1, gold_shown=True),
            outcome("wrong", "high"),
            outcome("wrong", "low"),
        ]
    )
    assert (scores["correct_high"], scores["wrong_high"]) == (1, 1)
    assert scores["precision_high"] == pytest.approx(1 / 2)
    assert scores["recall_high"] == pytest.approx(1 / 2)


def test_a_matcher_that_never_picks_scores_zero_not_an_error():
    scores = outside.score([outcome("missed", n_gold=1), outcome("correct_decline")])
    assert (scores["precision"], scores["recall"], scores["f1"]) == (0.0, 0.0, 0.0)


# --- a whole run -------------------------------------------------------------


class Declines:
    """A live provider that always declines, and counts what it was asked."""

    model, effort, max_tokens = "claude-haiku-5-5", "medium", 16000

    def __init__(self):
        self.calls = 0

    def structured(self, *, system, user, schema):
        self.calls += 1
        return decision(None, "low"), CallStats(self.model, 1000, 100, 0.2)


RUN = dict(model="claude-haiku-5-5", effort="medium", concurrency=2)


def test_without_a_recording_the_model_is_reported_as_not_run(queries, tmp_path):
    results = outside.run_outside(queries, cassette_dir=tmp_path, **RUN)
    assert results["top-hit"]["queries"] == 3
    assert isinstance(results["llm-match"], str) and "--live" in results["llm-match"]


def test_a_recorded_run_replays_without_a_live_provider_and_keeps_no_free_text(queries, tmp_path):
    live = Declines()
    first = outside.run_outside(
        queries, cassette_dir=tmp_path, live=True, record=True, live_provider=live, **RUN
    )
    assert live.calls == 2  # the third query retrieves nothing

    replayed = outside.run_outside(queries, cassette_dir=tmp_path, **RUN)
    assert replayed["llm-match"]["correct_declines"] == first["llm-match"]["correct_declines"] == 2
    saved = (tmp_path / "outside-claude-haiku-5-5.json").read_text(encoding="utf-8")
    assert "a sentence naming the product" not in saved


def test_the_spend_cap_stops_the_run(queries, tmp_path):
    live = Declines()
    one_call = CallStats(live.model, 1000, 100, 0.2).cost_usd
    with pytest.raises(outside.SpendCapReached):
        outside.run_outside(
            queries, cassette_dir=tmp_path, live=True, record=True, live_provider=live,
            max_usd=one_call, model=live.model, effort="medium", concurrency=1,
        )  # fmt: skip
    assert live.calls == 1


# --- several recorded models -------------------------------------------------


class Picks:
    """A live provider for one model that picks the given offer for the first query."""

    effort, max_tokens = "medium", 16000

    def __init__(self, model, first):
        self.model, self.first = model, first

    def structured(self, *, system, user, schema):
        answer = decision(self.first) if DRILL[2] in user.split("Candidates")[0] else decision(None)
        return answer, CallStats(self.model, 1000, 100, 0.2)


def record(queries, cassette_dir, model, first):
    live = Picks(model, first)
    outside.run_outside(
        queries, cassette_dir=cassette_dir, live=True, record=True, live_provider=live,
        model=model, effort="medium", concurrency=1,
    )  # fmt: skip


@pytest.fixture
def two_recordings(queries, tmp_path):
    record(queries, tmp_path, "claude-haiku-5-5", "21")  # the smaller drill: wrong
    record(queries, tmp_path, "claude-opus-5-5", "20")  # the same drill: right
    return tmp_path


def test_recorded_models_are_listed_cheapest_first(two_recordings):
    (two_recordings / "claude-haiku-5-5.json").write_text("{}")  # not an outside recording
    assert outside.recorded_models(two_recordings) == ["claude-haiku-5-5", "claude-opus-5-5"]


def test_models_are_compared_on_the_same_queries_from_their_recordings(queries, two_recordings):
    comparison = outside.compare_models(
        queries, cassette_dir=two_recordings, against="claude-haiku-5-5", effort="medium"
    )
    haiku, opus = comparison["models"]["claude-haiku-5-5"], comparison["models"]["claude-opus-5-5"]
    assert (haiku["correct"], haiku["wrong"], haiku["wrong_high"]) == (0, 1, 1)
    assert (opus["correct"], opus["wrong"], opus["correct_high"]) == (1, 0, 1)
    assert comparison["against"] == "claude-haiku-5-5"
    assert comparison["paired"] == {
        "claude-opus-5-5": {"only_this_right": 1, "only_other_right": 0, "p": 1.0}
    }


def test_a_model_whose_recording_does_not_cover_the_queries_is_left_out(queries, two_recordings):
    (two_recordings / "outside-claude-sonnet-5-5.json").write_text("{}")
    comparison = outside.compare_models(
        queries, cassette_dir=two_recordings, against="claude-haiku-5-5", effort="medium"
    )
    assert list(comparison["models"]) == ["claude-haiku-5-5", "claude-opus-5-5"]


@pytest.mark.parametrize(
    "first, second, p",
    [(44, 30, 0.130), (60, 32, 0.0046), (0, 0, 1.0), (5, 5, 1.0), (6, 0, 0.03125)],
)
def test_exact_mcnemar_test_on_the_queries_two_models_disagree_on(first, second, p):
    assert outside.mcnemar_exact(first, second) == pytest.approx(p, rel=0.02)


def test_report_compares_the_recorded_models(queries, two_recordings):
    results = outside.run_outside(queries, cassette_dir=two_recordings, **RUN)
    comparison = outside.compare_models(
        queries, cassette_dir=two_recordings, against="claude-haiku-5-5", effort="medium"
    )
    report = outside.render_report(
        results, queries, total_queries=3, comparison=comparison, **RUN_LABELS
    )
    assert "## The same queries with each recorded model" in report
    assert "| | `claude-haiku-5-5` | `claude-opus-5-5` |" in report
    assert "| Picks that are right (precision) | 0 of 1 (0.0%) | 1 of 1 (100.0%) |" in report
    assert "| Matches found (recall) | 0 of 1 (0.0%) | 1 of 1 (100.0%) |" in report
    assert "| High-confidence picks that are wrong | 1 of 1 (100.0%) | 0 of 1 (0.0%) |" in report
    assert "From `claude-haiku-5-5` to `claude-opus-5-5`, recall goes from 0.0% to 100.0%" in report
    assert "wrong from 100.0% to 0.0%" in report
    assert "| `claude-opus-5-5` | 1 | 0 | 1 | could be chance |" in report
    assert "exact McNemar test" in report
    assert "**One run per model.**" in report and "One run of one model" not in report


def test_report_says_when_a_lead_is_unlikely_to_be_chance(queries, two_recordings):
    comparison = outside.compare_models(
        queries, cassette_dir=two_recordings, against="claude-haiku-5-5", effort="medium"
    )
    comparison["paired"]["claude-opus-5-5"] = {
        "only_this_right": 60, "only_other_right": 32, "p": outside.mcnemar_exact(60, 32),
    }  # fmt: skip
    results = outside.run_outside(queries, cassette_dir=two_recordings, **RUN)
    report = outside.render_report(
        results, queries, total_queries=3, comparison=comparison, **RUN_LABELS
    )
    assert "| `claude-opus-5-5` | 60 | 32 | 0.0046 | unlikely to be chance alone |" in report


def test_report_for_one_recorded_model_has_no_comparison(queries, tmp_path):
    record(queries, tmp_path, "claude-haiku-5-5", "20")
    comparison = outside.compare_models(
        queries, cassette_dir=tmp_path, against="claude-haiku-5-5", effort="medium"
    )
    results = outside.run_outside(queries, cassette_dir=tmp_path, **RUN)
    report = outside.render_report(
        results, queries, total_queries=3, comparison=comparison, **RUN_LABELS
    )
    assert "each recorded model" not in report and "**One run of one model.**" in report


# --- the published recordings, when the benchmark file has been downloaded -----

PUBLISHED_RUNS = {
    # model: right picks, picks, high-confidence picks wrong, high-confidence picks, F1
    "claude-haiku-5-5": (230, 303, 19, 148, 0.717),
    "claude-sonnet-5-5": (242, 313, 25, 184, 0.742),
    "claude-opus-5-5": (271, 354, 23, 196, 0.782),
}


@pytest.fixture(scope="module")
def published():
    if not outside.DATA_PATH.exists():
        pytest.skip("benchmark file not downloaded; run: uv run rfq2quote fetch-outside")
    queries = outside.load_queries(outside.DATA_PATH)
    comparison = outside.compare_models(queries, against="claude-haiku-5-5", effort="medium")
    return queries, comparison


def test_published_recordings_replay_to_the_published_numbers(published):
    queries, comparison = published
    assert (len(queries), sum(len(q.candidates) for q in queries)) == (994, 4427)
    assert list(comparison["models"]) == list(PUBLISHED_RUNS)
    for model, (right, picks, wrong_high, high, f1) in PUBLISHED_RUNS.items():
        s = comparison["models"][model]
        assert (s["matches"], s["llm_calls"], s["failed_calls"]) == (339, 927, 0), model
        assert (s["correct"], s["correct"] + s["wrong"]) == (right, picks), model
        assert (s["wrong_high"], s["correct_high"] + s["wrong_high"]) == (wrong_high, high), model
        assert s["f1"] == pytest.approx(f1, abs=0.0005), model


def test_published_recordings_the_larger_models_against_the_default(published):
    paired = published[1]["paired"]
    sonnet, opus = paired["claude-sonnet-5-5"], paired["claude-opus-5-5"]
    assert (sonnet["only_this_right"], sonnet["only_other_right"]) == (44, 30)
    assert (opus["only_this_right"], opus["only_other_right"]) == (60, 32)
    assert sonnet["p"] == pytest.approx(0.13, abs=0.005)
    assert opus["p"] == pytest.approx(0.005, abs=0.0005)


def test_published_recordings_keep_no_free_text_and_no_local_path():
    for path in sorted(outside.CASSETTE_DIR.glob("outside-*.json")):
        text = path.read_text(encoding="utf-8")
        entries = json.loads(text)
        assert {entry["output"]["reason"] for entry in entries.values()} == {""}, path.name
        assert "/Users/" not in text and "/home/" not in text and "sk-ant" not in text, path.name
        assert path.stat().st_size < 400_000, path.name


# --- fetching ----------------------------------------------------------------


def test_fetch_refuses_a_file_whose_checksum_differs(tmp_path):
    dest = tmp_path / "data" / "pairs.json.gz"
    with pytest.raises(ValueError, match="checksum"):
        outside.fetch(dest, download=lambda url: b"not the pinned file")
    assert not dest.exists()


def test_fetch_saves_the_pinned_file(tmp_path):
    payload = b"the pinned file"
    dest = tmp_path / "data" / "pairs.json.gz"
    seen = []
    outside.fetch(
        dest,
        download=lambda url: seen.append(url) or payload,
        sha256=hashlib.sha256(payload).hexdigest(),
    )
    assert dest.read_bytes() == payload
    assert outside.COMMIT in seen[0]


# --- report and command line -------------------------------------------------


def test_report_names_the_source_and_says_what_was_not_run(queries, tmp_path):
    results = outside.run_outside(queries, cassette_dir=tmp_path, **RUN)
    report = outside.render_report(results, queries, total_queries=3, **RUN_LABELS)
    assert "wbsg-uni-mannheim/billiger-de-products" in report
    assert outside.COMMIT[:12] in report
    assert "3 queries" in report
    assert "not recorded" in report


RUN_LABELS = dict(model="claude-haiku-5-5", effort="medium")


def test_command_says_how_to_get_the_data_when_it_is_missing(tmp_path):
    result = CliRunner().invoke(app, ["eval-outside", "--data", str(tmp_path / "missing.json.gz")])
    assert result.exit_code == 2
    assert "fetch-outside" in result.output


def test_command_adds_the_comparison_when_several_models_are_recorded(
    pairs_file, queries, two_recordings, tmp_path
):
    out = tmp_path / "report.md"
    result = CliRunner().invoke(
        app,
        ["eval-outside", "--data", str(pairs_file), "--out", str(out),
         "--cassette-dir", str(two_recordings)],
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    assert "| | `claude-haiku-5-5` | `claude-opus-5-5` |" in out.read_text(encoding="utf-8")


def test_command_scores_a_limited_number_of_queries_and_writes_the_report(pairs_file, tmp_path):
    out = tmp_path / "report.md"
    result = CliRunner().invoke(
        app,
        ["eval-outside", "--data", str(pairs_file), "--limit", "2", "--out", str(out),
         "--cassette-dir", str(tmp_path)],
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    assert "first 2 of 3 queries" in out.read_text(encoding="utf-8")
