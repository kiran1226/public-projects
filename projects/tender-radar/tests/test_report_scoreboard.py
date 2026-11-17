from __future__ import annotations

import json
from datetime import UTC, date, datetime

import pytest

from tenderradar import report, scoreboard
from tenderradar.config import Settings
from tenderradar.log import PredictionLog
from tenderradar.models import GatedAssessment, Outcome, Shortlist, ShortlistEntry
from tenderradar.truth import OutcomeLog

NOW = datetime(2026, 11, 17, 6, 30, tzinfo=UTC)
MAY, JUNE = date(2026, 5, 12), date(2026, 6, 9)


def shortlist(
    ranker, numbers, day, profile_id="s1", mode="backtest", written=NOW, assess=None, cost=0.0
):
    entries = [
        ShortlistEntry(
            rank=i, publication_number=n, score=round(1 / i, 4), assessment=(assess or {}).get(n)
        )
        for i, n in enumerate(numbers, 1)
    ]
    return Shortlist(
        profile_id=profile_id,
        day=day,
        ranker=ranker,
        system_version="v1",
        mode=mode,
        written_at=written,
        candidates=len(numbers) + 3,
        removed={"outside_territory": 4},
        entries=entries,
        cost_usd=cost,
    )


def outcome(number, day, profile_id="s1", hits=()):
    return Outcome(
        profile_id=profile_id,
        procedure_id=f"proc-{number}",
        arrived=date(2026, 9, 1),
        publication_number=number,
        day=day,
        filter_hits=list(hits),
    )


@pytest.fixture
def settings(tmp_path):
    settings = Settings(
        log_dir=tmp_path / "data" / "log",
        scoreboard_dir=tmp_path / "var" / "scoreboard",
        cassette_dir=tmp_path / "data" / "cassettes",
        profiles_dir=tmp_path / "data" / "profiles",
    )
    skip = GatedAssessment(
        fit=10, decision="skip", reason="Not sold.", reason_quote="x" * 20, cost_usd=0.0004
    )
    bid = GatedAssessment(
        fit=90,
        decision="bid",
        reason="Core business.",
        reason_quote="y" * 20,
        dropped_conditions=1,
        downgraded=False,
        cost_usd=0.0004,
    )
    PredictionLog(settings.log_dir / report.BACKTEST_LOG).append(
        [
            shortlist("alert", ["b", "a"], MAY),
            shortlist("fused", ["a", "b"], MAY),
            shortlist("fused+model", ["a", "b"], MAY, assess={"a": bid, "b": skip}, cost=0.0008),
            shortlist("alert", ["d"], JUNE),
            shortlist("fused", ["c", "d"], JUNE),
        ]
    )
    OutcomeLog(settings.log_dir / report.BACKTEST_OUTCOMES).append(
        [
            outcome("a", MAY),
            outcome("b", MAY),
            outcome("c", JUNE),
            outcome("z", JUNE, hits=["outside_territory"]),
        ]
    )
    (settings.log_dir / report.BACKTEST_META).write_text(
        json.dumps(
            {
                "divisions": ["24", "33", "38"],
                "profile_start": "2025-01-01",
                "cut": "2026-04-01",
                "test_end": "2026-06-30",
                "held_out_start": "2026-06-01",
                "awards_before_cut": 86000,
                "awards_until": "2026-11-17",
                "suppliers": 34000,
                "eligible": 1500,
                "panel": 200,
                "min_past_wins": 5,
                "min_test_wins": 2,
                "tenders": 11549,
                "days": 60,
                "embedder": "test-model",
            }
        ),
        encoding="utf-8",
    )
    return settings


def test_development_and_held_out_months_are_scored_apart(settings):
    data = report.collect(settings)
    dev = {s.ranker: s for s in data["development"]}
    held = {s.ranker: s for s in data["held_out"]}
    assert dev["fused"].outcomes == 2 and dev["fused"].found[1] == 1
    assert (
        held["fused"].outcomes == 2
        and held["fused"].found[1] == 1
        and held["fused"].removed_by_rule == 1
    )
    assert held["alert"].not_listed == 1
    assert "fused+model" not in dev and "fused+model" not in held


def test_the_model_is_compared_on_its_own_sample(settings):
    data = report.collect(settings)
    sample = {s.ranker: s for s in data["model_sample"]}
    assert set(sample) == {"alert", "fused", "fused+model"}
    assert all(s.outcomes == 2 for s in sample.values())
    assert sample["fused+model"].skipped_by_model == 1
    assert data["model"] == {
        "lists": 1,
        "assessments": 2,
        "decisions": {"bid": 1, "skip": 1},
        "downgraded": 0,
        "conditions_kept": 0,
        "conditions_dropped": 1,
        "cost_usd": 0.0008,
    }


def test_report_states_counts_the_worst_error_and_limits(settings):
    text = report.render(settings)
    assert "On the panel: 200" in text and "11549 tenders on 60 publication days" in text
    assert "## Held-out month (from 2026-06-01)" in text
    assert "Removed by a hard rule before ranking: 1 of 4 (outside_territory: 1)." in text
    assert 'Marked "skip" by the model, development months: 1 of 2 won tenders' in text
    assert 'Marked "skip" by the model, held-out month' not in text  # the sample had no June list
    assert "The live run has not started." in text
    assert "Winners are the only outcome on record" in text
    out = report.write(settings, settings.log_dir.parent / "eval-report.md")
    assert out.read_text(encoding="utf-8") == text


def test_report_on_empty_logs_says_so(tmp_path):
    empty = Settings(log_dir=tmp_path / "log", scoreboard_dir=tmp_path / "sb")
    text = report.render(empty)
    assert "No backtest has been replayed yet." in text and "Not scored yet." in text
    assert "No model assessments are in the log." in text


def test_live_lists_are_reported_with_what_is_still_waiting(settings):
    live_day = date(2026, 11, 17)
    PredictionLog(settings.log_dir / report.LIVE_LOG).append(
        [
            shortlist("fused", ["n1", "n2"], live_day, mode="live"),
            shortlist(
                "fused+model", ["n1"], live_day, profile_id="client", mode="live", cost=0.002
            ),
        ]
    )
    text = report.render(settings)
    assert "Live since 2026-11-17, starting with the tenders published on 2026-11-17; " in text
    assert "1 publication days processed, 2 lists written." in text
    assert "None can be scored yet." in text


def test_scoreboard_json_and_page(settings):
    live_day = date(2026, 11, 17)
    PredictionLog(settings.log_dir / report.LIVE_LOG).append(
        [
            shortlist("fused", ["n1", "n2"], live_day, mode="live"),
            shortlist(
                "fused+model",
                ["n1"],
                live_day,
                profile_id="client",
                mode="live",
                cost=0.002,
                assess={
                    "n1": GatedAssessment(
                        fit=80, decision="bid", reason="</script> fits", cost_usd=0.002
                    )
                },
            ),
        ]
    )
    health = {
        "ok": True,
        "checked_at": "2026-11-17T06:30:00+00:00",
        "checks": [{"name": "feed_answers", "ok": True, "detail": "ok"}],
    }
    page = scoreboard.write(settings, now=NOW, health=health)
    board = json.loads((settings.scoreboard_dir / "scoreboard.json").read_text(encoding="utf-8"))
    assert board["live"]["since"] == "2026-11-17" and board["live"]["lists"] == 2
    assert board["live"]["first_day"] == "2026-11-17"
    assert board["live"]["tenders_listed"] == 2 and board["live"]["client_assessments"] == 1
    assert board["live"]["outcomes_scored"] == 0 and board["live"]["scores"] == []
    assert board["cost"] == {
        "cap_usd_per_day": 0.5,
        "today_usd": 0.002,
        "last_30_days_usd": 0.002,
        "live_total_usd": 0.002,
    }
    assert [r["ranker"] for r in board["backtest"]["held_out"]] == ["alert", "fused"]
    assert board["backtest"]["held_out"][1]["top1"] == 1
    assert board["health"]["ok"] is True
    html = page.read_text(encoding="utf-8")
    assert "/*SCOREBOARD_DATA*/null" not in html
    assert '"project": "tender-radar"' in html
    # The data is embedded in a script element, so it must not be able to close it.
    start = html.index('<script id="data"')
    end = html.index("</script>", start)
    assert json.loads(html[html.index(">", start) + 1 : end])["live"]["lists"] == 2


def test_scoreboard_before_anything_has_run(tmp_path):
    empty = Settings(log_dir=tmp_path / "log", scoreboard_dir=tmp_path / "sb")
    board = scoreboard.build(empty, now=NOW)
    assert board["live"]["since"] is None and board["live"]["lists"] == 0
    assert board["backtest"]["development"] == [] and board["health"] is None
