"""Small drawing helpers shared by the two diagram scripts."""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Polygon

STYLE = {  # kind: (fill, edge)
    "terminal": ("#F5F5F5", "#616161"),
    "code": ("#E3F2FD", "#1565C0"),
    "privacy": ("#E0F2F1", "#00695C"),
    "llm": ("#EDE7F6", "#5E35B1"),
    "decision": ("#FFF3E0", "#EF6C00"),
    "human": ("#E8F5E9", "#2E7D32"),
}
INK = "#1A1A1A"


def canvas(width: float, height: float):
    fig, ax = plt.subplots(figsize=(width, height))
    ax.set_xlim(0, width)
    ax.set_ylim(0, height)
    ax.axis("off")
    return fig, ax


def box(ax, x, y, kind, title, detail="", w=4.6, h=1.0, pill=False):
    fill, edge = STYLE[kind]
    ax.add_patch(
        FancyBboxPatch(
            (x - w / 2, y - h / 2),
            w,
            h,
            boxstyle=f"round,pad=0,rounding_size={h / 2 if pill else 0.12}",
            facecolor=fill,
            edgecolor=edge,
            linewidth=1.8,
        )
    )
    if detail:
        ax.text(
            x, y + 0.2, title, ha="center", va="center", fontsize=11, fontweight="bold", color=INK
        )
        ax.text(
            x,
            y - 0.22,
            detail,
            ha="center",
            va="center",
            fontsize=8.5,
            color="#333333",
            linespacing=1.25,
        )
    else:
        ax.text(x, y, title, ha="center", va="center", fontsize=11, fontweight="bold", color=INK)


def diamond(ax, x, y, text, w=4.6, h=1.5):
    fill, edge = STYLE["decision"]
    points = [(x, y + h / 2), (x + w / 2, y), (x, y - h / 2), (x - w / 2, y)]
    ax.add_patch(Polygon(points, closed=True, facecolor=fill, edgecolor=edge, linewidth=1.8))
    ax.text(x, y, text, ha="center", va="center", fontsize=9, color=INK, linespacing=1.25)


def arrow(ax, start, end, text="", offset=(0.0, 0.0)):
    ax.add_patch(
        FancyArrowPatch(
            start,
            end,
            arrowstyle="-|>",
            mutation_scale=14,
            linewidth=1.5,
            color="#333333",
            shrinkA=0,
            shrinkB=0,
        )
    )
    if text:
        mid = ((start[0] + end[0]) / 2 + offset[0], (start[1] + end[1]) / 2 + offset[1])
        ax.text(
            *mid,
            text,
            ha="center",
            va="center",
            fontsize=8.5,
            style="italic",
            color="#444444",
            bbox=dict(facecolor="white", edgecolor="none", pad=1.0),
        )


def path(ax, points, text="", at=0, offset=(0.0, 0.0)):
    """A line through several points with an arrowhead on the last segment."""
    for a, b in zip(points[:-2], points[1:-1], strict=True):
        ax.plot([a[0], b[0]], [a[1], b[1]], color="#333333", linewidth=1.5)
    arrow(ax, points[-2], points[-1])
    if text:
        a, b = points[at], points[at + 1]
        ax.text(
            (a[0] + b[0]) / 2 + offset[0],
            (a[1] + b[1]) / 2 + offset[1],
            text,
            ha="center",
            va="center",
            fontsize=8.5,
            style="italic",
            color="#444444",
            bbox=dict(facecolor="white", edgecolor="none", pad=1.0),
        )


def legend(ax, x, y, kinds):
    for i, (kind, label) in enumerate(kinds):
        box(ax, x + i * 2.9, y, kind, label, w=2.6, h=0.5)


def save(fig, out):
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=160, bbox_inches="tight", facecolor="white")
    plt.close(fig)
