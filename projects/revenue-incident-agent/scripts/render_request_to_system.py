"""Render docs/request-to-system.png: from the client's request to a measured system.

Four columns, read left to right: what the client asked for, the user stories that
came out of discovery, the part of the system that answers each story, and the
evaluation number that shows how far the story is met.

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
    "We find out on Monday that something broke on Saturday. I want to know within hours, "
    "I want to know what it was, and I don't want a robot switching things off in my shop."
)
REQUEST_BY = "Head of e-commerce,\nFernhill Home\n(fictional customer)"
TODAY = ["one engineer on call", "about 15 changes a day", "incidents found hours to days late"]

# (role, story, answer title, answer detail, proof number, proof label)
ROWS = [
    (
        "ON-CALL ENGINEER",
        "I want to hear about an incident within hours and be told where it is, so that I call the right team first.",
        "A forecast over 706 slices",
        "Five metrics, every slice, each hour. Grouping names one slice per incident.",
        "19 of 21",
        "incidents found, each on the exact slice; 5 of 7 held-out",
    ),
    (
        "ON-CALL ENGINEER",
        "I want the pager quiet during planned sales and holidays, so that I still look when it goes off.",
        "The calendar is in the forecast",
        "False-alarm control per metric. A check closes what a planned event explains.",
        "1 in 24",
        "weeks had a false page; 18 false alerts in the queue",
    ),
    (
        "PAYMENTS, MARKETING, PRICING",
        "I want the likely cause with the numbers behind it, so that I can check it in a minute.",
        "Numbers recomputed by code",
        "Read-only tools. Code runs each cited tool call again before a person reads it.",
        "391 of 391",
        "cited values recompute; right cause: agent 18 of 19, script 16",
    ),
    (
        "HEAD OF E-COMMERCE",
        "I want nothing switched off without a person saying yes, and never the wrong thing.",
        "Checks, approval, one call",
        "Reversible actions from a closed list. The model has no tool that writes.",
        "0 of 39",
        "wrong agent proposals passed checks; 1 was held; script: 1",
    ),
    (
        "HEAD OF E-COMMERCE",
        "I want to see in numbers how good it is before anyone relies on it.",
        "A baseline and a held-out world",
        "Three detectors and two investigators, on scenarios written after they were frozen.",
        "5 of 7",
        "held-out incidents found; 14 of 14 where it was tuned",
    ),
]  # fmt: skip

FOOTNOTE = (
    "Numbers: gradient-boosted detector and the agent (one recorded run, Claude Haiku 5.5) on two synthetic "
    "worlds, 21 incidents and 9 look-alikes. The sets are small, so read counts, not rates.\n"
    "Two repeat runs on the same model named the right cause for 19 of 19. One made an unsafe proposal that passed "
    "every check; a check added since stops it when the run is replayed."
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


def main(out: Path = OUT) -> None:
    fig, ax = plt.subplots(figsize=(FIG_W, FIG_H), dpi=150)
    ax.set_xlim(0, FIG_W)
    ax.set_ylim(0, FIG_H)
    ax.axis("off")
    ax.text(0.4, 9.8, "Revenue incident agent", fontsize=20, fontweight="bold", va="center")
    ax.text(5.75, 9.8, "from the client's request to a measured system",
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
    ax.text(left + pad, ROW_TOP - 4.05, REQUEST_BY, fontsize=9.5, color="#555555", va="top",
            linespacing=1.4)  # fmt: skip
    ax.plot([left + pad, left + width - pad], [ROW_TOP - 5.05] * 2, color="#BDBDBD", linewidth=1)
    ax.text(left + pad, ROW_TOP - 5.25, "Today", fontsize=10.5, fontweight="bold",
            color="#1A1A1A", va="top")  # fmt: skip
    ax.text(left + pad, ROW_TOP - 5.6, "\n".join(wrap(line, 28) for line in TODAY), fontsize=10,
            color="#333333", va="top", linespacing=1.5)  # fmt: skip
    ax.text(left + pad, ROW_TOP - 7.05, "Assumed figures for the exercise", fontsize=8.5,
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

    ax.text(0.4, 0.36, FOOTNOTE, fontsize=9, color="#666666", va="center", linespacing=1.5)

    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, facecolor="white", bbox_inches="tight", pad_inches=0.25)
    plt.close(fig)
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
