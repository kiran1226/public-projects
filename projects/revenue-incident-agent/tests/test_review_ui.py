import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
import uvicorn

from mock_admin.app import create_app
from revwatch.store import Store
from tests.test_mock_admin import ENTITIES
from tests.test_store import put

pytest.importorskip("streamlit")
APP = str(Path(__file__).resolve().parents[1] / "app" / "review_ui.py")


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@pytest.fixture()
def queue(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Store:
    path = tmp_path / "queue.sqlite"
    monkeypatch.setenv("REVWATCH_STORE", str(path))
    monkeypatch.setenv("REVWATCH_WORLD", str(tmp_path / "no-world.duckdb"))
    monkeypatch.setenv("REVWATCH_ADMIN_URL", f"http://127.0.0.1:{free_port()}")  # nothing listens
    return Store(path)


@pytest.fixture()
def admin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    port = free_port()
    app = create_app(tmp_path / "admin.sqlite", ENTITIES)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    url = f"http://127.0.0.1:{port}"
    monkeypatch.setenv("REVWATCH_ADMIN_URL", url)
    yield url
    server.should_exit = True
    thread.join(timeout=10)


def page():  # type: ignore[no-untyped-def]
    from streamlit.testing.v1 import AppTest

    return AppTest.from_file(APP, default_timeout=30).run()


def click(app, label: str):  # type: ignore[no-untyped-def]
    return next(b for b in app.button if b.label == label).click().run()


def widget(widgets, label: str):  # type: ignore[no-untyped-def]
    return next(w for w in widgets if w.label == label)


def test_the_page_shows_the_finding_the_evidence_and_the_proposal(queue: Store) -> None:
    put(queue)
    app = page()
    assert not app.exception
    text = " ".join(e.value for e in [*app.markdown, *app.header, *app.subheader, *app.success])
    assert "discount_rate above expected for channel=email, country=DE" in text
    assert "coupon_misconfiguration" in text and "1 of 1 cited values recompute" in text
    assert "disable_coupon" in text and "Passed every check" in text
    assert [b.label for b in app.button] == [
        "Approve and execute",
        "Correct the cause",
        "Reject: not an incident",
    ]


def test_a_withheld_proposal_shows_why_and_cannot_be_executed(queue: Store) -> None:
    put(queue, proposal=None)
    app = page()
    assert any("LOW_CONFIDENCE" in e.value and "well-supported" in e.value for e in app.error)
    assert any("Withheld" in w.value for w in app.warning)
    assert "Approve the cause" in [b.label for b in app.button]
    click(app, "Approve the cause")
    assert queue.get("INC-1").status == "approved" and queue.action_for("INC-1") is None


def test_approving_sends_the_action_to_the_admin_service_once(queue: Store, admin: str) -> None:
    import httpx

    put(queue)
    app = page()
    widget(app.text_input, "Reviewer").set_value("j.meier").run()
    app = click(app, "Approve and execute")
    assert not app.exception
    assert queue.get("INC-1").status == "mitigated"
    applied = httpx.get(f"{admin}/actions").json()
    assert [(a["action"], a["reviewer"]) for a in applied] == [("disable_coupon", "j.meier")]
    assert queue.decisions("INC-1")[0]["reviewer"] == "j.meier"


def test_approving_while_the_service_is_down_fails_visibly_and_can_be_retried(queue: Store) -> None:
    put(queue)
    app = click(page(), "Approve and execute")
    assert not app.exception
    assert queue.get("INC-1").status == "action_failed"
    widget(app.selectbox, "Show").set_value("action_failed").run()
    text = " ".join(e.value for e in app.markdown)
    assert "failed" in text and "admin service not reached" in text
    assert "Retry the action" in [b.label for b in app.button]
    assert "Correct the cause" not in [b.label for b in app.button]


def test_a_reviewer_can_correct_or_reject(queue: Store) -> None:
    put(queue, "INC-1")
    put(queue, "INC-2", severity="ticket")
    app = page()
    widget(app.selectbox, "The cause is").set_value("price_error").run()
    click(app, "Correct the cause")
    assert queue.get("INC-1").status == "corrected"
    assert queue.corrections()[0]["cause"] == "price_error"
    app = page()
    click(app, "Reject: not an incident")
    assert queue.get("INC-2").status == "rejected"
    assert "Nothing is open." in " ".join(i.value for i in page().info)


def test_an_empty_queue_says_what_to_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REVWATCH_STORE", str(tmp_path / "none.sqlite"))
    app = page()
    assert not app.exception and "revwatch replay" in app.info[0].value
    assert not (tmp_path / "none.sqlite").exists()
