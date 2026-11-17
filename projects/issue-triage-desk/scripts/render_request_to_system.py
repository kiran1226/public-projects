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
# sentence with the figure first, copied from the evaluation report (or the test run,
# for the story about shadow mode), and run this script again. Up to about 90
# characters fit: the text is wrapped over at most three lines and shrinks to fit.
# A value left as TO_BE_FILLED is drawn in grey.
NUMBERS = {
    # story 1: how often the proposed area is right, beside the two baselines
    "labels": "Right area on 412 to 419 of 638 held-out issues; classifier 347, keyword rules 206",
    # story 2: duplicates against the maintainers' record, and what a reading of the calls shows
    "duplicates": "Agrees with the maintainers on 11 of 28 duplicates; 18 of its 20 other sure calls are the same defect when read",
    # story 3: the held-out window, and the live log so far
    "held_out": "754 issues opened after training, run three times; live log since 17 Nov 2026, no outcomes yet",
    # story 4: the write path
    "shadow": "0 writes to the tracker; apply mode is tested against a fake tracker and was never run on GitHub",
    # story 5: checks, cost, cap
    "operations": "4 of 4 feed checks pass; US$0.89 per 1,000 issues; cap US$0.50 a day",
}

FIG_W, FIG_H = 17.7, 11.6
ROW_TOP, ROW_H, ROW_GAP = 9.7, 1.6, 0.24
PAD = 0.22

# column: (left edge, width, fill, edge, heading)
COLUMNS = {
    "request": (0.4, 3.1, "#F5F5F5", "#616161", "The request, in the\nclient's words"),
    "story": (4.0, 3.85, "#E8F5E9", "#2E7D32", "User stories"),
    "answer": (8.35, 4.2, "#E3F2FD", "#1565C0", "The part of the system\nthat answers it"),
    "proof": (13.05, 4.25, "#FFF8E1", "#F9A825", "The number that\nproves it"),
}

REQUEST = (
    "Every new ticket waits for one of five people to read it, label it and check "
    "whether we already have it. A good share are duplicates. I want that first pass "
    "done in a minute, and I want to know how often it is wrong before it touches "
    "the tracker."
)
REQUEST_BY = "Support lead of a team that\nmaintains an open-source\nproduct (fictional client)"
TODAY = ["5 people share the reading", "about 12 new issues a day", "about 4.5 minutes for each"]
TODAY_NOTE = "Volume measured on a real public\ntracker; the rest is assumed"

# (role, story, where it lives in src/triagedesk, the part of the system, key in NUMBERS)
ROWS = [
    (
        "TRIAGER",
        "A new issue arrives with proposed area and platform labels and a confidence for each",
        "classifier.py  triage.py  gate.py",
        "Classifier and model propose labels; the gate keeps only labels of the taxonomy",
        "labels",
    ),
    (
        "TRIAGER",
        "A likely duplicate arrives with up to three earlier issues and the passage in each that matches",
        "duplicates.py  triage.py  queue.py",
        "Word and vector search over earlier issues; the model picks one of eight or none; passages computed in code",
        "duplicates",
    ),
    (
        "SUPPORT LEAD",
        "Numbers on issues that arrived after the system was frozen, scored against what the maintainers did",
        "backtest.py  live.py  evals.py",
        "Replay with a cut in time, a held-out window, then the live log",
        "held_out",
    ),
    (
        "SUPPORT LEAD",
        "The system writes nothing to a tracker unless that tracker is named in its configuration",
        "act.py",
        "Shadow mode by default; apply mode needs the allow-list",
        "shadow",
    ),
    (
        "OWNER OF THE SERVICE",
        "I know when the feed broke, when a run was missed and what yesterday cost",
        "health.py  budget.py  scoreboard.py",
        "Four feed checks, a daily spend cap, a scoreboard",
        "operations",
    ),
]

FOOTNOTE = (
    "Numbers come from the evaluation report and the test run. The client is fictional. "
    "The issues, and what their maintainers did with them, are real."
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


def fit(ax, text: str, width: float, sizes: tuple[float, ...], max_lines: int, **style):
    """Wrap `text` to at most `max_lines` lines no wider than `width`, as large as fits.

    The width is measured, not guessed from a character count, so a value typed into
    NUMBERS later cannot run over the edge of its box.
    """
    renderer = ax.figure.canvas.get_renderer()
    wrapped = text
    for size in sizes:
        for chars in range(max(len(text), 8), 7, -1):
            wrapped = wrap(text, chars)
            probe = ax.text(0, 0, wrapped, fontsize=size, **style)
            extent = probe.get_window_extent(renderer).transformed(ax.transData.inverted())
            probe.remove()
            if extent.width <= width:
                break
        if extent.width <= width and wrapped.count("\n") < max_lines:
            return wrapped, size
    return wrapped, sizes[-1]


def render(out: Path = OUT) -> None:
    fig, ax = plt.subplots(figsize=(FIG_W, FIG_H), dpi=150)
    ax.set_xlim(0, FIG_W)
    ax.set_ylim(0, FIG_H)
    ax.axis("off")
    ax.text(0.4, FIG_H - 0.5, "Issue triage desk", fontsize=20, fontweight="bold", va="center")
    ax.text(
        4.45,
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
    bottom = row_top(len(ROWS) - 1) - ROW_H
    box(ax, left, ROW_TOP, width, ROW_TOP - bottom, fill, edge)
    ax.text(
        left + PAD, ROW_TOP - 0.25, "“", fontsize=40, color="#9E9E9E", va="top", fontweight="bold"
    )
    quote_style = dict(style="italic", linespacing=1.45)
    quote, _ = fit(ax, REQUEST, width - 2 * PAD, (11.5,), 99, **quote_style)
    drawn = ax.text(
        left + PAD,
        ROW_TOP - 0.9,
        quote,
        fontsize=11.5,
        color="#1A1A1A",
        va="top",
        **quote_style,
    )
    renderer = fig.canvas.get_renderer()
    quote_bottom = drawn.get_window_extent(renderer).transformed(ax.transData.inverted()).y0
    ax.text(
        left + PAD,
        quote_bottom - 0.4,
        REQUEST_BY,
        fontsize=9.5,
        color="#555555",
        va="top",
        linespacing=1.4,
    )
    ax.plot([left + PAD, left + width - PAD], [bottom + 2.25] * 2, color="#BDBDBD", linewidth=1)
    ax.text(
        left + PAD,
        bottom + 2.07,
        "Today",
        fontsize=10.5,
        fontweight="bold",
        color="#1A1A1A",
        va="top",
    )
    ax.text(
        left + PAD,
        bottom + 1.72,
        "\n".join(TODAY),
        fontsize=10,
        color="#333333",
        va="top",
        linespacing=1.5,
    )
    ax.text(
        left + PAD,
        bottom + 0.75,
        TODAY_NOTE,
        fontsize=8.5,
        color="#777777",
        va="top",
        linespacing=1.35,
    )

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
        number = NUMBERS[key]
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
            (17, 15, 13, 12, 11, 10.5, 10, 9.5) if len(number) <= 18 else (12, 11, 10.5, 10, 9.5)
        )
        text, size = fit(ax, number, p_width - 2 * PAD, sizes, 3, **style)
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
