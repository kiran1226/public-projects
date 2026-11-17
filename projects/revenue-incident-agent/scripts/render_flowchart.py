"""Render docs/agent-flowchart.png: every decision for one alert, and every exit to a person.

uv run python scripts/render_flowchart.py
"""

from __future__ import annotations

from pathlib import Path

from _draw import arrow, box, canvas, diamond, legend, path, save

OUT = Path(__file__).resolve().parents[1] / "docs" / "agent-flowchart.png"
SPINE, EXIT = 5.2, 14.2  # x of the main line and of the exits


def render(out: Path = OUT) -> None:
    fig, ax = canvas(18.5, 30)
    ax.text(
        9.25,
        29.4,
        "Decision flow for one hour and one alert",
        ha="center",
        fontsize=16,
        fontweight="bold",
    )
    ax.text(
        9.25,
        28.9,
        "All checks on a finding always run; the order below is how to read them, not a short-circuit.",
        ha="center",
        fontsize=10,
        color="#555555",
    )

    ys = {
        "start": 27.9, "score": 26.5, "alarm": 24.7, "explain": 22.9, "carried": 21.1,
        "investigate": 19.2, "failed": 17.3, "verify": 15.5, "event": 13.7, "proposed": 11.9,
        "passes": 10.0, "reviewer": 8.1, "approved": 6.3, "executor": 4.5, "confirmed": 2.7, "done": 0.9,
    }  # fmt: skip
    box(ax, SPINE, ys["start"], "terminal", "An hour closes", pill=True)
    box(
        ax,
        SPINE,
        ys["score"],
        "ml",
        "Score 706 series",
        "forecast and conformal p-value per slice;\nfalse-alarm control per metric",
        h=1.2,
    )
    diamond(ax, SPINE, ys["alarm"], "A slice out of range\nin two of the last three hours?")
    box(
        ax,
        SPINE,
        ys["explain"],
        "code",
        "Explain alarms by one another",
        "what nothing else explains is a root",
        h=1.1,
    )
    diamond(ax, SPINE, ys["carried"], "Carried by the root\nof an alert that is open?")
    box(
        ax,
        SPINE,
        ys["investigate"],
        "llm",
        "Investigate the new alert",
        "agent: tool loop, at most 12 read-only calls\n(or the script: fixed procedure, no model)",
        h=1.3,
        w=5.4,
    )
    diamond(ax, SPINE, ys["failed"], "Call failed or refused,\nno valid finding,\nor budget spent?")
    box(
        ax,
        SPINE,
        ys["verify"],
        "code",
        "Verify and check",
        "re-run every cited tool call;\ncause, calendar, action, target",
        h=1.2,
    )
    diamond(ax, SPINE, ys["event"], "A planned event explains\nthis metric and direction?")
    diamond(ax, SPINE, ys["proposed"], "Does the finding\npropose a mitigation?")
    diamond(
        ax,
        SPINE,
        ys["passes"],
        "Evidence verified, cause real, before\nonset and supported, action allowed,\ntarget in evidence, confidence high?",
        w=5.6,
        h=1.7,
    )
    box(
        ax,
        SPINE,
        ys["reviewer"],
        "human",
        "Reviewer, proposal ready",
        "sees evidence, trace and proposal",
        h=1.1,
    )
    diamond(ax, SPINE, ys["approved"], "Approved?")
    box(
        ax,
        SPINE,
        ys["executor"],
        "code",
        "Executor",
        "approval and pending action stored first;\nPOST with idempotency key, one retry",
        h=1.2,
        w=5.4,
    )
    diamond(ax, SPINE, ys["confirmed"], "Service confirmed?")
    box(ax, SPINE, ys["done"], "terminal", "Mitigated", pill=True)

    box(ax, EXIT, ys["alarm"], "terminal", "No alert", pill=True, w=5.6)
    box(
        ax,
        EXIT,
        ys["carried"],
        "terminal",
        "Joins that alert",
        "no second page for the same event",
        w=5.6,
        h=1.1,
    )
    box(
        ax,
        EXIT,
        ys["failed"],
        "human",
        "Queue: no finding",
        "INVESTIGATION_ERROR, BUDGET_EXHAUSTED;\nthe alert and its slice still arrive",
        w=5.6,
        h=1.2,
    )
    box(
        ax,
        EXIT,
        ys["event"],
        "human",
        "Queue: severity none",
        "EXPECTED_EVENT; nobody is paged,\nany proposal is withheld",
        w=5.6,
        h=1.2,
    )
    box(
        ax,
        EXIT,
        ys["proposed"],
        "human",
        "Queue: finding, no proposal",
        "`unknown` is a valid finding;\npaged or ticketed by money lost per hour",
        w=5.6,
        h=1.2,
    )
    box(
        ax,
        EXIT,
        ys["passes"],
        "human",
        "Queue: proposal withheld",
        "EVIDENCE_NOT_VERIFIED, CAUSE_NOT_IN_CHANGE_LOG,\nCAUSE_AFTER_ONSET, MITIGATION_NOT_ALLOWED,\nTARGET_NOT_IN_EVIDENCE, CAUSE_NOT_SUPPORTED,\nLOW_CONFIDENCE",
        w=5.6,
        h=1.8,
        title_dy=0.58,
    )
    box(
        ax,
        EXIT,
        ys["approved"],
        "terminal",
        "Corrected or rejected",
        "no action; a corrected cause\nbecomes an evaluation label",
        w=5.6,
        h=1.2,
    )
    box(
        ax,
        EXIT,
        ys["confirmed"],
        "human",
        "Queue: action failed",
        "reason shown; the approval is kept,\na retry reuses the same key",
        w=5.6,
        h=1.2,
    )

    order = list(ys)
    half = {"start": 0.5, "score": 0.6, "alarm": 0.75, "explain": 0.55, "carried": 0.75, "investigate": 0.65,
            "failed": 0.75, "verify": 0.6, "event": 0.75, "proposed": 0.75, "passes": 0.85, "reviewer": 0.55,
            "approved": 0.75, "executor": 0.6, "confirmed": 0.75, "done": 0.5}  # fmt: skip
    down = {"alarm": "yes", "carried": "no", "failed": "no", "event": "no", "proposed": "yes",
            "passes": "yes", "approved": "yes", "confirmed": "yes"}  # fmt: skip
    for upper, lower in zip(order[:-1], order[1:], strict=True):
        arrow(
            ax,
            (SPINE, ys[upper] - half[upper]),
            (SPINE, ys[lower] + half[lower]),
            down.get(upper, ""),
            offset=(0.4, 0),
        )
    side = {"alarm": "no", "carried": "yes", "failed": "yes", "event": "yes", "proposed": "no",
            "passes": "no", "approved": "no", "confirmed": "no"}  # fmt: skip
    for name, text in side.items():
        width = 5.6 if name == "passes" else 4.6
        arrow(ax, (SPINE + width / 2, ys[name]), (EXIT - 2.8, ys[name]), text)
    # A finding without a proposal, and a withheld one, still reach a reviewer.
    path(
        ax,
        [
            (EXIT + 2.8, ys["proposed"]),
            (17.6, ys["proposed"]),
            (17.6, ys["reviewer"]),
            (SPINE + 2.3, ys["reviewer"]),
        ],
        "a person still decides: approve the cause, correct it, or reject",
        at=2,
        offset=(0, 0.28),
    )
    path(ax, [(EXIT + 2.8, ys["passes"]), (17.6, ys["passes"])])

    legend(ax, 10.4, 27.9, [("ml", "ML model"), ("llm", "LLM call"), ("code", "Code")])
    legend(ax, 10.4, 27.2, [("decision", "Decision"), ("human", "Person"), ("terminal", "End")])
    save(fig, out)


if __name__ == "__main__":
    render()
    print(f"Wrote {OUT}")
