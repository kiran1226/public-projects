"""Render docs/agent-flow.png: the components, and which of them is a model.

uv run python scripts/render_flow.py
"""

from __future__ import annotations

from pathlib import Path

from _draw import arrow, box, canvas, legend, path, save

OUT = Path(__file__).resolve().parents[1] / "docs" / "agent-flow.png"
DETECT, INVESTIGATE, REVIEW = 3.0, 9.0, 15.0  # x of the three columns
ROWS = (9.2, 7.4, 5.6, 3.8)  # y of the four rows


def render(out: Path = OUT) -> None:
    fig, ax = canvas(18, 12)
    ax.text(
        9, 11.4, "Revenue incident agent: components", ha="center", fontsize=15, fontweight="bold"
    )
    ax.text(DETECT, 10.4, "Detect (statistics, no LLM)", ha="center", fontsize=10, color="#00695C")
    ax.text(INVESTIGATE, 10.4, "Investigate and check", ha="center", fontsize=10, color="#5E35B1")
    ax.text(REVIEW, 10.4, "Decide and act", ha="center", fontsize=10, color="#2E7D32")
    r1, r2, r3, r4 = ROWS

    box(
        ax,
        DETECT,
        r1,
        "terminal",
        "Hourly data",
        "DuckDB: traffic, payments, orders,\nchange log, calendar",
        h=1.2,
    )
    box(
        ax,
        DETECT,
        r2,
        "ml",
        "Detector ladder",
        "static, seasonal, gradient-boosted\nforecast with conformal p-values",
        h=1.2,
    )
    box(
        ax,
        DETECT,
        r3,
        "code",
        "False-alarm control",
        "Benjamini-Hochberg per metric,\ntwo of three hours",
        h=1.2,
    )
    box(
        ax,
        DETECT,
        r4,
        "code",
        "Grouping",
        "alarms explained by one another;\none alert, named after a slice",
        h=1.2,
    )

    box(
        ax,
        INVESTIGATE,
        r1,
        "code",
        "Read-only tools",
        "metric series, breakdown,\nchange log, calendar",
        h=1.2,
    )
    box(
        ax,
        INVESTIGATE,
        r2,
        "llm",
        "Agent loop (Haiku 5.5)",
        "at most 12 tool calls; recorded\nand replayed",
        h=1.2,
    )
    box(
        ax,
        INVESTIGATE,
        r3,
        "code",
        "Scripted investigator",
        "the baseline: fixed procedure,\nno model, no key",
        h=1.2,
    )
    box(
        ax,
        INVESTIGATE,
        r4,
        "code",
        "Verifier and guardrails",
        "re-run cited tool calls; cause, target,\ncalendar; severity and owner",
        h=1.2,
    )

    box(
        ax,
        REVIEW,
        r1,
        "code",
        "Review queue",
        "SQLite: incidents, decisions,\nappend-only event log",
        h=1.2,
    )
    box(ax, REVIEW, r2, "human", "Reviewer", "approves, corrects the cause,\nor rejects", h=1.2)
    box(
        ax,
        REVIEW,
        r3,
        "code",
        "Executor",
        "only after approval; idempotency key,\none retry, every call logged",
        h=1.2,
    )
    box(
        ax,
        REVIEW,
        r4,
        "terminal",
        "Admin service (mock)",
        "HTTP: disable coupon, roll back,\nfail over, pause, challenge, resume",
        h=1.2,
    )

    for x in (DETECT, REVIEW):
        for upper, lower in zip(ROWS[:-1], ROWS[1:], strict=True):
            arrow(ax, (x, upper - 0.6), (x, lower + 0.6))
    # An alert goes to one of the two investigators; both use the tools and end at the checks.
    path(
        ax,
        [(DETECT + 2.3, r4), (5.9, r4), (5.9, r2), (INVESTIGATE - 2.3, r2)],
        "alert",
        at=0,
        offset=(0, 0.22),
    )
    path(ax, [(5.9, r3), (INVESTIGATE - 2.3, r3)])
    arrow(ax, (INVESTIGATE, r2 + 0.6), (INVESTIGATE, r1 - 0.6), "tool calls", offset=(0.85, 0))
    path(
        ax,
        [(INVESTIGATE + 2.3, r2), (12.0, r2), (12.0, r4), (INVESTIGATE + 2.3, r4)],
        "finding",
        at=0,
        offset=(0, 0.22),
    )
    path(ax, [(INVESTIGATE + 2.3, r3), (12.0, r3)])
    path(
        ax,
        [(INVESTIGATE, r4 - 0.6), (INVESTIGATE, 2.6), (12.5, 2.6), (12.5, r1), (REVIEW - 2.3, r1)],
        "checked finding",
        at=1,
        offset=(0, -0.25),
    )
    # Feedback
    path(
        ax,
        [(REVIEW + 2.3, r2), (17.6, r2), (17.6, 1.5), (0.35, 1.5), (0.35, r2), (DETECT - 2.3, r2)],
        "confirmed incidents leave the detectors' history; corrected causes become evaluation labels",
        at=2,
        offset=(0, -0.28),
    )

    legend(
        ax,
        5.0,
        0.5,
        [("ml", "ML model"), ("llm", "LLM call"), ("code", "Code"), ("human", "Person")],
    )
    save(fig, out)


if __name__ == "__main__":
    render()
    print(f"Wrote {OUT}")
