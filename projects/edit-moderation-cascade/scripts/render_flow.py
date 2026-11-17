"""Render docs/agent-flow.png: the components, and which of them call a model.

uv run python scripts/render_flow.py
"""

from __future__ import annotations

from pathlib import Path

from _draw import INK, STYLE, arrow, box, canvas, legend, path, save

OUT = Path(__file__).resolve().parents[1] / "docs" / "agent-flow.png"

# Figures shown in the labels. They mirror config.Settings (model_share, wiki_pause_s,
# truth_after_hours), loadtest.FETCH_BATCH, wiki.BATCH and the load test's speeds.
SHARE, PAUSE, FETCH_BATCH, TAG_BATCH, TRUTH_HOURS = "20%", "five", 25, 50, 48
SPEEDS = "1x to 50x"

HEIGHT = 15.1
COLS = (2.55, 7.5, 12.45, 17.4)  # x of the four columns
ROWS = (12.35, 9.8, 7.25, 4.9, 2.05)  # y of the five rows
W, H = 3.8, 1.5  # box width and height
LEFT_EDGE, RIGHT_EDGE = 0.3, 19.62  # x of the lines down the two margins
# The file name is written in the colour of the box's edge. Amber is too pale for text.
MODULE_INK = {kind: edge for kind, (_, edge) in STYLE.items()} | {"data": "#A15C00"}
LABEL = dict(fontsize=8.5, style="italic", color="#444444", va="center")


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
    fig, ax = canvas(19.9, HEIGHT)
    ax.text(
        9.95,
        HEIGHT - 0.57,
        "Edit moderation cascade: components",
        ha="center",
        fontsize=15,
        fontweight="bold",
    )
    ax.text(
        9.95,
        HEIGHT - 1.1,
        f"One step calls a paid model, and only for the riskiest {SHARE} of edits. "
        "Tier 1 is a classifier trained here. The rest is code.",
        ha="center",
        fontsize=10,
        color="#5E35B1",
    )
    c1, c2, c3, c4 = COLS
    r1, r2, r3, r4, r5 = ROWS

    # Row 1: every edit is read from the stream, scored and offered to the model's queue.
    part(
        ax,
        c1,
        r1,
        "terminal",
        "Wikimedia event stream",
        "",
        "every change to every wiki, as\npublic events; read only",
    )
    part(
        ax,
        c2,
        r1,
        "code",
        "Consume",
        "stream.py",
        "reconnects from the last event id;\nkeeps article edits by people",
    )
    part(
        ax,
        c3,
        r1,
        "ml",
        "Tier 1",
        "tier1.py, features.py",
        "gradient boosting on the event's own\nfields; scores every edit, no request",
    )
    part(
        ax,
        c4,
        r1,
        "code",
        "Admission",
        "cascade.py",
        f"in the riskiest {SHARE}? room in the queue\nbetween the tiers? budget left?",
    )

    # Row 2: what stands beside the stream: checks, a recording, the load test, the cap.
    part(
        ax,
        c1,
        r2,
        "code",
        "Health",
        "health.py",
        "its own command, run before a live run:\nfive checks; a failed check exits non-zero",
    )
    part(
        ax,
        c2,
        r2,
        "data",
        "Recorder",
        "recorder.py",
        f"a slice of the stream, saved without\naccount names; replayed at {SPEEDS}",
    )
    part(
        ax,
        c3,
        r2,
        "code",
        "Load test",
        "loadtest.py",
        "a virtual clock and measured latencies:\nwhat is skipped at each speed",
    )
    part(
        ax,
        c4,
        r2,
        "code",
        "Budget",
        "budget.py",
        "a daily cap, spent evenly\nacross the day",
    )

    # Row 3: the model's slice.
    part(
        ax,
        c1,
        r3,
        "terminal",
        "Wiki API",
        "",
        f"revision text and tags; read only,\none request every {PAUSE} seconds",
    )
    part(
        ax,
        c2,
        r3,
        "code",
        "Enrich",
        "diff.py",
        f"what the edit changed: both revisions\nread, {FETCH_BATCH} edits a request, compared here",
    )
    part(
        ax,
        c3,
        r3,
        "llm",
        "Tier 2",
        "tier2.py",
        "one paid call per edit in the slice: a risk,\na category, a line range; no free text",
    )
    part(
        ax,
        c4,
        r3,
        "code",
        "Gate",
        "gate.py",
        "risk from 0 to 100, category in the list,\nrange inside the change;\nelse tier 1 stands",
    )

    # Row 4: one risk for each edit, logged and shown.
    part(
        ax,
        c1,
        r4,
        "terminal",
        "Baseline for comparison",
        "",
        "Wikimedia's own revert-risk model;\nnot part of the cascade",
    )
    part(
        ax,
        c2,
        r4,
        "human",
        "Review queue",
        "app/review_queue.py",
        "a person reads the queue top down;\nmarks problem, fine or unsure",
    )
    part(
        ax,
        c3,
        r4,
        "data",
        "Prediction log",
        "log.py",
        "append-only, committed: revision ids\nand scores; no text, no account names",
    )
    part(
        ax,
        c4,
        r4,
        "code",
        "Combine",
        "combine.py",
        "one risk from two tiers, by two fitted\nweights; tier 1 alone if no model read",
    )

    # Row 5: what became of each edit, and the score.
    part(
        ax,
        c1,
        r5,
        "code",
        "Truth",
        "truth.py",
        f"reads the mw-reverted tag of each\nlogged revision, {TAG_BATCH} revisions a request",
    )
    part(
        ax,
        c2,
        r5,
        "data",
        "Outcome log",
        "truth.py",
        "append-only, committed: reverted, kept\nor not found, and when it was read",
    )
    part(
        ax,
        c3,
        r5,
        "code",
        "Scoring",
        "evals.py",
        "how many of the reverted edits were\nnear the top of the queue",
    )
    part(
        ax,
        c4,
        r5,
        "code",
        "Scoreboard and report",
        "scoreboard.py, report.py",
        "the queue beside arrival order and the\nbaseline; latency, skips and cost",
    )

    half, side = H / 2, W / 2
    inset = 1.2  # how far from a box's centre an elbow line meets its edge
    elbow = r1 - half - 0.6  # y of the lines that turn between rows 1 and 2
    slice_bus = r2 - half - 0.6  # y of the line that carries the model's slice to Enrich
    baseline_bus = r4 - half - 0.5  # y of the line from the baseline to Scoring
    rule = r4 - half - 0.78  # y of the dashed line

    # Row 1, left to right.
    arrow(ax, (c1 + side, r1), (c2 - side, r1), "events", offset=(0, 0.22))
    arrow(ax, (c2 + side, r1), (c3 - side, r1), "edits", offset=(0, 0.22))
    arrow(ax, (c3 + side, r1), (c4 - side, r1), "scores", offset=(0, 0.22))

    # Between rows 1 and 2.
    path(
        ax,
        [
            (c1 + inset, r2 + half),
            (c1 + inset, elbow),
            (c2 - inset, elbow),
            (c2 - inset, r1 - half),
        ],
        "checks the stream",
        at=1,
        offset=(0, 0.22),
    )
    arrow(ax, (c2, r1 - half), (c2, r2 + half), "a slice", offset=(-0.55, 0.05))
    path(
        ax,
        [
            (c2 + inset, r2 + half),
            (c2 + inset, elbow),
            (c3 - inset, elbow),
            (c3 - inset, r1 - half),
        ],
        "replayed",
        at=1,
        offset=(0, 0.22),
    )
    arrow(ax, (c2 + side, r2), (c3 - side, r2), "arrivals", offset=(0, 0.22))
    path(
        ax,
        [
            (c3 + inset, r2 + half),
            (c3 + inset, elbow),
            (c4 - inset, elbow),
            (c4 - inset, r1 - half),
        ],
        "the same policy",
        at=1,
        offset=(0, 0.22),
    )
    arrow(ax, (c4 + 0.3, r2 + half), (c4 + 0.3, r1 - half), "allowed?", offset=(0.65, 0.05))

    # Down the right margin: the model's slice turns off to Enrich, and every edit's
    # tier-1 score goes on to Combine.
    path(ax, [(c4 + side, r1), (RIGHT_EDGE, r1), (RIGHT_EDGE, r4), (c4 + side, r4)])
    path(
        ax,
        [(RIGHT_EDGE, slice_bus), (c2, slice_bus), (c2, r3 + half)],
        f"the riskiest {SHARE}, while the queue has room and the budget allows",
        at=0,
        offset=(0, 0.22),
    )
    ax.text(
        RIGHT_EDGE - 0.17,
        (r3 - half + r4 + half) / 2,
        "tier-1 score of every edit,\nand the reason for a skip",
        ha="right",
        linespacing=1.25,
        **LABEL,
    )

    # Row 3, left to right, then down to Combine.
    arrow(ax, (c1 + side, r3), (c2 - side, r3), "revisions", offset=(0, 0.22))
    arrow(ax, (c2 + side, r3), (c3 - side, r3), "the change", offset=(0, 0.22))
    arrow(ax, (c3 + side, r3), (c4 - side, r3), "three fields", offset=(0, 0.22))
    arrow(ax, (c4 - 0.9, r3 - half), (c4 - 0.9, r4 + half), "checked answer", offset=(-0.85, 0))

    # Row 4, right to left.
    arrow(ax, (c4 - side, r4), (c3 + side, r4), "one risk", offset=(0, 0.22))
    arrow(ax, (c3 - side, r4), (c2 + side, r4), "riskiest first", offset=(0, 0.22))

    # After 48 hours: the outcome is read and the queue is scored.
    ax.plot([0.65, 19.3], [rule, rule], color="#9E9E9E", linewidth=1.0, linestyle=(0, (5, 4)))
    ax.text(
        0.8,
        rule - 0.27,
        f"{TRUTH_HOURS} hours after an edit was made",
        fontsize=10,
        color="#616161",
        va="center",
    )
    path(ax, [(c1 - side, r3), (LEFT_EDGE, r3), (LEFT_EDGE, r5), (c1 - side, r5)])
    ax.text(
        LEFT_EDGE + 0.17,
        (r3 - half + r4 + half) / 2,
        f"revision tags, {TRUTH_HOURS} hours later",
        ha="left",
        **LABEL,
    )
    path(
        ax,
        [
            (c1, r4 - half),
            (c1, baseline_bus),
            (c3 - inset, baseline_bus),
            (c3 - inset, r5 + half),
        ],
        "its score for the same edits",
        at=1,
        offset=(0, 0.2),
    )
    arrow(ax, (c3 + 0.3, r4 - half), (c3 + 0.3, r5 + half), "every prediction", offset=(1.0, 0.4))
    arrow(ax, (c1 + side, r5), (c2 - side, r5), "outcome", offset=(0, 0.22))
    arrow(ax, (c2 + side, r5), (c3 - side, r5), "outcomes", offset=(0, 0.22))
    arrow(ax, (c3 + side, r5), (c4 - side, r5), "scores", offset=(0, 0.22))

    legend(
        ax,
        2.05,
        0.55,
        [
            ("terminal", "Outside world"),
            ("code", "Code, no model"),
            ("ml", "Classical ML"),
            ("llm", "Paid model call"),
            ("human", "Person"),
            ("data", "Stored data"),
        ],
        step=3.17,
        w=2.8,
    )
    save(fig, out)


if __name__ == "__main__":
    render()
    print(f"Wrote {OUT}")
