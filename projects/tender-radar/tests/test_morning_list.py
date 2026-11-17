from __future__ import annotations

import importlib.util
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from tenderradar.decisions import DecisionLog
from tenderradar.log import PredictionLog
from tenderradar.models import Condition, GatedAssessment, Shortlist, ShortlistEntry
from tenderradar.store import COMPETITION, Store

from conftest import make_notice, raw_notice

pytest.importorskip("streamlit")
APP = Path(__file__).resolve().parents[1] / "app" / "morning_list.py"

DAY = date(2026, 4, 14)
EARLIER = date(2026, 4, 13)
FIRST, SECOND, THIRD = "300100-2026", "300200-2026", "300300-2026"
QUOTE = "Mindestjahresumsatz von 1 000 000 EUR in den letzten drei Geschäftsjahren."


@pytest.fixture(scope="module")
def page():
    """The page's functions, loaded from the script without running the page."""
    spec = importlib.util.spec_from_file_location("morning_list", APP)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def assessment(decision: str = "bid", fit: int = 82, **over) -> GatedAssessment:
    fields = dict(
        fit=fit,
        decision=decision,
        reason="Two spectrometers with service: core business.",
        reason_quote="Beschaffung von zwei Spektrometern",
        conditions=[
            Condition(
                requirement="Annual turnover of at least EUR 1,000,000.", quote=QUOTE, met="yes"
            )
        ],
        cost_usd=0.0004,
    )
    return GatedAssessment(**{**fields, **over})


def shortlist(day: date = DAY, ranker: str = "fused+model", hour: int = 6, **over) -> Shortlist:
    fields = dict(
        profile_id="client",
        day=day,
        ranker=ranker,
        system_version="abc12345",
        mode="live",
        written_at=datetime(2026, 4, 14, hour, 0, tzinfo=UTC),
        candidates=37,
        removed={"outside_territory": 41, "deadline_too_close": 2},
        entries=[
            ShortlistEntry(rank=1, publication_number=FIRST, score=0.9, assessment=assessment()),
            ShortlistEntry(
                rank=2,
                publication_number=SECOND,
                score=0.7,
                assessment=assessment(
                    "ask_person",
                    40,
                    reason_quote=None,
                    downgraded=True,
                    conditions=[],
                    dropped_conditions=2,
                ),
            ),
            ShortlistEntry(rank=3, publication_number=THIRD, score=0.5),
        ],
        cost_usd=0.0008,
    )
    return Shortlist(**{**fields, **over})


def write_log(home: Path, *lists: Shortlist) -> Path:
    path = home / "data" / "log" / "live.jsonl"
    PredictionLog(path).append(lists)
    return path


def write_db(home: Path) -> Path:
    path = home / "var" / "tenderradar.db"
    store = Store(path)
    store.upsert_raw(
        COMPETITION,
        [
            raw_notice(),
            raw_notice(
                **{
                    "publication-number": SECOND,
                    "procedure-identifier": "proc-0002",
                    "title-proc": {"deu": "Reagenzien für die Mikrobiologie"},
                    "estimated-value-proc": None,
                }
            ),
        ],
    )
    store.close()
    return path


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    monkeypatch.setenv("TENDERRADAR_HOME", str(tmp_path))
    return tmp_path


def run_page():
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_file(str(APP), default_timeout=30).run()
    assert not app.exception
    return app


def texts(app) -> str:
    """Everything the page wrote, as one string."""
    blocks = [*app.markdown, *app.caption, *app.info, *app.warning, *app.success, *app.error]
    return "\n".join(block.value for block in blocks)


def decisions(home: Path) -> DecisionLog:
    return DecisionLog(home / "data" / "corrections" / "decisions.jsonl")


# --- the page's functions


def test_the_models_list_is_preferred_over_the_rankers(page, tmp_path):
    path = write_log(tmp_path, shortlist(ranker="fused"), shortlist(ranker="fused+model"))
    assert page.client_lists(path)[DAY].ranker == "fused+model"


def test_the_rankers_list_is_used_when_the_model_wrote_none(page, tmp_path):
    path = write_log(tmp_path, shortlist(ranker="fused"))
    assert page.client_lists(path)[DAY].ranker == "fused"


def test_only_the_clients_lists_are_shown_newest_day_first(page, tmp_path):
    path = write_log(
        tmp_path,
        shortlist(day=EARLIER),
        shortlist(day=DAY),
        shortlist(day=date(2026, 4, 15), profile_id="s_0001", ranker="fused"),
    )
    assert list(page.client_lists(path)) == [DAY, EARLIER]


def test_of_two_versions_of_a_days_list_the_one_written_last_wins(page, tmp_path):
    path = write_log(
        tmp_path,
        shortlist(system_version="new00000", hour=9),
        shortlist(system_version="old00000", hour=6),
    )
    assert page.client_lists(path)[DAY].system_version == "new00000"


def test_no_log_means_no_lists(page, tmp_path):
    assert page.client_lists(tmp_path / "data" / "log" / "live.jsonl") == {}


def test_notices_are_looked_up_by_publication_number(page, tmp_path):
    found = page.notices_for(write_db(tmp_path), DAY)
    assert set(found) == {FIRST, SECOND}
    assert found[FIRST].buyer_name == "Universitätsklinikum Beispielstadt"
    assert page.notices_for(tmp_path / "var" / "tenderradar.db", EARLIER) == {}


def test_a_missing_database_is_not_created(page, tmp_path):
    path = tmp_path / "var" / "tenderradar.db"
    assert page.notices_for(path, DAY) == {}
    assert not path.exists()


def test_removed_tenders_are_counted_under_each_rule_name(page):
    assert page.removed_text(shortlist()) == (
        "Removed by rule (43): `deadline_too_close` 2, `outside_territory` 41"
    )
    assert page.removed_text(shortlist(removed={})) == "No tender was removed by a rule."


def test_facts_come_from_the_notice(page):
    facts = dict(page.notice_facts(make_notice(), date(2026, 4, 14)))
    assert facts == {
        "Buyer": "Universitätsklinikum Beispielstadt",
        "Country": "DEU",
        "Deadline": "2026-05-18 (34 days from today)",
        "Estimated value": "EUR 480,000",
        "Language": "deu",
        "Lots": "2",
    }


def test_fields_the_buyer_left_out_are_not_guessed(page):
    notice = make_notice(
        **{
            "buyer-name": None,
            "deadline-receipt-tender-date-lot": None,
            "estimated-value-proc": None,
            "title-lot": None,
            "description-lot": None,
        }
    )
    facts = dict(page.notice_facts(notice, date(2026, 4, 14)))
    assert facts["Buyer"] == facts["Deadline"] == facts["Estimated value"] == "not stated"
    assert facts["Lots"] == "not stated"


def test_a_deadline_that_has_passed_says_so(page):
    assert (
        page.deadline_text(date(2026, 4, 10), date(2026, 4, 14)) == "2026-04-10 (passed 4 days ago)"
    )


def test_the_link_goes_to_the_notice_on_ted(page):
    assert page.notice_url(FIRST) == "https://ted.europa.eu/en/notice/-/detail/300100-2026"
    assert page.notice_url(FIRST) == make_notice().url


def test_notice_text_is_never_read_as_formatting(page):
    planted = (
        "Analyser ![x](http://example.invalid/a.png) [click](http://example.invalid)\n# Skip *this*"
    )
    safe = page.plain(planted)
    assert "\n" not in safe
    assert "![x](" not in safe and "[click](" not in safe
    assert safe.replace("\\", "") == " ".join(planted.split())
    assert page.plain(None) == ""


def test_the_verdict_names_the_decision_and_the_fit(page):
    assert (
        page.verdict_text(assessment("ask_person", 40)) == "Model: **ask a person**, fit 40 of 100"
    )
    assert page.verdict_text(None) == "Not read by the model."


def test_the_reason_is_shown_with_its_quoted_passage(page):
    assert page.reason_lines(assessment()) == [
        "Two spectrometers with service: core business.",
        "> Beschaffung von zwei Spektrometern",
    ]


def test_a_reason_whose_passage_the_gate_rejected_says_so(page):
    lines = page.reason_lines(assessment("ask_person", reason_quote=None, downgraded=True))
    assert lines[1] == (
        "*The passage the model quoted for this is not in the notice. "
        "Its decision was changed to ask a person.*"
    )
    kept = page.reason_lines(assessment("ask_person", reason_quote=None, downgraded=False))
    assert "changed" not in kept[1]


@pytest.mark.parametrize(
    ("met", "label"),
    [
        ("yes", "the profile meets it"),
        ("no", "the profile does not meet it"),
        ("unknown", "the profile does not say"),
    ],
)
def test_a_condition_shows_its_quote_and_whether_the_profile_meets_it(page, met, label):
    condition = Condition(
        requirement="ISO 9001 certificate.", quote="Zertifikat nach ISO 9001", met=met
    )
    assert page.condition_lines(condition) == [
        f"- ISO 9001 certificate. ({label})",
        "  > Zertifikat nach ISO 9001",
    ]


def test_submit_records_a_decision_and_refuses_a_skip_without_a_reason(page, tmp_path):
    log = DecisionLog(tmp_path / "decisions.jsonl")
    assert page.submit(log, FIRST, DAY, "skip", "  ") == page.SKIP_NEEDS_REASON
    assert log.all() == []
    assert page.submit(log, FIRST, DAY, "skip", "Turnover requirement too high.") is None
    assert page.submit(log, SECOND, DAY, "bid", "") is None
    assert {n: d.decision for n, d in log.latest().items()} == {FIRST: "skip", SECOND: "bid"}
    assert page.decision_text(log.latest()[FIRST]).endswith(
        "Reason: Turnover requirement too high."
    )
    assert "Reason" not in page.decision_text(log.latest()[SECOND])


# --- the page itself


def test_without_a_list_the_page_names_the_command_to_run(home):
    app = run_page()
    assert len(app.info) == 1
    assert "No list has been written" in app.info[0].value
    assert "`uv run tenderradar run`" in app.info[0].value
    assert not app.button


def test_the_page_shows_the_day_the_counts_and_each_entry(home):
    write_log(home, shortlist(ranker="fused", entries=[]), shortlist())
    write_db(home)
    app = run_page()

    assert app.subheader[0].value == "Tenders published 2026-04-14"
    assert {m.label: m.value for m in app.metric} == {
        "Passed the rules": "37",
        "On the list": "3",
        "Read by the model": "2",
        "Decided": "0 of 3",
    }
    shown = texts(app)
    assert "Removed by rule (43): `deadline_too_close` 2, `outside_territory` 41" in shown
    assert "by `fused+model`, system version `abc12345`" in shown

    # Place, number as a link, and the notice's own fields.
    assert (
        "**1.** [300100-2026](https://ted.europa.eu/en/notice/-/detail/300100-2026) "
        "Lieferung von Analysegeräten für das Zentrallabor"
    ) in shown
    assert (
        "Buyer: Universitätsklinikum Beispielstadt · Country: DEU · Deadline: 2026-05-18" in shown
    )
    assert "Estimated value: EUR 480,000" in shown
    assert "Reagenzien für die Mikrobiologie" in shown
    assert "Estimated value: not stated" in shown

    # What the model wrote, with the passages it rests on.
    assert "Model: **bid**, fit 82 of 100" in shown
    assert "Two spectrometers with service: core business." in shown
    assert "> Beschaffung von zwei Spektrometern" in shown
    assert "- Annual turnover of at least EUR 1,000,000. (the profile meets it)" in shown
    assert f"  > {QUOTE}" in shown
    assert "Model: **ask a person**, fit 40 of 100" in shown
    assert "is not in the notice. Its decision was changed to ask a person." in shown
    assert "2 more conditions are not shown" in shown

    # The third entry was not read and is not in the database: it still has its link.
    assert "**3.** [300300-2026](https://ted.europa.eu/en/notice/-/detail/300300-2026)" in shown
    assert "Not read by the model." in shown
    assert [b.label for b in app.button] == ["Bid", "Skip"] * 3


def test_without_the_database_the_list_is_still_shown(home):
    write_log(home, shortlist())
    app = run_page()
    shown = texts(app)
    assert "The local database is not on this machine" in shown
    assert "**1.** [300100-2026](https://ted.europa.eu/en/notice/-/detail/300100-2026)" in shown
    assert "Buyer:" not in shown
    assert not (home / "var" / "tenderradar.db").exists()


def test_a_list_the_model_did_not_read_says_so(home):
    entries = [ShortlistEntry(rank=1, publication_number=FIRST, score=0.9)]
    write_log(home, shortlist(ranker="fused", entries=entries))
    assert "The model did not read this list." in texts(run_page())


def test_entries_skipped_at_the_cap_are_counted(home):
    write_log(home, shortlist(skipped_assessments=4))
    assert "4 tenders were not read by the model" in texts(run_page())


def test_bid_records_a_decision_and_the_entry_shows_it(home):
    write_log(home, shortlist())
    app = run_page()
    app.button(key=f"bid-{FIRST}").click().run()
    assert not app.exception

    made = decisions(home).latest()
    assert set(made) == {FIRST}
    assert (made[FIRST].decision, made[FIRST].day, made[FIRST].reason) == ("bid", DAY, "")
    assert "Decided: **bid** on" in app.success[0].value
    assert {m.label: m.value for m in app.metric}["Decided"] == "1 of 3"


def test_skip_without_a_reason_is_refused_and_says_why(home):
    write_log(home, shortlist())
    app = run_page()
    app.button(key=f"skip-{SECOND}").click().run()
    assert not app.exception
    assert [e.value for e in app.error] == [
        "A skip needs a reason. Write one and press Skip again."
    ]
    assert decisions(home).all() == []

    # The message goes away on the next run of the page.
    app.run()
    assert not app.error


def test_skip_with_a_reason_is_recorded_and_the_field_is_cleared(home):
    write_log(home, shortlist())
    app = run_page()
    app.text_input(key=f"reason-{SECOND}").input("Turnover requirement is above ours.")
    app.button(key=f"skip-{SECOND}").click().run()
    assert not app.exception and not app.error

    made = decisions(home).latest()[SECOND]
    assert (made.decision, made.reason) == ("skip", "Turnover requirement is above ours.")
    assert "Decided: **skip**" in app.warning[0].value
    assert "Reason: Turnover requirement is above ours." in app.warning[0].value
    assert app.text_input(key=f"reason-{SECOND}").value == ""


def test_a_second_decision_supersedes_the_first_and_both_stay_on_file(home):
    write_log(home, shortlist())
    app = run_page()
    app.button(key=f"bid-{FIRST}").click().run()
    app.text_input(key=f"reason-{FIRST}").input("Lot 1 was withdrawn.")
    app.button(key=f"skip-{FIRST}").click().run()
    assert not app.exception

    log = decisions(home)
    assert [d.decision for d in log.all()] == ["bid", "skip"]
    assert log.latest()[FIRST].decision == "skip"
    assert not app.success
    assert "Decided: **skip**" in app.warning[0].value


def test_decisions_made_earlier_are_shown_when_the_page_opens(home):
    write_log(home, shortlist())
    decisions(home).record(THIRD, DAY, "skip", "Outside what we sell.")
    app = run_page()
    assert "Decided: **skip**" in app.warning[0].value
    assert "Reason: Outside what we sell." in app.warning[0].value


def test_the_day_selector_shows_an_earlier_day(home):
    earlier_entry = ShortlistEntry(rank=1, publication_number="299000-2026", score=0.8)
    write_log(
        home,
        shortlist(day=EARLIER, ranker="fused", candidates=12, removed={}, entries=[earlier_entry]),
        shortlist(),
    )
    app = run_page()
    selector = app.sidebar.selectbox(key="day")
    assert selector.options == ["2026-04-14", "2026-04-13"]
    assert selector.value == DAY

    selector.select(EARLIER).run()
    assert not app.exception
    assert app.subheader[0].value == "Tenders published 2026-04-13"
    shown = texts(app)
    assert "No tender was removed by a rule." in shown
    assert "[299000-2026](https://ted.europa.eu/en/notice/-/detail/299000-2026)" in shown
    assert "300100-2026" not in shown

    # A decision made on an earlier day carries that day.
    app.button(key="bid-299000-2026").click().run()
    assert decisions(home).latest()["299000-2026"].day == EARLIER
