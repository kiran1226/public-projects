"""Render docs/agent-flowchart.png: the decision flow for one request.

The companion diagram (render_flow.py) shows the components; this one shows the
decisions: every branch that sends a request to a person, and why.

    uv run python scripts/render_flowchart.py
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Polygon

OUT = Path(__file__).resolve().parents[1] / "docs" / "agent-flowchart.png"

STYLE = {
    "terminal": ("#F5F5F5", "#616161"),
    "code": ("#E3F2FD", "#1565C0"),
    "llm": ("#EDE7F6", "#5E35B1"),
    "decision": ("#FFF3E0", "#EF6C00"),
    "human": ("#E8F5E9", "#2E7D32"),
}
INK, MUTED = "#1A1A1A", "#555555"
SPINE_X, LANE_X, REVIEW_X = 3.6, 10.3, 14.5
BOX_W, DIAMOND_W, DIAMOND_H = 5.2, 5.2, 1.6


def box(ax, x, y, kind, title, detail="", w=BOX_W, h=0.95, pill=False):
    fill, edge = STYLE[kind]
    ax.add_patch(
        FancyBboxPatch(
            (x - w / 2, y - h / 2), w, h,
            boxstyle=f"round,pad=0,rounding_size={h / 2 if pill else 0.12}",
            facecolor=fill, edgecolor=edge, linewidth=1.8,
        )
    )  # fmt: skip
    if detail:
        tall = "\n" in detail
        ax.text(x, y + (0.34 if tall else 0.2), title, ha="center", va="center", fontsize=12,
                fontweight="bold", color=INK)  # fmt: skip
        ax.text(x, y - (0.17 if tall else 0.2), detail, ha="center", va="center", fontsize=9.5,
                color="#333333", linespacing=1.3)  # fmt: skip
    else:
        ax.text(x, y, title, ha="center", va="center", fontsize=12, fontweight="bold", color=INK)


def diamond(ax, x, y, text, w=DIAMOND_W, h=DIAMOND_H):
    fill, edge = STYLE["decision"]
    points = [(x, y + h / 2), (x + w / 2, y), (x, y - h / 2), (x - w / 2, y)]
    ax.add_patch(Polygon(points, closed=True, facecolor=fill, edgecolor=edge, linewidth=1.8))
    ax.text(x, y, text, ha="center", va="center", fontsize=10, color=INK, linespacing=1.3)


def arrow(ax, start, end, color="#333333"):
    ax.add_patch(
        FancyArrowPatch(start, end, arrowstyle="-|>", mutation_scale=15, linewidth=1.6,
                        color=color, shrinkA=0, shrinkB=0)
    )  # fmt: skip


def label(ax, x, y, text, **kw):
    style = dict(fontsize=9.5, style="italic", color="#444444", ha="center", va="center")
    ax.text(x, y, text, **(style | kw))


def main() -> None:
    fig, ax = plt.subplots(figsize=(13.4, 17.5), dpi=150)
    ax.set_xlim(0, 17.4)
    ax.set_ylim(-5.3, 18.0)
    ax.set_aspect("equal")
    ax.axis("off")
    ax.text(0.4, 17.6, "RFQ-to-quote agent", fontsize=19, fontweight="bold", va="center")
    ax.text(0.4, 17.0, "decision flow for one request", fontsize=13, color=MUTED, va="center")

    # --- main spine: (kind, y, title, detail) --------------------------------
    steps = [
        ("terminal", 16.1, "RFQ arrives", "email · PDF · spreadsheet · note"),
        ("code", 14.85, "Intake", "parse the file to plain text"),
        ("llm", 13.6, "Extract with Claude", "typed RFQ: customer, date, line items"),
        ("decision", 12.0, "File read and\nmodel call succeeded?", None),
        ("decision", 10.0, "Any line items\nfound?", None),
        ("llm", 8.25, "Match each line", "search finds candidates, Claude picks\n"
         "a SKU or declines (never invents one)"),
        ("code", 6.75, "Price the matched lines", "list price, volume discount, margin"),
        ("decision", 5.05, "Every line matched, confident,\nwith quantity and unit?", None),
        ("decision", 3.05, "Unit, minimum order\nand lead time fit?", None),
        ("decision", 1.05, "Margin and order value\nwithin limits?", None),
        ("decision", -0.95, "Recipient is the\nverified customer?", None),
        ("decision", -2.95, "Nothing left open\nor unread?", None),
        ("terminal", -4.6, "Auto-approved", "draft quote ready to send"),
    ]  # fmt: skip
    half = []
    for kind, y, title, detail in steps:
        if kind == "decision":
            diamond(ax, SPINE_X, y, title)
            half.append(DIAMOND_H / 2)
        else:
            tall = "\n" in (detail or "")
            box(ax, SPINE_X, y, kind, title, detail, h=1.25 if tall else 0.95,
                pill=kind == "terminal")  # fmt: skip
            half.append(0.625 if tall else 0.475)
    for (_, y1, *_), h1, (_, y2, *_), h2 in zip(steps, half, steps[1:], half[1:], strict=False):
        arrow(ax, (SPINE_X, y1 - h1), (SPINE_X, y2 + h2))
    for kind, y, *_ in steps:
        if kind == "decision":
            label(ax, SPINE_X + 0.42, y - DIAMOND_H / 2 - 0.12, "yes")

    # --- human review lane ---------------------------------------------------
    lane_w, lane_top, lane_bottom = 2.9, 13.1, -3.9
    fill, edge = STYLE["human"]
    ax.add_patch(
        FancyBboxPatch(
            (LANE_X - lane_w / 2, lane_bottom), lane_w, lane_top - lane_bottom,
            boxstyle="round,pad=0,rounding_size=0.14", facecolor=fill, edgecolor=edge,
            linewidth=1.8,
        )
    )  # fmt: skip
    ax.text(LANE_X, 5.35, "Held for\nhuman review", ha="center", va="center", fontsize=13,
            fontweight="bold", color=INK, linespacing=1.3)  # fmt: skip
    ax.text(LANE_X, 4.15, "the reviewer sees the\nsource document, the\ndraft quote and every\n"
            "reason that fired", ha="center", va="center", fontsize=9.5, color="#333333",
            linespacing=1.35)  # fmt: skip

    reasons = {
        12.0: "PIPELINE_ERROR",
        10.0: "NO_LINE_ITEMS",
        5.05: "UNMATCHED_ITEM\nLOW_MATCH_CONFIDENCE\nMISSING_QUANTITY\nUNKNOWN_UNIT",
        3.05: "UNIT_MISMATCH\nBELOW_MOQ\nLEAD_TIME_RISK",
        1.05: "MARGIN_BELOW_FLOOR\nHIGH_ORDER_VALUE",
        -0.95: "MISSING_CUSTOMER_EMAIL\nINTERNAL_RECIPIENT\nEMAIL_NOT_IN_SOURCE\nRECIPIENT_NOT_SENDER",
        -2.95: "OPEN_QUESTION\nUNREAD_ATTACHMENT\nMISSING_CUSTOMER_NAME",
    }
    exit_x, lane_left = SPINE_X + DIAMOND_W / 2, LANE_X - lane_w / 2
    for y, codes in reasons.items():
        arrow(ax, (exit_x, y), (lane_left, y))
        label(ax, exit_x + 0.3, y + 0.2, "no")
        lines = codes.count("\n") + 1
        ax.text((exit_x + lane_left) / 2, y - 0.2 - 0.17 * lines, codes, ha="center",
                va="center", fontsize=8, family="monospace", color="#8A3B00", linespacing=1.35)  # fmt: skip

    # --- reviewer decision ---------------------------------------------------
    decide_y = 9.6
    arrow(ax, (LANE_X + lane_w / 2, decide_y), (REVIEW_X - 1.6, decide_y))
    diamond(ax, REVIEW_X, decide_y, "Reviewer\ndecides", w=3.2, h=1.5)
    outcomes = [
        (7.5, "Approve", "quote ready to send"),
        (5.75, "Correct", "re-priced and re-checked;\nsaved as a new eval case"),
        (4.0, "Reject", "no quote is sent"),
    ]
    trunk_x = REVIEW_X + 2.15
    ax.plot([REVIEW_X + 1.6, trunk_x, trunk_x], [decide_y, decide_y, outcomes[-1][0]],
            color="#333333", linewidth=1.6, solid_capstyle="round")  # fmt: skip
    for y, title, detail in outcomes:
        tall = "\n" in detail
        box(ax, REVIEW_X, y, "terminal", title, detail, w=3.5, h=1.25 if tall else 0.95, pill=True)
        arrow(ax, (trunk_x, y), (REVIEW_X + 1.75, y))

    # --- notes and legend ----------------------------------------------------
    ax.text(12.2, 12.6, "All guardrail checks always run.\nA held quote lists every reason,\n"
            "not only the first one.\n\nIn assisted mode nothing is\nauto-approved: every quote\nis reviewed.", fontsize=9.5, color=MUTED, va="center", linespacing=1.4)  # fmt: skip
    legend = [
        ("terminal", "Start / end", True),
        ("llm", "Step that calls the LLM", False),
        ("code", "Step in plain code", False),
        ("decision", "Decision (rule in code)", False),
        ("human", "Human in the loop", False),
    ]
    lx, ly = 12.3, -1.6
    ax.text(lx, ly, "Legend", fontsize=10.5, fontweight="bold", va="center")
    for i, (kind, text, pill) in enumerate(legend):
        y = ly - 0.5 - i * 0.44
        fill, edge = STYLE[kind]
        if kind == "decision":
            ax.add_patch(Polygon([(lx + 0.28, y + 0.16), (lx + 0.56, y), (lx + 0.28, y - 0.16),
                                  (lx, y)], closed=True, facecolor=fill, edgecolor=edge,
                                 linewidth=1.4))  # fmt: skip
        else:
            ax.add_patch(FancyBboxPatch(
                (lx, y - 0.13), 0.56, 0.26,
                boxstyle=f"round,pad=0,rounding_size={0.13 if pill else 0.05}",
                facecolor=fill, edgecolor=edge, linewidth=1.4))  # fmt: skip
        ax.text(lx + 0.8, y, text, fontsize=9.5, va="center", color="#333333")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, facecolor="white", bbox_inches="tight", pad_inches=0.25)
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
