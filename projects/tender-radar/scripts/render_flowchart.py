"""Render docs/agent-flowchart.png: every decision in one morning's run, and every exit to a person.

uv run python scripts/render_flowchart.py
"""

from __future__ import annotations

from pathlib import Path

from _draw import arrow, box, canvas, diamond, legend, path, save

OUT = Path(__file__).resolve().parents[1] / "docs" / "agent-flowchart.png"

# Figures shown in the labels. They mirror config.Settings.list_size and assess_top.
LIST_SIZE, ASSESS_TOP = 15, 10

# x of the four lanes: exits on the left, the run, the model, what the model step leaves.
LEFT, SPINE, MODEL, RIGHT = 1.9, 6.4, 12.1, 17.2
WRAP = 9.2  # x of the line that carries the list from the run lane to the model lane
EDGE = 19.6  # x of the line down the right margin
LATER = 1.2  # y of the row below the dashed line


def render(out: Path = OUT) -> None:
    fig, ax = canvas(19.9, 21.7)
    ax.text(
        9.95,
        21.15,
        "Decision flow for one morning's run",
        ha="center",
        fontsize=16,
        fontweight="bold",
    )
    ax.text(
        9.95,
        20.65,
        "Rules and rankers run for every profile. The model reads only the client's list, inside the daily spend cap.",
        ha="center",
        fontsize=10,
        color="#5E35B1",
    )

    # The run: feed, rules, ranking
    box(ax, SPINE, 19.35, "terminal", "Scheduled run starts", w=4.4, pill=True)
    box(
        ax,
        SPINE,
        17.75,
        "code",
        "Fetch tenders and award notices",
        "from the feed into the local store",
        w=4.4,
        h=1.1,
    )
    diamond(ax, SPINE, 15.9, "Feed answered, in the\nexpected format?", w=4.4)
    box(
        ax,
        SPINE,
        14.05,
        "code",
        "Tenders first published today",
        "taken once for every profile:\nthe panel suppliers and the client",
        w=4.4,
        h=1.1,
    )
    diamond(
        ax, SPINE, 12.2, "Breaks a hard rule?\n(deadline, territory,\nvalue band, procedure)", w=4.4
    )
    box(
        ax,
        SPINE,
        10.3,
        "code",
        f"Rank, keep the first {LIST_SIZE}",
        "weighted categories and embeddings\n(local model), fused by rank",
        w=4.4,
        h=1.2,
    )

    # The model step, for the client's list
    diamond(
        ax,
        MODEL,
        17.4,
        f"In the first {ASSESS_TOP} of the\nclient's list, and a\nmodel key is set?",
    )
    diamond(ax, MODEL, 15.35, "Daily spend cap\nreached?")
    box(
        ax,
        MODEL,
        13.45,
        "llm",
        "Model reads the tender (Haiku 5.5)",
        "against the client profile: fit, decision,\nreason, conditions, each with a quoted passage",
        h=1.2,
    )
    diamond(ax, MODEL, 11.55, "Model answered?")
    diamond(ax, MODEL, 9.5, "Quoted passage for the\ndecision is in the notice?")
    box(
        ax,
        MODEL,
        7.6,
        "code",
        "Order the list and log it",
        "bid first, then ask a person, not read, skip;\nappended to the prediction log",
        h=1.2,
    )
    diamond(ax, MODEL, 5.7, "Feed checks pass?\n(records parse, feed is fresh,\nvolume is usual)")

    # What the model step leaves when it does not give a decision
    box(
        ax,
        RIGHT,
        15.35,
        "code",
        "Listed without an assessment",
        "keeps the ranker's place; a skipped\nor failed model call is counted",
        w=4.2,
        h=1.2,
    )
    box(
        ax,
        RIGHT,
        9.5,
        "code",
        "Decision becomes ask a person",
        "a skip never rests on a passage\nthat is not in the notice",
        w=4.2,
        h=1.2,
    )

    # Ends that are not a list
    box(
        ax,
        LEFT,
        12.2,
        "code",
        "Removed from the list",
        "counted under the name\nof the first rule broken",
        w=3.4,
        h=1.2,
    )

    # Exits to a person
    box(
        ax,
        LEFT,
        15.9,
        "human",
        "Operator",
        "no list today, the run fails: the\nfeed is down or changed format",
        w=3.4,
        h=1.2,
    )
    box(
        ax,
        SPINE,
        5.7,
        "human",
        "Operator",
        "the run fails, health.json says why: records\nunreadable, feed stale or too few notices",
        w=4.4,
        h=1.2,
    )
    box(
        ax,
        MODEL,
        3.8,
        "human",
        "Bid manager reads the morning list",
        "marks each tender bid or skip, with a reason. The list says where to look first:\n"
        "model says ask a person, quoted passage not found, not read by the model",
        w=8.4,
        h=1.2,
    )

    # Run lane arrows
    arrow(ax, (SPINE, 18.85), (SPINE, 18.3))
    arrow(ax, (SPINE, 17.2), (SPINE, 16.65))
    arrow(ax, (SPINE - 2.2, 15.9), (LEFT + 1.7, 15.9), "no", offset=(0, 0.22))
    arrow(ax, (SPINE, 15.15), (SPINE, 14.6), "yes", offset=(0.35, 0))
    arrow(ax, (SPINE, 13.5), (SPINE, 12.95))
    arrow(ax, (SPINE - 2.2, 12.2), (LEFT + 1.7, 12.2), "yes", offset=(0, 0.22))
    arrow(ax, (SPINE, 11.45), (SPINE, 10.9), "no", offset=(0.3, 0))
    path(
        ax,
        [(SPINE + 2.2, 10.3), (WRAP, 10.3), (WRAP, 17.4), (MODEL - 2.3, 17.4)],
        "each\nlisted\ntender",
        at=1,
        offset=(0, -1.3),
    )

    # Model lane arrows
    arrow(ax, (MODEL, 16.65), (MODEL, 16.1), "yes", offset=(0.35, 0))
    arrow(ax, (MODEL, 14.6), (MODEL, 14.05), "no", offset=(0.3, 0))
    arrow(ax, (MODEL, 12.85), (MODEL, 12.3))
    arrow(ax, (MODEL, 10.8), (MODEL, 10.25), "yes", offset=(0.35, 0))
    arrow(ax, (MODEL, 8.75), (MODEL, 8.2), "yes", offset=(0.35, 0))
    arrow(ax, (MODEL, 7.0), (MODEL, 6.45))
    arrow(ax, (MODEL - 2.3, 5.7), (SPINE + 2.2, 5.7), "no", offset=(0, 0.22))
    arrow(ax, (MODEL, 4.95), (MODEL, 4.4), "yes", offset=(0.35, 0))

    # Tenders the model does not decide on
    path(ax, [(MODEL + 2.3, 17.4), (RIGHT, 17.4), (RIGHT, 15.95)], "no", at=0, offset=(-0.9, 0.22))
    arrow(ax, (MODEL + 2.3, 15.35), (RIGHT - 2.1, 15.35), "yes", offset=(0, 0.22))
    path(
        ax,
        [(MODEL + 2.3, 11.55), (RIGHT - 1.0, 11.55), (RIGHT - 1.0, 14.75)],
        "no",
        at=0,
        offset=(-0.4, 0.22),
    )
    arrow(ax, (MODEL + 2.3, 9.5), (RIGHT - 2.1, 9.5), "no", offset=(0, 0.22))
    # Both rejoin the list before it is ordered.
    path(ax, [(RIGHT + 2.1, 15.35), (EDGE, 15.35), (EDGE, 7.6), (MODEL + 2.3, 7.6)])
    path(ax, [(RIGHT, 8.9), (RIGHT, 7.6), (MODEL + 2.3, 7.6)])

    # Months later: a separate, short flow
    ax.plot([0.2, 19.7], [2.6, 2.6], color="#9E9E9E", linewidth=1.0, linestyle=(0, (5, 4)))
    ax.text(
        0.3,
        2.3,
        "Months later, a separate run: the outcome arrives",
        fontsize=10,
        color="#616161",
        va="center",
    )
    box(
        ax,
        2.3,
        LATER,
        "terminal",
        "Award notice is published",
        "it names a panel supplier as the winner",
        w=4.2,
        h=1.2,
        pill=True,
    )
    box(
        ax,
        6.85,
        LATER,
        "code",
        "Outcome log",
        "who won, and the day\nthe award notice arrived",
        w=3.7,
        h=1.2,
    )
    box(
        ax,
        11.3,
        LATER,
        "code",
        "The list is scored",
        "only if it was written before\nthe award notice arrived",
        w=4.0,
        h=1.2,
    )
    box(
        ax,
        15.7,
        LATER,
        "code",
        "Scoreboard page",
        "each ranker next to\nthe category alert",
        w=3.6,
        h=1.2,
    )
    box(ax, 18.85, LATER, "terminal", "End", w=1.5, h=1.0, pill=True)
    arrow(ax, (4.4, LATER), (5.0, LATER))
    arrow(ax, (8.7, LATER), (9.3, LATER))
    arrow(ax, (13.3, LATER), (13.9, LATER))
    arrow(ax, (17.5, LATER), (18.1, LATER))

    legend(ax, 11.4, 19.35, [("code", "Code"), ("llm", "Paid model call"), ("human", "Person")])
    save(fig, out)


if __name__ == "__main__":
    render()
    print(f"Wrote {OUT}")
