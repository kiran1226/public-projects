from __future__ import annotations

from pathlib import Path

import pytest

from rfq2quote.llm import CallStats
from rfq2quote.pipeline import DATA_DIR, load_catalog


@pytest.fixture(scope="session")
def catalog():
    return load_catalog()


@pytest.fixture(scope="session")
def data_dir() -> Path:
    return DATA_DIR


class ScriptedProvider:
    """Stands in for an LLM: answers each schema from a queue of prepared objects."""

    model = "claude-opus-5-5"

    def __init__(self, answers: dict[type, list]):
        self.answers = {schema: list(items) for schema, items in answers.items()}
        self.calls: list[tuple[str, str]] = []

    def structured(self, *, system, user, schema):
        self.calls.append((system, user))
        answer = self.answers[schema].pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer, CallStats(self.model, input_tokens=1000, output_tokens=200, latency_s=0.5)


@pytest.fixture
def scripted():
    return ScriptedProvider
