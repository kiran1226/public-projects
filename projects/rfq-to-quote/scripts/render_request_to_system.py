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
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

OUT = Path(__file__).resolve().parents[1] / "docs" / "request-to-system.png"

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
    "My reps spend the day retyping requests into quotes. I want them checking "
    "quotes, not building them. And nothing wrong may go out because a machine guessed."
)
REQUEST_BY = "Head of sales,\nHelios Industrial Supply\n(fictional customer)"
TODAY = ["6 inside-sales reps", "about 60 requests a day", "about 12 minutes each"]

# (role, story, answer title, answer detail, proof number, proof label)
ROWS = [
    (
        "SALES REP",
        "I want a priced draft waiting for each request, so that I check a quote instead of typing it.",
        "A draft quote in five steps",
        "Reads the email, PDF or spreadsheet, names each product and prices it in code.",
        "40 of 40",
        "requests with every line exactly right",
    ),
    (
        "SALES REP",
        "I want the system to say when it does not know a product, so that I can trust the matches it makes.",
        "Declining is a valid answer",
        "The matcher may return no product. Only a confident match can pass without review.",
        "101 of 101",
        "products matched correctly, none wrong with high confidence",
    ),
    (
        "PRICING LEAD",
        "I want prices and sign-off limits as rules I can read, so that no model decides money.",
        "Money stays in plain code",
        "The model never sees a price. Every threshold lives in one policy object.",
        "19 of 19",
        "quotes that policy says to hold reached a person",
    ),
    (
        "REVIEWER",
        "I want every held quote to show why it was held, so that I can fix it in minutes.",
        "A review queue with reasons",
        "21 named rules, all checked every time. The source sits beside the draft.",
        "2 of 21",
        "clean quotes were held without need",
    ),
    (
        "HEAD OF SALES",
        "I want to see in numbers how often it is right, so that I can decide when it may send on its own.",
        "One command scores the system",
        "Labelled requests, a rules-only baseline, and a rollout that starts fully reviewed.",
        "0 of 19",
        "auto-approvals were unsafe; the rules-only baseline had 6",
    ),
]  # fmt: skip

FOOTNOTE = (
    "Numbers: default configuration (Claude Haiku 5.5) on 40 synthetic, hand-labelled requests, "
    "development and held-out sets combined. The sets are small, so read counts, not rates."
)


def row_top(i: int) -> float:
    return ROW_TOP - i * (ROW_H + ROW_GAP)


def box(ax, left: float, top: float, width: float, height: float, fill: str, edge: str) -> None:
    ax.add_patch(FancyBboxPatch(
        (left, top - height), width, height, boxstyle="round,pad=0,rounding_size=0.14",
        facecolor=fill, edgecolor=edge, linewidth=1.8))  # fmt: skip


def arrow(ax, x0: float, x1: float, y: float) -> None:
    ax.add_patch(FancyArrowPatch(
        (x0, y), (x1, y), arrowstyle="-|>", mutation_scale=16, linewidth=1.6,
        color="#333333", shrinkA=3, shrinkB=3))  # fmt: skip


def wrap(text: str, width: int) -> str:
    return "\n".join(textwrap.wrap(text, width))


def main() -> None:
    fig, ax = plt.subplots(figsize=(FIG_W, FIG_H), dpi=150)
    ax.set_xlim(0, FIG_W)
    ax.set_ylim(0, FIG_H)
    ax.axis("off")
    ax.text(0.4, 9.8, "RFQ-to-quote agent", fontsize=20, fontweight="bold", va="center")
    ax.text(4.95, 9.8, "from the client's request to a measured system",
            fontsize=13, color="#555555", va="center")  # fmt: skip

    for left, _, _, edge, heading in COLUMNS.values():
        ax.text(left + 0.05, 9.0, heading, fontsize=12, fontweight="bold", color=edge, va="center")

    # Column 1: the request, one box as tall as all the rows.
    left, width, fill, edge, _ = COLUMNS["request"]
    bottom = row_top(len(ROWS) - 1) - ROW_H
    box(ax, left, ROW_TOP, width, ROW_TOP - bottom, fill, edge)
    pad = 0.22
    ax.text(left + pad, ROW_TOP - 0.3, "“", fontsize=40, color="#9E9E9E", va="top",
            fontweight="bold")  # fmt: skip
    ax.text(left + pad, ROW_TOP - 1.0, wrap(REQUEST, 22), fontsize=12, style="italic",
            color="#1A1A1A", va="top", linespacing=1.45)  # fmt: skip
    ax.text(left + pad, ROW_TOP - 3.75, REQUEST_BY, fontsize=9.5, color="#555555", va="top",
            linespacing=1.4)  # fmt: skip
    ax.plot([left + pad, left + width - pad], [ROW_TOP - 4.95] * 2, color="#BDBDBD", linewidth=1)
    ax.text(left + pad, ROW_TOP - 5.2, "Today", fontsize=10.5, fontweight="bold",
            color="#1A1A1A", va="top")  # fmt: skip
    ax.text(left + pad, ROW_TOP - 5.55, "\n".join(TODAY), fontsize=10, color="#333333",
            va="top", linespacing=1.5)  # fmt: skip
    ax.text(left + pad, ROW_TOP - 6.45, "Assumed figures for the exercise", fontsize=8.5,
            color="#777777", va="top")  # fmt: skip

    s_left, s_width, s_fill, s_edge, _ = COLUMNS["story"]
    a_left, a_width, a_fill, a_edge, _ = COLUMNS["answer"]
    p_left, p_width, p_fill, p_edge, _ = COLUMNS["proof"]
    for i, (role, story, a_title, a_detail, number, label) in enumerate(ROWS):
        top = row_top(i)
        mid = top - ROW_H / 2

        box(ax, s_left, top, s_width, ROW_H, s_fill, s_edge)
        ax.text(s_left + pad, top - 0.27, role, fontsize=9, fontweight="bold", color=s_edge,
                va="center")  # fmt: skip
        ax.text(s_left + pad, top - 0.5, wrap(story, 40), fontsize=10.5, color="#1A1A1A",
                va="top", linespacing=1.35)  # fmt: skip

        box(ax, a_left, top, a_width, ROW_H, a_fill, a_edge)
        ax.text(a_left + pad, top - 0.33, a_title, fontsize=11.5, fontweight="bold",
                color="#1A1A1A", va="center")  # fmt: skip
        ax.text(a_left + pad, top - 0.62, wrap(a_detail, 41), fontsize=10, color="#333333",
                va="top", linespacing=1.35)  # fmt: skip

        box(ax, p_left, top, p_width, ROW_H, p_fill, p_edge)
        ax.text(p_left + pad, top - 0.4, number, fontsize=17, fontweight="bold",
                color="#1A1A1A", va="center")  # fmt: skip
        ax.text(p_left + pad, top - 0.76, wrap(label, 32), fontsize=10, color="#333333",
                va="top", linespacing=1.35)  # fmt: skip

        arrow(ax, left + width, s_left, mid)
        arrow(ax, s_left + s_width, a_left, mid)
        arrow(ax, a_left + a_width, p_left, mid)

    ax.text(0.4, 0.42, FOOTNOTE, fontsize=9, color="#666666", va="center")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, facecolor="white", bbox_inches="tight", pad_inches=0.25)
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
