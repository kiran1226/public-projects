"""Render docs/agent-flow.png: the components, and which of them call a model.

uv run python scripts/render_flow.py
"""

from __future__ import annotations

from pathlib import Path

from _draw import INK, STYLE, arrow, box, canvas, legend, path, save

OUT = Path(__file__).resolve().parents[1] / "docs" / "agent-flow.png"

# Figures shown in the labels. They mirror triagedesk.yaml: candidates, model_candidates
# and truth_after_days.
CANDIDATES, SHOWN, TRUTH_DAYS = 20, 8, 14

COLS = (2.55, 7.5, 12.45, 17.4)  # x of the four columns
ROWS = (13.05, 10.95, 8.8, 6.7, 4.6, 2.05)  # y of the six rows
W, H = 3.8, 1.5  # box width and height
LEFT_EDGE, RIGHT_EDGE = 0.3, 19.62  # x of the lines down the two margins
TOP = ROWS[0] + H / 2 + 0.32  # y of the line that passes above the first row
# The file name is written in the colour of the box's edge. Amber is too pale for text.
MODULE_INK = {kind: edge for kind, (_, edge) in STYLE.items()} | {"data": "#A15C00"}


def part(ax, x, y, kind, title, module, detail):
    """A component: its name, the file it lives in, and what it does."""
    box(ax, x, y, kind, "", w=W, h=H)
    title_h, module_h, gap = 0.21, 0.17, 0.11
    detail_h = 0.19 * (detail.count("\n") + 1)
    total = title_h + gap + detail_h + ((module_h + gap) if module else 0.0)
    cursor = y + total / 2
    ax.text(
        x,
        cursor - title_h / 2,
        title,
        ha="center",
        va="center",
        fontsize=11,
        fontweight="bold",
        color=INK,
    )
    cursor -= title_h + gap
    if module:
        ax.text(
            x,
            cursor - module_h / 2,
            module,
            ha="center",
            va="center",
            fontsize=8,
            family="monospace",
            color=MODULE_INK[kind],
        )
        cursor -= module_h + gap
    ax.text(
        x,
        cursor - detail_h / 2,
        detail,
        ha="center",
        va="center",
        fontsize=8.5,
        color="#333333",
        linespacing=1.25,
    )


def render(out: Path = OUT) -> None:
    fig, ax = canvas(19.9, 15.8)
    ax.text(
        9.95, 15.23, "Issue triage desk: components", ha="center", fontsize=15, fontweight="bold"
    )
    ax.text(
        9.95,
        14.7,
        "One step calls a paid model. The classifier is trained here and the embedding model "
        "runs on a CPU. The rest is code.",
        ha="center",
        fontsize=10,
        color="#5E35B1",
    )
    c1, c2, c3, c4 = COLS
    r1, r2, r3, r4, r5, r6 = ROWS

    # Row 1: an issue comes in and is kept.
    part(
        ax,
        c1,
        r1,
        "terminal",
        "GitHub API",
        "",
        "a public issue tracker: issue search\nand timelines, read only",
    )
    part(
        ax,
        c2,
        r1,
        "code",
        "Fetch",
        "feed.py",
        "every hour: the issues opened\nsince the last run",
    )
    part(
        ax,
        c3,
        r1,
        "data",
        "Store",
        "store.py",
        "each issue as first seen;\nlocal file, never committed",
    )
    part(
        ax,
        c4,
        r1,
        "local",
        "Index",
        "index.py",
        "word search (SQLite FTS5) and vectors\nfrom an embedding model on a CPU",
    )

    # Row 2: checks on the feed, and the earlier issues found for this one.
    part(
        ax,
        c2,
        r2,
        "code",
        "Health",
        "health.py",
        "four checks of the feed; a failed\ncheck fails the scheduled job",
    )
    part(
        ax,
        c4,
        r2,
        "code",
        "Candidates",
        "duplicates.py",
        f"the two result lists fused by rank:\ntop {CANDIDATES}, only issues opened earlier",
    )

    # Row 3: three ways to answer, and the cap on the paid one.
    part(
        ax,
        c1,
        r3,
        "code",
        "Rules baseline",
        "rules.py",
        "a label's keywords in the issue;\nbaseline only",
    )
    part(
        ax,
        c2,
        r3,
        "ml",
        "Classifier",
        "classifier.py",
        "TF-IDF and logistic regression per\nlabel; no paid model; also a baseline",
    )
    part(
        ax,
        c3,
        r3,
        "llm",
        "Triage: Claude Haiku 5.5",
        "triage.py",
        f"one paid call per issue: the issue, the\nlabel definitions, {SHOWN} earlier issues",
    )
    part(
        ax,
        c4,
        r3,
        "code",
        "Budget",
        "budget.py",
        "daily spend cap, checked\nbefore every model call",
    )

    # Rows 4 and 5: every answer is checked, logged and shown.
    part(
        ax,
        c3,
        r4,
        "code",
        "Gate",
        "gate.py",
        "a label must be in the taxonomy; a\n"
        f"duplicate must be one of the {SHOWN} shown and\nolder; decides what goes to a person",
    )
    part(
        ax,
        c4,
        r4,
        "code",
        "Act",
        "act.py",
        "shadow mode writes nothing outside;\napply mode needs the allow-list",
    )
    part(
        ax,
        c3,
        r5,
        "data",
        "Prediction log",
        "log.py",
        "append-only, committed:\nnumbers and answers, no issue text",
    )
    part(
        ax,
        c4,
        r5,
        "human",
        "Triage queue page",
        "app/triage_queue.py",
        "each proposal beside its issue; a\nperson confirms or corrects",
    )

    # Row 6: what the maintainers did, and the score.
    part(
        ax,
        c1,
        r6,
        "code",
        "Truth",
        "truth.py",
        "labels, close reason, and the original\nof a duplicate from its timeline",
    )
    part(
        ax,
        c2,
        r6,
        "data",
        "Outcome log",
        "truth.py",
        "append-only, committed: what the\nmaintainers did, and when it was read",
    )
    part(
        ax,
        c3,
        r6,
        "code",
        "Scoring",
        "evals.py",
        "each prediction against what\nthe maintainers did",
    )
    part(
        ax,
        c4,
        r6,
        "code",
        "Scoreboard and report",
        "scoreboard.py, report.py",
        "each configuration beside the\nbaselines; cost and feed health",
    )

    half, side = H / 2, W / 2
    issue_x, shown_x = c3 - 0.9, c3 + 0.9  # the two lines that enter Triage from above
    bus = r3 + half + 0.3  # y of the line that carries the issue to the two baselines

    # Row 1, left to right.
    arrow(ax, (c1 + side, r1), (c2 - side, r1), "new issues", offset=(0, 0.22))
    arrow(ax, (c2 + side, r1), (c3 - side, r1), "first seen", offset=(0, 0.22))
    arrow(ax, (c3 + side, r1), (c4 - side, r1), "issue text", offset=(0, 0.22))
    arrow(ax, (c2, r1 - half), (c2, r2 + half), "the last seven days", offset=(1.2, 0))
    arrow(ax, (c4, r1 - half), (c4, r2 + half), "two ranked lists", offset=(-1.1, 0))

    # The issue goes to the model and to the two baselines.
    arrow(ax, (issue_x, r1 - half), (issue_x, r3 + half), "the issue", offset=(-0.6, 0.45))
    ax.plot([c1, issue_x], [bus, bus], color="#333333", linewidth=1.5)
    arrow(ax, (c1, bus), (c1, r3 + half))
    arrow(ax, (c2, bus), (c2, r3 + half))
    path(
        ax,
        [(c4 - side, r2), (shown_x, r2), (shown_x, r3 + half)],
        f"top {SHOWN}",
        at=0,
        offset=(0, 0.22),
    )
    arrow(ax, (c4 - side, r3), (c3 + side, r3), "allowed?", offset=(0, 0.22))

    # Every answer goes through the gate.
    arrow(ax, (c3, r3 - half), (c3, r4 + half), "labels, duplicate or none", offset=(1.75, 0))
    ax.plot([c1, c1, c2], [r3 - half, r4, r4], color="#333333", linewidth=1.5)
    path(
        ax,
        [(c2, r3 - half), (c2, r4), (c3 - side, r4)],
        "baselines; the classifier also has to agree\nbefore a model label counts as sure",
        at=1,
        offset=(0, 0.36),
    )
    arrow(ax, (c3 + side, r4), (c4 - side, r4), "sure parts", offset=(0, 0.22))
    arrow(ax, (c3, r4 - half), (c3, r5 + half), "every answer", offset=(1.0, 0))
    arrow(ax, (c3 + side, r5), (c4 - side, r5), "proposals", offset=(0, 0.22))
    path(
        ax,
        [
            (shown_x, r1 + half),
            (shown_x, TOP),
            (RIGHT_EDGE, TOP),
            (RIGHT_EDGE, r5),
            (c4 + side, r5),
        ],
        "titles and text, for the page",
        at=1,
        offset=(0, 0.22),
    )

    # Fourteen days later: the outcome is read and the prediction is scored.
    ax.plot([0.65, 19.3], [3.35, 3.35], color="#9E9E9E", linewidth=1.0, linestyle=(0, (5, 4)))
    ax.text(
        0.8,
        3.08,
        f"{TRUTH_DAYS} days after an issue was opened",
        fontsize=10,
        color="#616161",
        va="center",
    )
    path(
        ax,
        [(c1 - side, r1), (LEFT_EDGE, r1), (LEFT_EDGE, r6), (c1 - side, r6)],
        "what the maintainers did:\nlabels, close reason and, for\na duplicate, the timeline",
        at=1,
        offset=(1.85, -2.2),
    )
    arrow(ax, (c3, r5 - half), (c3, r6 + half), "every prediction", offset=(1.2, 0.3))
    arrow(ax, (c1 + side, r6), (c2 - side, r6), "outcome", offset=(0, 0.22))
    arrow(ax, (c2 + side, r6), (c3 - side, r6), "outcomes", offset=(0, 0.22))
    arrow(ax, (c3 + side, r6), (c4 - side, r6), "scores", offset=(0, 0.22))

    legend(
        ax,
        1.9,
        0.55,
        [
            ("terminal", "Outside world"),
            ("code", "Code, no model"),
            ("ml", "Classical ML"),
            ("local", "Local model"),
            ("llm", "Paid model call"),
            ("human", "Person"),
            ("data", "Stored data"),
        ],
        step=2.75,
        w=2.5,
    )
    save(fig, out)


if __name__ == "__main__":
    render()
    print(f"Wrote {OUT}")
