"""Render docs/request-to-system.png: from the client's request to a measured system.

Four columns, read left to right: what the client asked for, the user stories that
came out of discovery, the part of the system that answers each story, and the
number that proves the story is met.

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

TO_BE_FILLED = "to be filled from the evaluation"

# The number shown in the last column for each user story. Write each one as a short
# sentence with the figure first, copied from the evaluation report (or the load test
# and the test run, for stories 3 and 5), and run this script again. Up to about 110
# characters fit: the text is wrapped over at most four lines and shrinks to fit.
# A value left as TO_BE_FILLED is drawn in grey.
NUMBERS = {
    # story 1: what a queue entry carries, and how often the model's slice has it
    "queue": "234 of 1,500 held-out edits read by the model; 135 to 141 of them carry a "
    "category and marked lines",
    # story 2: reverted edits in the top of the queue, beside arrival order and the baseline
    "ranking": "16 to 17 of 74 reverted edits in the top 5% of the queue; arrival order 4, "
    "tier 1 alone 14, Wikimedia's model 17",
    # story 3: seconds to the queue at the stream's rate, and what is skipped when it is faster
    "latency": "7.1 and 7.8 s at the 95th percentile in live runs (target 15); reads are "
    "skipped and counted from 10 times the rate",
    # story 4: cost per 1,000 edits for each tier, and the daily cap
    "cost": "US$0.03 per 1,000 edits with Haiku on the riskiest fifth; tier 1 costs nothing; "
    "cap US$1.00 a day",
    # story 5: writes to a wiki, and account names in the records
    "policy": "0 methods that write to a wiki; 0 fields for an account name in any record",
}

FIG_W, FIG_H = 17.7, 13.0
ROW_TOP, ROW_H, ROW_GAP = 11.1, 1.88, 0.24
PAD = 0.22

# column: (left edge, width, fill, edge, heading)
COLUMNS = {
    "request": (0.4, 3.1, "#F5F5F5", "#616161", "The request, in the\nclient's words"),
    "story": (4.0, 3.85, "#E8F5E9", "#2E7D32", "User stories"),
    "answer": (8.35, 4.2, "#E3F2FD", "#1565C0", "The part of the system\nthat answers it"),
    "proof": (13.05, 4.25, "#FFF8E1", "#F9A825", "The number that\nproves it"),
}

REQUEST = (
    "Thousands of edits an hour. My reviewers see them in the order they arrive, and "
    "most are fine. I want the bad ones at the top of the queue within seconds, I want "
    "the cost per thousand edits, and I do not want a model call on every edit."
)
REQUEST_BY = "Trust and safety lead of a\nplatform that anyone can\nedit (fictional client)"
TODAY = [
    "reviewers read in arrival order",
    "about 88,000 article edits a day by people",
    "5 to 7 in 100 are later reverted",
]
TODAY_NOTE = "Measured on the real stream;\nthe client is fictional"

# (role, story, where it lives in src/editwatch or app, the part of the system, key in NUMBERS)
ROWS = [
    (
        "REVIEWER",
        "My queue is ordered by risk, and each entry shows the change with the risky "
        "part marked and the kind of problem named",
        "tier1.py  tier2.py  app/review_queue.py",
        "Tier 1 orders every edit; the model marks lines and names a category for the "
        "top slice; the page writes the sentence",
        "queue",
    ),
    (
        "TRUST AND SAFETY LEAD",
        "I see how many of the edits later reverted my reviewers would have reached, "
        "next to arrival order and the platform's own model",
        "backtest.py  truth.py  evals.py",
        "Replay of three weeks with a split in time; scored on which edits were "
        "reverted within 48 hours",
        "ranking",
    ),
    (
        "PLATFORM ENGINEER",
        "The system keeps up with the stream inside its latency target, and I see what "
        "it gives up first when it cannot",
        "cascade.py  recorder.py  loadtest.py",
        "A bounded queue between the tiers; a load test on a recorded slice of the stream",
        "latency",
    ),
    (
        "BUDGET OWNER",
        "I see the cost per thousand edits for each tier, under a daily cap",
        "cascade.py  budget.py  report.py",
        "The model reads the riskiest 20%; a daily cap spent evenly across the day",
        "cost",
    ),
    (
        "POLICY OWNER",
        "The system only orders a queue. It never edits, never reverts, and keeps no "
        "record of who made an edit",
        "wiki.py  models.py  scrub.py",
        "Read-only client; no field for an account name in any record",
        "policy",
    ),
]

FOOTNOTE = (
    "Numbers come from the evaluation report, the load test and the live runs. The client is "
    "fictional. The edits, and whether editors reverted them, are real."
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


def extent(ax, drawn):
    """The box around a drawn text, in the units of the axes."""
    renderer = ax.figure.canvas.get_renderer()
    return drawn.get_window_extent(renderer).transformed(ax.transData.inverted())


def fit(ax, text: str, width: float, sizes: tuple[float, ...], max_lines: int, **style):
    """Wrap `text` to at most `max_lines` lines no wider than `width`, as large as fits.

    The width is measured, not guessed from a character count, so a value typed into
    NUMBERS later cannot run over the edge of its box.
    """
    wrapped = text
    for size in sizes:
        for chars in range(max(len(text), 8), 7, -1):
            wrapped = wrap(text, chars)
            probe = ax.text(0, 0, wrapped, fontsize=size, **style)
            measured = extent(ax, probe).width
            probe.remove()
            if measured <= width:
                break
        if measured <= width and wrapped.count("\n") < max_lines:
            return wrapped, size
    return wrapped, sizes[-1]


def render(out: Path = OUT, numbers: dict[str, str] | None = None) -> None:
    numbers = NUMBERS if numbers is None else numbers
    fig, ax = plt.subplots(figsize=(FIG_W, FIG_H), dpi=150)
    ax.set_xlim(0, FIG_W)
    ax.set_ylim(0, FIG_H)
    ax.axis("off")
    title = ax.text(
        0.4, FIG_H - 0.5, "Edit moderation cascade", fontsize=20, fontweight="bold", va="center"
    )
    ax.text(
        extent(ax, title).x1 + 0.45,
        FIG_H - 0.5,
        "from the client's request to a measured system",
        fontsize=13,
        color="#555555",
        va="center",
    )

    for left, _, _, edge, heading in COLUMNS.values():
        # One line at a time, on fixed baselines: a heading of one line sits on the lower one.
        for n, line in enumerate(reversed(heading.split("\n"))):
            ax.text(
                left + 0.05,
                ROW_TOP + 0.22 + n * 0.3,
                line,
                fontsize=12,
                fontweight="bold",
                color=edge,
                va="baseline",
            )

    # Column 1: the request, one box as tall as all the rows.
    left, width, fill, edge, _ = COLUMNS["request"]
    inner = width - 2 * PAD
    bottom = row_top(len(ROWS) - 1) - ROW_H
    box(ax, left, ROW_TOP, width, ROW_TOP - bottom, fill, edge)
    ax.text(
        left + PAD, ROW_TOP - 0.25, "“", fontsize=40, color="#9E9E9E", va="top", fontweight="bold"
    )
    quote_style = dict(style="italic", linespacing=1.45)
    quote, _ = fit(ax, REQUEST, inner, (11.5,), 99, **quote_style)
    drawn = ax.text(
        left + PAD,
        ROW_TOP - 0.9,
        quote,
        fontsize=11.5,
        color="#1A1A1A",
        va="top",
        **quote_style,
    )
    ax.text(
        left + PAD,
        extent(ax, drawn).y0 - 0.4,
        REQUEST_BY,
        fontsize=9.5,
        color="#555555",
        va="top",
        linespacing=1.4,
    )

    # The "Today" block is built upwards from the bottom of the box, so a longer list
    # moves the rule up and never runs out of the box.
    note = ax.text(
        left + PAD,
        bottom + 0.3,
        TODAY_NOTE,
        fontsize=8.5,
        color="#777777",
        va="bottom",
        linespacing=1.35,
    )
    cursor = extent(ax, note).y1 + 0.22
    today_style = dict(linespacing=1.3)
    for item in reversed(TODAY):
        text, _ = fit(ax, item, inner, (10,), 99, **today_style)
        drawn = ax.text(
            left + PAD, cursor, text, fontsize=10, color="#333333", va="bottom", **today_style
        )
        cursor = extent(ax, drawn).y1 + 0.13
    heading = ax.text(
        left + PAD,
        cursor + 0.06,
        "Today",
        fontsize=10.5,
        fontweight="bold",
        color="#1A1A1A",
        va="bottom",
    )
    rule = extent(ax, heading).y1 + 0.18
    ax.plot([left + PAD, left + width - PAD], [rule] * 2, color="#BDBDBD", linewidth=1)

    s_left, s_width, s_fill, s_edge, _ = COLUMNS["story"]
    a_left, a_width, a_fill, a_edge, _ = COLUMNS["answer"]
    p_left, p_width, p_fill, p_edge, _ = COLUMNS["proof"]
    for i, (role, story, modules, answer, key) in enumerate(ROWS):
        top = row_top(i)
        mid = top - ROW_H / 2

        box(ax, s_left, top, s_width, ROW_H, s_fill, s_edge)
        ax.text(
            s_left + PAD, top - 0.32, role, fontsize=9, fontweight="bold", color=s_edge, va="center"
        )
        ax.text(
            s_left + PAD,
            top - 0.57,
            wrap(story, 35),
            fontsize=10,
            color="#1A1A1A",
            va="top",
            linespacing=1.35,
        )

        box(ax, a_left, top, a_width, ROW_H, a_fill, a_edge)
        ax.text(
            a_left + PAD,
            top - 0.32,
            modules,
            fontsize=9,
            family="monospace",
            color=a_edge,
            va="center",
        )
        ax.text(
            a_left + PAD,
            top - 0.57,
            wrap(answer, 38),
            fontsize=10,
            color="#1A1A1A",
            va="top",
            linespacing=1.35,
        )

        box(ax, p_left, top, p_width, ROW_H, p_fill, p_edge)
        number = numbers[key]
        pending = number == TO_BE_FILLED
        # parse_math is off so that two dollar signs in one value ("US$0.42 ... US$0.50")
        # are drawn as typed, not read as a formula.
        style = dict(
            fontweight="bold",
            style="italic" if pending else "normal",
            linespacing=1.3,
            parse_math=False,
        )
        # A bare figure is drawn large; a sentence starts smaller so it reads as text.
        sizes = (
            (17, 15, 13, 12, 11, 10.5, 10, 9.5) if len(number) <= 18 else (12, 11, 10.5, 10, 9.5, 9)
        )
        text, size = fit(ax, number, p_width - 2 * PAD, sizes, 4, **style)
        ax.text(
            p_left + PAD,
            mid,
            text,
            fontsize=size,
            color="#9E9E9E" if pending else "#1A1A1A",
            va="center",
            **style,
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
