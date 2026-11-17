"""Render docs/agent-flow.png: how an inbound RFQ becomes a reviewed quote.

uv run python scripts/render_flow.py
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

OUT = Path(__file__).resolve().parents[1] / "docs" / "agent-flow.png"

KINDS = {
    "io": ("#F5F5F5", "#616161", "Input / output"),
    "code": ("#E3F2FD", "#1565C0", "Deterministic code"),
    "llm": ("#EDE7F6", "#5E35B1", "LLM call (Claude, structured output)"),
    "human": ("#E8F5E9", "#2E7D32", "Human in the loop"),
    "data": ("#FFF8E1", "#F9A825", "Data store"),
}
W, H = 2.8, 1.4

# name: (x, y, kind, title, detail, width)
BOXES = {
    "inbound": (2.0, 7.3, "io", "Inbound RFQ", "email · PDF · spreadsheet\nphone or chat note", W),
    "intake": (5.6, 7.3, "code", "1  Intake", "parse any format\nto plain text", W),
    "extract": (9.2, 7.3, "llm", "2  Extract", "typed RFQ: customer, date,\nline items, missing info", W),
    "match": (12.8, 7.3, "llm", "3  Match", "search finds candidates,\nmodel picks a SKU or declines", W),
    "quote": (12.8, 4.4, "code", "4  Quote", "prices, volume discounts\nand margins in plain code", W),
    "guard": (9.2, 4.4, "code", "5  Guardrails", "lines · units · MOQ · lead time\nmargin · value · recipient · open points", W),
    "auto": (5.6, 4.4, "io", "Auto-approved", "draft quote\nready to send", W),
    "review": (9.2, 1.6, "human", "Human review", "queue: approve · correct\nor reject (Streamlit)", W),
    "corrections": (5.6, 1.6, "data", "Corrections", "saved as new\nlabelled eval cases", W),
    "evals": (2.0, 1.6, "code", "Eval suite", "accuracy · routing recall\ncost per RFQ", W),
    "prompts": (2.0, 4.4, "data", "Prompts + policy", "versioned in git,\nchanged only with evals", W),
    "catalog": (15.75, 5.85, "data", "Catalog", "SQLite\n+ FTS5", 1.7),
}  # fmt: skip

# (from, to, label, dashed, from side, to side)
ARROWS = [
    ("inbound", "intake", "", False, "r", "l"),
    ("intake", "extract", "", False, "r", "l"),
    ("extract", "match", "", False, "r", "l"),
    ("match", "quote", "", False, "b", "t"),
    ("quote", "guard", "", False, "l", "r"),
    ("guard", "auto", "no flags", False, "l", "r"),
    ("guard", "review", "any flag", False, "b", "t"),
    ("review", "corrections", "", False, "l", "r"),
    ("corrections", "evals", "", False, "l", "r"),
    ("evals", "prompts", "measure", True, "t", "b"),
    ("prompts", "extract", "improve", True, "tr", "bl"),
    ("catalog", "match", "", True, "tl", "r"),
    ("catalog", "quote", "", True, "bl", "r"),
]


def anchor(name: str, side: str) -> tuple[float, float]:
    x, y, *_, width = BOXES[name]
    dx, dy = width / 2, H / 2
    return {
        "l": (x - dx, y), "r": (x + dx, y), "t": (x, y + dy), "b": (x, y - dy),
        "tr": (x + dx * 0.55, y + dy), "bl": (x - dx * 0.75, y - dy),
        "tl": (x - dx * 0.2, y + dy), "br": (x + dx * 0.55, y - dy),
    }[side]  # fmt: skip


def main() -> None:
    fig, ax = plt.subplots(figsize=(17, 9.4), dpi=150)
    ax.set_xlim(0, 17)
    ax.set_ylim(0, 9.4)
    ax.axis("off")
    ax.text(0.5, 8.95, "RFQ-to-quote agent", fontsize=20, fontweight="bold", va="center")
    ax.text(5.05, 8.95, "how a messy request becomes a reviewed draft quote",
            fontsize=13, color="#555555", va="center")  # fmt: skip

    for x, y, kind, title, detail, width in BOXES.values():
        fill, edge, _ = KINDS[kind]
        ax.add_patch(FancyBboxPatch(
            (x - width / 2, y - H / 2), width, H, boxstyle="round,pad=0,rounding_size=0.14",
            facecolor=fill, edgecolor=edge, linewidth=1.8))  # fmt: skip
        ax.text(x, y + 0.36, title, ha="center", va="center", fontsize=12.5, fontweight="bold",
                color="#1A1A1A")  # fmt: skip
        ax.text(x, y - 0.24, detail, ha="center", va="center", fontsize=9.2, color="#333333",
                linespacing=1.35)  # fmt: skip

    for src, dst, label, dashed, s_side, d_side in ARROWS:
        start, end = anchor(src, s_side), anchor(dst, d_side)
        ax.add_patch(FancyArrowPatch(
            start, end, arrowstyle="-|>", mutation_scale=16, linewidth=1.6,
            linestyle=(0, (4, 3)) if dashed else "solid",
            color="#888888" if dashed else "#333333", shrinkA=2, shrinkB=2))  # fmt: skip
        if label:
            mx, my = (start[0] + end[0]) / 2, (start[1] + end[1]) / 2
            vertical = abs(start[0] - end[0]) < 0.1
            ax.text(mx + (0.12 if vertical else 0), my + (0 if vertical else 0.2), label,
                    fontsize=9.5, style="italic", color="#444444",
                    ha="left" if vertical else "center", va="center",
                    bbox=dict(facecolor="white", edgecolor="none", pad=1.2))  # fmt: skip

    lx, ly = 11.6, 2.35
    ax.text(lx, ly, "Legend", fontsize=10.5, fontweight="bold", va="center")
    for i, (fill, edge, text) in enumerate(KINDS.values()):
        y = ly - 0.4 - i * 0.36
        ax.add_patch(FancyBboxPatch((lx, y - 0.11), 0.42, 0.22,
                                    boxstyle="round,pad=0,rounding_size=0.05",
                                    facecolor=fill, edgecolor=edge, linewidth=1.4))  # fmt: skip
        ax.text(lx + 0.6, y, text, fontsize=9.5, va="center", color="#333333")
    ax.text(lx, ly - 0.4 - len(KINDS) * 0.36 - 0.05, "Dashed arrows: data lookups and the "
            "improvement loop", fontsize=9, va="center", color="#666666")  # fmt: skip

    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, facecolor="white", bbox_inches="tight", pad_inches=0.25)
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
