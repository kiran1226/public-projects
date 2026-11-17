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

FIG_W, FIG_H = 17.7, 10.3
ROW_TOP, ROW_H, ROW_GAP = 8.6, 1.38, 0.24
PENDING = "pending"

# column: (left edge, width, fill, edge, heading)
COLUMNS = {
    "request": (0.4, 3.1, "#F5F5F5", "#616161", "Client request"),
    "story": (4.1, 4.3, "#E8F5E9", "#2E7D32", "User stories"),
    "answer": (9.0, 4.3, "#E3F2FD", "#1565C0", "Answer: what the system does"),
    "proof": (13.9, 3.4, "#FFF8E1", "#F9A825", "Proof: what was measured"),
}

REQUEST = (
    "My analysts spend the day clearing alerts that turn out to be nothing, while real "
    "cases wait in the same queue. Prepare the routine ones for them. But no customer "
    "data leaves the bank, and no machine closes a case on its own."
)
REQUEST_BY = "Head of financial crime,\nHarbourline Bank\n(fictional customer)"
TODAY = ["8 level-1 analysts", "about 220 alerts a day", "20 to 40 minutes each"]

# (role, story, answer title, answer detail, proof number, proof label)
# Proof numbers are copied from docs/eval-report.md. PENDING marks a number that needs
# a recorded model run.
ROWS = [
    (
        "LEVEL-1 ANALYST",
        "I want the evidence gathered and a draft recommendation per alert, so that I review a case instead of four systems.",
        "An agent that investigates",
        "Gathers profile, activity, screening and history, then proposes an outcome.",
        "40 of 40",
        "alerts with the right outcome, routed configuration",
    ),
    (
        "HEAD OF FINANCIAL CRIME",
        "I want no suspicious alert closed by the system, so that speed never costs us a missed case.",
        "Escalating is the safe default",
        "Hard flags skip tier one. Only a confident close stands; a failure never closes.",
        "0 of 9",
        "suspicious alerts closed by the system (target: none)",
    ),
    (
        "DATA PROTECTION OFFICER",
        "I want no real customer identifier to reach an external model, so that using AI does not widen our exposure.",
        "A privacy gateway",
        "Models see case-scoped tokens. Free text is scrubbed and every request is scanned.",
        "188 of 188",
        "identifier-bearing text fields clean after scrubbing",
    ),
    (
        "LEVEL-2 INVESTIGATOR",
        "I want every claim tied to evidence I can open, so that I can trust an escalation without redoing it.",
        "Citations the system verifies",
        "A finding may cite only tokens issued in its own case. Anything else is rejected.",
        "100%",
        "cited evidence that was really retrieved in the case",
    ),
    (
        "OPERATIONS LEAD",
        "I want the expensive model used only where a case needs it, so that cost per alert stays predictable.",
        "Two tiers and a spend cap",
        "Haiku triages, Opus leads hard cases, and a ledger stops a run at the cap.",
        "$0.03",
        "per alert, routed. Opus alone did as well for the same cost",
    ),
]

FOOTNOTE = (
    "Numbers come from docs/eval-report.md: 40 synthetic alerts, 28 of them used to tune the "
    "prompts, one recorded run per configuration. The privacy number is measured on the whole dataset."
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
    ax.text(0.4, 9.8, "AML alert investigation agent", fontsize=20, fontweight="bold", va="center")
    ax.text(
        7.25,
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
        left + pad, ROW_TOP - 0.3, "“", fontsize=40, color="#9E9E9E", va="top", fontweight="bold"
    )
    ax.text(
        left + pad,
        ROW_TOP - 1.0,
        wrap(REQUEST, 22),
        fontsize=12,
        style="italic",
        color="#1A1A1A",
        va="top",
        linespacing=1.45,
    )
    ax.text(
        left + pad,
        ROW_TOP - 4.75,
        REQUEST_BY,
        fontsize=9.5,
        color="#555555",
        va="top",
        linespacing=1.4,
    )
    ax.plot([left + pad, left + width - pad], [ROW_TOP - 5.75] * 2, color="#BDBDBD", linewidth=1)
    ax.text(
        left + pad,
        ROW_TOP - 5.95,
        "Today",
        fontsize=10.5,
        fontweight="bold",
        color="#1A1A1A",
        va="top",
    )
    ax.text(
        left + pad,
        ROW_TOP - 6.3,
        "\n".join(TODAY),
        fontsize=10,
        color="#333333",
        va="top",
        linespacing=1.5,
    )
    ax.text(
        left + pad,
        ROW_TOP - 7.2,
        "Assumed figures for the exercise",
        fontsize=8.5,
        color="#777777",
        va="top",
    )

    s_left, s_width, s_fill, s_edge, _ = COLUMNS["story"]
    a_left, a_width, a_fill, a_edge, _ = COLUMNS["answer"]
    p_left, p_width, p_fill, p_edge, _ = COLUMNS["proof"]
    for i, (role, story, a_title, a_detail, number, label) in enumerate(ROWS):
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
            wrap(a_detail, 44),
            fontsize=10,
            color="#333333",
            va="top",
            linespacing=1.35,
        )

        box(ax, p_left, top, p_width, ROW_H, p_fill, p_edge)
        pending = number == PENDING
        ax.text(p_left + pad, top - 0.4, number, fontsize=14 if pending else 17, fontweight="bold",
                color="#9E9E9E" if pending else "#1A1A1A", va="center", style="italic" if pending else "normal")  # fmt: skip
        ax.text(
            p_left + pad,
            top - 0.76,
            wrap(label, 32),
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
