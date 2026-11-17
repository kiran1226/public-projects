from datetime import datetime, timedelta
from pathlib import Path

import pytest

from revwatch.scenarios import CalendarSpec, ChangeSpec, Effect, Label, Scenario
from revwatch.world import WorldConfig, generate_world

START = datetime(2026, 6, 1)


def at(day: int, hour: int = 0) -> datetime:
    """A timestamp `day` days and `hour` hours after the world's start."""
    return START + timedelta(days=day, hours=hour)


def small_scenarios() -> list[Scenario]:
    return [
        Scenario(
            id="bot",
            kind="incident",
            cause="bot_traffic",
            start=at(9, 10),
            end=at(9, 16),
            slice={"country": "FR", "channel": "affiliate"},
            effects=[Effect(param="observed_sessions_mult", value=3.0)],
        ),
        Scenario(
            id="pay",
            kind="incident",
            cause="payment_provider_failure",
            start=at(10, 10),
            end=at(10, 16),
            slice={"country": "DE", "payment_method": "card"},
            effects=[Effect(param="payment_success_mult", value=0.3)],
            change=ChangeSpec(
                kind="provider_status", entity="psp_alpha", note="psp_alpha: elevated error rates"
            ),
        ),
        Scenario(
            id="late",
            kind="benign",
            cause="unknown",
            start=at(14, 3),
            end=at(14, 5),
            drop_table="traffic",
        ),
        Scenario(
            id="promo",
            kind="benign",
            cause="expected_event",
            start=at(16),
            end=at(17),
            effects=[Effect(param="sessions_mult", value=1.4)],
            calendar=CalendarSpec(kind="planned_promotion", note="Weekend sale"),
        ),
        Scenario(
            id="holiday",
            kind="benign",
            cause="expected_event",
            start=at(18),
            end=at(19),
            slice={"country": "ES"},
            effects=[Effect(param="sessions_mult", value=0.8)],
            calendar=CalendarSpec(kind="public_holiday", note="Public holiday in Spain"),
        ),
    ]


@pytest.fixture(scope="session")
def small_world(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, list[Label]]:
    path = tmp_path_factory.mktemp("world") / "small.duckdb"
    labels = generate_world(WorldConfig(seed=5, days=21), small_scenarios(), path)
    return path, labels
