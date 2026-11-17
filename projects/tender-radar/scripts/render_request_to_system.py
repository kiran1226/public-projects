"""Render docs/request-to-system.png: from the client's request to a measured system.

Four columns, read left to right: what the client asked for, the user stories that
came out of discovery, the part of the system that answers each story, and the
evaluation number that shows the story is met.

    uv run python scripts/render_request_to_system.py
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "docs" / "request-to-system.png"

TO_BE_MEASURED = "to be measured"

# The number shown in the last column for each user story. Copy each value from
# docs/eval-report.md (or the test run, for stories 4 and 5) and run this script again.
# A value left as TO_BE_MEASURED is drawn in grey.
NUMBERS = {
    "shortlist": "197 of 270",  # story 1: fused ranker, held-out month
    "conditions": "14 of 1,770",  # story 2: conditions dropped by the gate
    "beats_alert": "73% against 49%",  # story 3: fused against alert, held-out month
    "decisions": "16 tests",  # story 4: tests/test_decisions.py
    "operations": "US$0.006",  # story 5: the first live day
}

# What each number counts. Change the wording here if a different figure is reported.
# Two lines fit under a number: keep a label to about 60 characters.
NUMBER_LABELS = {
    "shortlist": "tenders later won that the list had in its first three places",
    "conditions": "quoted conditions the gate dropped as not in the notice",
    "beats_alert": "in the first three places, list against alert, held-out month",
    "decisions": "of the decision log pass: bid, skip, reason, time",
    "operations": "for the client's list on the first live day; cap US$0.50",
}

FIG_W, FIG_H = 17.7, 10.3
ROW_TOP, ROW_H, ROW_GAP = 8.6, 1.38, 0.24

# column: (left edge, width, fill, edge, heading)
COLUMNS = {
    "request": (0.4, 3.1, "#F5F5F5", "#616161", "Client request"),
    "story": (4.1, 4.3, "#E8F5E9", "#2E7D32", "User stories"),
    "answer": (9.0, 4.3, "#E3F2FD", "#1565C0", "Answer: what the system does"),
    "proof": (13.9, 3.4, "#FFF8E1", "#F9A825", "Proof: what was measured"),
}

REQUEST = (
    "We find tenders through category alerts. We get two hundred a day, read ten, and "
    "hear about the ones we should have bid on when a competitor wins them. I want a "
    "short list every morning, with the deadline and what would rule us out."
)
REQUEST_BY = "Sales director,\nTarnwell Lab Supply\n(fictional client)"
TODAY = ["bid desk of 3 people", "about 200 notices a day", "about 10 of them read"]

# (role, story, answer title, answer detail, key in NUMBERS)
ROWS = [
    (
        "BID MANAGER",
        "I get a ranked list each morning of at most 15 new tenders that fit what we sell, each with its reason.",
        "A ranked list of 15",
        "Hard rules cut what cannot be bid. Rankers order the rest; the model gives a reason.",
        "shortlist",
    ),
    (
        "BID MANAGER",
        "For each tender I see the deadline, the value and the conditions we must meet, each tied to its place in the notice.",
        "Quotes checked against the notice",
        "Each condition is shown with its passage. Deadline and value are the notice's own.",
        "conditions",
    ),
    (
        "SALES DIRECTOR",
        "I see that the list beats our category alerts, measured on tenders that real suppliers went on to win.",
        "A backtest on who won",
        "Lists for 200 real suppliers, scored on the tenders each supplier went on to win.",
        "beats_alert",
    ),
    (
        "BID MANAGER",
        "I mark each tender bid or skip with a reason, and those decisions are kept.",
        "Bid and skip on the morning list",
        "Each decision is appended to a log with its reason and time. A skip needs a reason.",
        "decisions",
    ),
    (
        "OWNER OF THE SERVICE",
        "I know when the feed broke, when a run was missed and what yesterday cost.",
        "Feed checks and a spend cap",
        "A broken feed fails the run. The cap is checked before every model call.",
        "operations",
    ),
]

FOOTNOTE = (
    "Numbers come from docs/eval-report.md and the test run. The client is fictional. "
    "The notices, the awards and the suppliers used for scoring are real."
)


def row_top(i: int) -> float:
    return ROW_TOP - i * (ROW_H + ROW_GAP)


def box(ax, left: float, top: float, width: float, height: float, fill: str, edge: str) -> None:
    ax.add_patch(
        FancyBboxPatch(
            (left, top - height),
            width,
            height,
            boxstyle="round,pad=0,rounding_size=0.14",
            facecolor=fill,
            edgecolor=edge,
            linewidth=1.8,
        )
    )


def arrow(ax, x0: float, x1: float, y: float) -> None:
    ax.add_patch(
        FancyArrowPatch(
            (x0, y),
            (x1, y),
            arrowstyle="-|>",
            mutation_scale=16,
            linewidth=1.6,
            color="#333333",
            shrinkA=3,
            shrinkB=3,
        )
    )


def wrap(text: str, width: int) -> str:
    return "\n".join(textwrap.wrap(text, width))


def render(out: Path = OUT) -> None:
    fig, ax = plt.subplots(figsize=(FIG_W, FIG_H), dpi=150)
    ax.set_xlim(0, FIG_W)
    ax.set_ylim(0, FIG_H)
    ax.axis("off")
    ax.text(0.4, 9.8, "Tender radar", fontsize=20, fontweight="bold", va="center")
    ax.text(
        3.45,
        9.8,
        "from the client's request to a measured system",
        fontsize=13,
        color="#555555",
        va="center",
    )

    for left, _, _, edge, heading in COLUMNS.values():
        ax.text(left + 0.05, 9.0, heading, fontsize=12, fontweight="bold", color=edge, va="center")

    # Column 1: the request, one box as tall as all the rows.
    left, width, fill, edge, _ = COLUMNS["request"]
    bottom = row_top(len(ROWS) - 1) - ROW_H
    box(ax, left, ROW_TOP, width, ROW_TOP - bottom, fill, edge)
    pad = 0.22
    ax.text(
        left + pad, ROW_TOP - 0.25, "“", fontsize=40, color="#9E9E9E", va="top", fontweight="bold"
    )
    ax.text(
        left + pad,
        ROW_TOP - 0.9,
        wrap(REQUEST, 24),
        fontsize=11.5,
        style="italic",
        color="#1A1A1A",
        va="top",
        linespacing=1.45,
    )
    ax.text(
        left + pad,
        ROW_TOP - 4.8,
        REQUEST_BY,
        fontsize=9.5,
        color="#555555",
        va="top",
        linespacing=1.4,
    )
    ax.plot([left + pad, left + width - pad], [ROW_TOP - 5.9] * 2, color="#BDBDBD", linewidth=1)
    ax.text(
        left + pad,
        ROW_TOP - 6.08,
        "Today",
        fontsize=10.5,
        fontweight="bold",
        color="#1A1A1A",
        va="top",
    )
    ax.text(
        left + pad,
        ROW_TOP - 6.43,
        "\n".join(TODAY),
        fontsize=10,
        color="#333333",
        va="top",
        linespacing=1.5,
    )
    ax.text(
        left + pad,
        ROW_TOP - 7.33,
        "Figures written for the exercise",
        fontsize=8.5,
        color="#777777",
        va="top",
    )

    s_left, s_width, s_fill, s_edge, _ = COLUMNS["story"]
    a_left, a_width, a_fill, a_edge, _ = COLUMNS["answer"]
    p_left, p_width, p_fill, p_edge, _ = COLUMNS["proof"]
    for i, (role, story, a_title, a_detail, key) in enumerate(ROWS):
        top = row_top(i)
        mid = top - ROW_H / 2

        box(ax, s_left, top, s_width, ROW_H, s_fill, s_edge)
        ax.text(
            s_left + pad, top - 0.27, role, fontsize=9, fontweight="bold", color=s_edge, va="center"
        )
        ax.text(
            s_left + pad,
            top - 0.5,
            wrap(story, 41),
            fontsize=10,
            color="#1A1A1A",
            va="top",
            linespacing=1.35,
        )

        box(ax, a_left, top, a_width, ROW_H, a_fill, a_edge)
        ax.text(
            a_left + pad,
            top - 0.33,
            a_title,
            fontsize=11.5,
            fontweight="bold",
            color="#1A1A1A",
            va="center",
        )
        ax.text(
            a_left + pad,
            top - 0.62,
            wrap(a_detail, 43),
            fontsize=10,
            color="#333333",
            va="top",
            linespacing=1.35,
        )

        box(ax, p_left, top, p_width, ROW_H, p_fill, p_edge)
        number = NUMBERS[key]
        pending = number == TO_BE_MEASURED
        ax.text(p_left + pad, top - 0.4, number, fontsize=14 if pending else 17, fontweight="bold",
                color="#9E9E9E" if pending else "#1A1A1A", va="center", style="italic" if pending else "normal")  # fmt: skip
        ax.text(
            p_left + pad,
            top - 0.76,
            wrap(NUMBER_LABELS[key], 32),
            fontsize=10,
            color="#333333",
            va="top",
            linespacing=1.35,
        )

        arrow(ax, left + width, s_left, mid)
        arrow(ax, s_left + s_width, a_left, mid)
        arrow(ax, a_left + a_width, p_left, mid)

    ax.text(0.4, 0.42, FOOTNOTE, fontsize=9, color="#666666", va="center")

    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, facecolor="white", bbox_inches="tight", pad_inches=0.25)
    plt.close(fig)


if __name__ == "__main__":
    render()
    print(f"Wrote {OUT}")
