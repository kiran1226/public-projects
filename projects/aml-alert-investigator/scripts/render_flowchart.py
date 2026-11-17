"""Render docs/agent-flowchart.png: every decision for one alert, and every exit to a person.

uv run python scripts/render_flowchart.py
"""

from __future__ import annotations

from pathlib import Path

from _draw import arrow, box, canvas, diamond, legend, path, save

OUT = Path(__file__).resolve().parents[1] / "docs" / "agent-flowchart.png"
SPINE, LEAD, EXIT = 4.0, 10.6, 16.6  # x of the triage spine, the lead lane, the exits


def render(out: Path = OUT) -> None:
    fig, ax = canvas(19.5, 22)
    ax.text(9.75, 21.4, "Decision flow for one alert", ha="center", fontsize=16, fontweight="bold")
    ax.text(
        9.75,
        20.9,
        "Every tool result passes the privacy gateway; every request passes the outbound check.",
        ha="center",
        fontsize=10,
        color="#00695C",
    )

    # Triage spine
    box(ax, SPINE, 20.0, "terminal", "Alert raised", pill=True)
    box(ax, SPINE, 18.5, "code", "Risk features and hard flags", "on real data, inside the bank")
    diamond(
        ax, SPINE, 16.7, "Hard flag?\n(watchlist, PEP, high risk,\nprior escalation, large amount)"
    )
    box(
        ax,
        SPINE,
        14.7,
        "llm",
        "Triage agent (Haiku 5.5)",
        "profile, activity, prior alerts,\ntransactions; all as tokens",
        h=1.2,
    )
    diamond(ax, SPINE, 12.6, "Step failed?\n(API error, refusal, limit,\noutbound check hit)")
    diamond(ax, SPINE, 10.5, "Triage proposes\na close?")
    diamond(
        ax,
        SPINE,
        8.4,
        "Close stands?\n(high confidence, allowed reason,\nevery citation was issued)",
    )

    # Lead lane
    box(
        ax,
        LEAD,
        14.7,
        "llm",
        "Lead investigator (Opus 5.5)",
        "plans; counterparties, related parties,\nscreening, media, sensitive detail",
        h=1.2,
        w=5.4,
    )
    box(
        ax,
        LEAD,
        12.9,
        "llm",
        "Workers (Haiku 5.5)",
        "transaction digest, screening check,\nprior-alert summary",
        h=1.1,
        w=5.4,
    )
    diamond(
        ax,
        LEAD,
        10.9,
        "Investigation failed?\n(API error, refusal, step,\ndelegation or cost limit, leak)",
        w=5.4,
    )

    # Policy gate, shared by both tiers
    box(ax, 7.3, 6.3, "code", "Policy gate", "named rules over the disposition", w=5.0)
    diamond(
        ax,
        7.3,
        4.5,
        "Citations issued in this case,\nevery finding cited,\nconfidence not low?",
        w=5.4,
    )
    diamond(ax, 7.3, 2.4, "Close proposed on an alert\nwith a hard flag?", w=5.4)

    # Exits to a person
    box(
        ax,
        EXIT,
        12.6,
        "human",
        "Manual investigation",
        "MODEL_FAILURE, LIMIT_EXCEEDED,\nLEAK_BLOCKED: no recommendation",
        w=5.0,
        h=1.2,
    )
    box(
        ax,
        EXIT,
        4.5,
        "human",
        "Manual investigation",
        "CITATION_UNVERIFIED, FINDING_UNCITED,\nLOW_CONFIDENCE: output rejected",
        w=5.0,
        h=1.2,
    )
    box(ax, EXIT, 2.4, "human", "Senior review", "CLOSE_WITH_HARD_FLAG", w=5.0)
    box(
        ax,
        7.3,
        0.6,
        "human",
        "Analyst review",
        "recommendation with cited evidence, real values restored\n(auto-close mode: clean tier-1 closes pass, with QA sampling)",
        w=8.6,
        h=1.0,
    )

    # Spine arrows
    arrow(ax, (SPINE, 19.5), (SPINE, 19.0))
    arrow(ax, (SPINE, 18.0), (SPINE, 17.45))
    arrow(ax, (SPINE, 15.95), (SPINE, 15.3), "no", offset=(0.3, 0))
    arrow(ax, (SPINE, 14.1), (SPINE, 13.35))
    arrow(ax, (SPINE, 11.85), (SPINE, 11.25), "no", offset=(0.3, 0))
    arrow(ax, (SPINE, 9.75), (SPINE, 9.15), "yes", offset=(0.35, 0))
    path(ax, [(SPINE, 7.65), (SPINE, 6.3), (4.8, 6.3)], "yes", at=0, offset=(0.35, 0))

    # To the lead
    path(ax, [(6.3, 16.7), (LEAD, 16.7), (LEAD, 15.3)], "yes: skip triage", at=0, offset=(0, 0.25))
    path(
        ax,
        [(6.3, 10.5), (7.4, 10.5), (7.4, 14.7), (7.9, 14.7)],
        "no: escalate\nwith notes",
        at=0,
        offset=(0.2, 0.45),
    )
    path(ax, [(6.3, 8.4), (7.4, 8.4), (7.4, 10.5)], "no: rejected", at=0, offset=(0, -0.25))
    arrow(ax, (LEAD, 14.1), (LEAD, 13.45), "delegate", offset=(0.75, 0))
    arrow(ax, (LEAD, 12.35), (LEAD, 11.65))
    path(ax, [(LEAD, 10.15), (LEAD, 6.3), (9.8, 6.3)], "no: disposition", at=0, offset=(1.1, 0))

    # Failures
    path(
        ax,
        [(6.3, 12.6), (6.9, 12.6), (6.9, 17.6), (EXIT, 17.6), (EXIT, 13.2)],
        "yes",
        at=0,
        offset=(0, 0.25),
    )
    path(ax, [(13.3, 10.9), (EXIT, 10.9), (EXIT, 12.0)], "yes", at=0, offset=(0, 0.25))

    # Gate
    arrow(ax, (7.3, 5.8), (7.3, 5.25))
    arrow(ax, (10.0, 4.5), (14.1, 4.5), "no")
    arrow(ax, (7.3, 3.75), (7.3, 3.15), "yes", offset=(0.35, 0))
    arrow(ax, (10.0, 2.4), (14.1, 2.4), "yes")
    arrow(ax, (7.3, 1.65), (7.3, 1.1), "no", offset=(0.3, 0))

    legend(ax, 12.2, 20.0, [("code", "Code"), ("llm", "Model call"), ("human", "Person")])
    save(fig, out)


if __name__ == "__main__":
    render()
    print(f"Wrote {OUT}")
