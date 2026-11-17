"""Render docs/agent-flowchart.png: every decision for one edit, and every way it ends up
ordered by tier 1 alone.

uv run python scripts/render_flowchart.py
"""

from __future__ import annotations

from pathlib import Path

from _draw import arrow, box, canvas, diamond, legend, path, save

OUT = Path(__file__).resolve().parents[1] / "docs" / "agent-flowchart.png"

# Figures shown in the labels. They mirror config.Settings (model_share, queue_bound,
# wiki_pause_s, truth_after_hours), loadtest.FETCH_BATCH, live.DRAIN_S and wiki.BATCH.
SHARE, QUEUE_BOUND, PAUSE, FETCH_BATCH, DRAIN_S, TRUTH_HOURS, TAG_BATCH = (
    "20%",
    50,
    "five",
    25,
    60,
    48,
    50,
)

# x of the four lanes: what a "no" on the left leads to, the edit up to admission,
# the edit once admitted, and what a "no" on the right leads to.
EXIT_L, MAIN_L, MAIN_R, EXIT_R = 2.45, 7.1, 12.8, 17.45
BUS = 9.95  # x of the line that carries an admitted edit up to the second lane
LEFT_EDGE, RIGHT_EDGE = 0.25, 19.65  # x of the lines down the two margins
MAIN_W, EXIT_W = 4.4, 3.7

HEIGHT = 28.1
Y0 = HEIGHT - 3.1  # y of the start

# y of each step in the first lane, top to bottom.
EDIT, KEEP, TIER1, MODEL = Y0 - 1.75, Y0 - 3.55, Y0 - 5.2, Y0 - 7.0
SLICE, ROOM, BUDGET = Y0 - 8.95, Y0 - 10.9, Y0 - 12.85
WRAP = Y0 - 14.1  # y of the line that takes an admitted edit across to the bus

# y of each step in the second lane, top to bottom.
WAIT, CLOSED, READ, READABLE = Y0 - 0.3, Y0 - 2.05, Y0 - 3.85, Y0 - 5.65
ASK, ANSWERED, GATE, COMBINE = Y0 - 7.45, Y0 - 9.25, Y0 - 11.3, Y0 - 13.45
LOG, QUEUE = Y0 - 15.3, Y0 - 17.1

# The second run, below the dashed line: three rows.
RULE = Y0 - 18.5
ROW_A, ROW_B, ROW_C = Y0 - 20.0, Y0 - 21.95, Y0 - 24.0
JOIN = ROW_B - 0.6 - 0.4  # where the three outcomes meet above "Outcome logged"

NOTE = dict(fontsize=8.5, style="italic", color="#444444", va="center", linespacing=1.25)


def render(out: Path = OUT) -> None:
    fig, ax = canvas(19.9, HEIGHT)
    ax.text(
        9.95,
        HEIGHT - 0.55,
        "Decision flow for one edit",
        ha="center",
        fontsize=16,
        fontweight="bold",
    )
    ax.text(
        9.95,
        HEIGHT - 1.05,
        "Every article edit by a person gets a tier-1 score and a log row. The model's answer counts only "
        "if every check on the way passes; otherwise tier 1 orders the edit.",
        ha="center",
        fontsize=10,
        color="#5E35B1",
    )
    legend(
        ax,
        5.0,
        HEIGHT - 1.9,
        [
            ("code", "Code"),
            ("ml", "Classical ML"),
            ("llm", "Paid model call"),
            ("human", "Person"),
        ],
        step=3.3,
        w=3.0,
    )

    # First lane: the event arrives, tier 1 scores it, admission decides.
    box(ax, MAIN_L, Y0, "terminal", "An event arrives on the stream", w=MAIN_W, pill=True)
    diamond(ax, MAIN_L, EDIT, "An article edit by a\nperson on this wiki?", w=MAIN_W)
    box(
        ax,
        MAIN_L,
        KEEP,
        "code",
        "Keep the edit's own fields",
        "the account name is read once (temporary\nor registered account?) and is not kept",
        w=MAIN_W,
        h=1.2,
    )
    box(
        ax,
        MAIN_L,
        TIER1,
        "ml",
        "Tier 1 scores the edit",
        "a classifier on the event's own fields;\nno request is made",
        w=MAIN_W,
        h=1.2,
    )
    diamond(
        ax,
        MAIN_L,
        MODEL,
        "This run has a model?\n(a key is set, and it was not\nstarted with --no-model)",
        w=MAIN_W,
    )
    diamond(ax, MAIN_L, SLICE, f"In the riskiest {SHARE}\nby tier-1 score?", w=MAIN_W)
    diamond(
        ax,
        MAIN_L,
        ROOM,
        f"Room in the queue between\nthe tiers? (fewer than\n{QUEUE_BOUND} edits in it)",
        w=MAIN_W,
    )
    diamond(ax, MAIN_L, BUDGET, "Budget left for this\ntime of day?", w=MAIN_W)

    # What a "no" in the first lane leads to.
    box(
        ax,
        EXIT_L,
        EDIT,
        "terminal",
        "Dropped",
        "not scored, not logged",
        w=EXIT_W,
        h=1.1,
        pill=True,
    )
    box(
        ax,
        EXIT_L,
        MODEL,
        "ml",
        "Ordered by tier 1 alone",
        f"the run has no model; reason no_model\nif the edit was in the riskiest {SHARE}",
        w=EXIT_W,
        h=1.2,
    )
    box(
        ax,
        EXIT_L,
        SLICE,
        "ml",
        "Ordered by tier 1 alone",
        "never meant for the model:\nnot a skip, no reason logged",
        w=EXIT_W,
        h=1.2,
    )
    box(
        ax,
        EXIT_L,
        ROOM,
        "ml",
        "Ordered by tier 1",
        "a skipped model read, counted;\nreason: queue_full",
        w=EXIT_W,
        h=1.2,
    )
    box(
        ax,
        EXIT_L,
        BUDGET,
        "ml",
        "Ordered by tier 1",
        "a skipped model read, counted;\nreason: budget",
        w=EXIT_W,
        h=1.2,
    )

    # Second lane: the change is read, the model is asked, the gate checks the answer.
    box(
        ax,
        MAIN_R,
        WAIT,
        "code",
        "Wait for the next request to the wiki",
        f"one every {PAUSE} seconds, for up to {FETCH_BATCH} edits",
        w=MAIN_W,
        h=1.1,
    )
    diamond(
        ax,
        MAIN_R,
        CLOSED,
        f"Still waiting {DRAIN_S} seconds\nafter the run's window\nclosed?",
        w=MAIN_W,
    )
    box(
        ax,
        MAIN_R,
        READ,
        "code",
        "Read what the edit changed",
        "the revision and the one before it,\ncompared line by line",
        w=MAIN_W,
        h=1.2,
    )
    diamond(ax, MAIN_R, READABLE, "Is the change readable?\n(both revisions available)", w=MAIN_W)
    box(
        ax,
        MAIN_R,
        ASK,
        "llm",
        "The model reads the change",
        "it returns a risk from 0 to 100, a category\nand a line range; no free text",
        w=MAIN_W,
        h=1.2,
    )
    diamond(ax, MAIN_R, ANSWERED, "Did the model\nanswer?", w=MAIN_W)
    diamond(
        ax,
        MAIN_R,
        GATE,
        "Risk from 0 to 100, category\nin the list, line range\ninside the change?",
        w=MAIN_W + 0.4,
        h=1.7,
    )
    box(
        ax,
        MAIN_R,
        COMBINE,
        "code",
        "Combine the two tiers",
        "the tier-1 score and the model's risk\nbecome one number",
        w=MAIN_W,
        h=1.2,
    )
    box(
        ax,
        MAIN_R,
        LOG,
        "code",
        "Write the log row",
        "append-only: revision id and scores, no text;\nsaved to the file when the run ends",
        w=MAIN_W,
        h=1.2,
    )
    box(
        ax,
        MAIN_R,
        QUEUE,
        "human",
        "Shown in the review queue",
        "riskiest first; a person reads\nfrom the top down",
        w=MAIN_W,
        h=1.2,
    )
    box(
        ax,
        EXIT_R,
        QUEUE,
        "terminal",
        "Ordered",
        "end for this edit",
        w=EXIT_W - 0.5,
        h=1.1,
        pill=True,
    )

    # What the answers in the second lane lead to.
    box(
        ax,
        EXIT_R,
        CLOSED,
        "ml",
        "Ordered by tier 1",
        "the run ended before the model\nwas asked; reason: window_closed",
        w=EXIT_W,
        h=1.2,
    )
    box(
        ax,
        EXIT_R,
        ASK,
        "ml",
        "Ordered by tier 1",
        "no change to read, or no model\nanswer; reason: model_error",
        w=EXIT_W,
        h=1.2,
    )
    box(
        ax,
        EXIT_R,
        GATE,
        "ml",
        "Answer dropped",
        "ordered by tier 1; reason: gate,\nwith the rule that failed",
        w=EXIT_W,
        h=1.2,
    )

    left_tip, left_box = MAIN_L - MAIN_W / 2, EXIT_L + EXIT_W / 2
    right_tip, right_box = MAIN_R + MAIN_W / 2, EXIT_R - EXIT_W / 2
    far_left, far_right = EXIT_L - EXIT_W / 2, EXIT_R + EXIT_W / 2

    # First lane arrows
    arrow(ax, (MAIN_L, Y0 - 0.5), (MAIN_L, EDIT + 0.75))
    arrow(ax, (left_tip, EDIT), (left_box, EDIT), "no", offset=(0, 0.22))
    arrow(ax, (MAIN_L, EDIT - 0.75), (MAIN_L, KEEP + 0.6), "yes", offset=(0.35, 0))
    arrow(ax, (MAIN_L, KEEP - 0.6), (MAIN_L, TIER1 + 0.6))
    arrow(ax, (MAIN_L, TIER1 - 0.6), (MAIN_L, MODEL + 0.75))
    for step, below in ((MODEL, SLICE), (SLICE, ROOM), (ROOM, BUDGET)):
        arrow(ax, (left_tip, step), (left_box, step), "no", offset=(0, 0.22))
        arrow(ax, (MAIN_L, step - 0.75), (MAIN_L, below + 0.75), "yes", offset=(0.35, 0))
    arrow(ax, (left_tip, BUDGET), (left_box, BUDGET), "no", offset=(0, 0.22))

    # An admitted edit goes up one line to the second lane.
    path(
        ax,
        [
            (MAIN_L, BUDGET - 0.75),
            (MAIN_L, WRAP),
            (BUS, WRAP),
            (BUS, WAIT),
            (MAIN_R - MAIN_W / 2, WAIT),
        ],
        "yes",
        at=0,
        offset=(0.35, 0.05),
    )
    ax.text(
        BUS,
        (KEEP + TIER1) / 2,
        "admitted:\nthe edit\nis in the\nqueue\nbetween\nthe tiers\nuntil the\nmodel\nanswers",
        ha="center",
        bbox=dict(facecolor="white", edgecolor="none", pad=1.0),
        **NOTE,
    )

    # Second lane arrows
    arrow(ax, (MAIN_R, WAIT - 0.55), (MAIN_R, CLOSED + 0.75))
    arrow(ax, (right_tip, CLOSED), (right_box, CLOSED), "yes", offset=(0, 0.22))
    arrow(ax, (MAIN_R, CLOSED - 0.75), (MAIN_R, READ + 0.6), "no", offset=(0.3, 0))
    arrow(ax, (MAIN_R, READ - 0.6), (MAIN_R, READABLE + 0.75))
    path(
        ax,
        [(right_tip, READABLE), (EXIT_R, READABLE), (EXIT_R, ASK + 0.6)],
        "no",
        at=0,
        offset=(-0.92, 0.22),
    )
    arrow(ax, (MAIN_R, READABLE - 0.75), (MAIN_R, ASK + 0.6), "yes", offset=(0.35, 0))
    arrow(ax, (MAIN_R, ASK - 0.6), (MAIN_R, ANSWERED + 0.75))
    path(
        ax,
        [(right_tip, ANSWERED), (EXIT_R, ANSWERED), (EXIT_R, ASK - 0.6)],
        "no",
        at=0,
        offset=(-0.92, 0.22),
    )
    arrow(ax, (MAIN_R, ANSWERED - 0.75), (MAIN_R, GATE + 0.85), "yes", offset=(0.35, 0))
    arrow(ax, (right_tip + 0.2, GATE), (right_box, GATE), "no", offset=(0, 0.22))
    arrow(ax, (MAIN_R, GATE - 0.85), (MAIN_R, COMBINE + 0.6), "yes", offset=(-0.35, 0))
    ax.text(
        MAIN_R + 0.3,
        (GATE - 0.85 + COMBINE + 0.6) / 2 - 0.05,
        "a range given with the category none\nis cleared, and the answer is kept",
        ha="left",
        **NOTE,
    )
    arrow(ax, (MAIN_R, COMBINE - 0.6), (MAIN_R, LOG + 0.6))
    arrow(ax, (MAIN_R, LOG - 0.6), (MAIN_R, QUEUE + 0.6))
    arrow(ax, (right_tip, QUEUE), (EXIT_R - (EXIT_W - 0.5) / 2, QUEUE))

    # Every edit ordered by tier 1 alone is logged too: one line down each margin.
    for step in (MODEL, SLICE, ROOM, BUDGET):
        ax.plot([far_left, LEFT_EDGE], [step, step], color="#333333", linewidth=1.5)
    path(
        ax,
        [(LEFT_EDGE, MODEL), (LEFT_EDGE, LOG), (MAIN_R - MAIN_W / 2, LOG)],
        "its tier-1 score is its risk; the reason, if there is one, goes in the row",
        at=1,
        offset=(-0.6, 0.22),
    )
    for step in (CLOSED, ASK, GATE):
        ax.plot([far_right, RIGHT_EDGE], [step, step], color="#333333", linewidth=1.5)
    path(ax, [(RIGHT_EDGE, CLOSED), (RIGHT_EDGE, LOG), (right_tip, LOG)])

    # 48 hours later: a separate run reads whether the edit was reverted.
    ax.plot([0.2, 19.7], [RULE, RULE], color="#9E9E9E", linewidth=1.0, linestyle=(0, (5, 4)))
    ax.text(
        0.3,
        RULE - 0.3,
        f"{TRUTH_HOURS} hours later, a separate run: was the edit reverted?",
        fontsize=10,
        color="#616161",
        va="center",
    )
    box(
        ax,
        1.75,
        ROW_A,
        "terminal",
        "The wait is over",
        f"{TRUTH_HOURS} hours after the edit",
        w=2.9,
        h=1.2,
        pill=True,
    )
    box(
        ax,
        5.3,
        ROW_A,
        "code",
        "Read the revision's tags",
        f"from the wiki, {TAG_BATCH} revisions a request",
        w=3.4,
        h=1.2,
    )
    diamond(ax, 9.3, ROW_A, "Revision still\nreadable?", w=3.8)
    diamond(ax, 14.0, ROW_A, "Tagged\nmw-reverted?", w=3.8)
    box(
        ax,
        9.3,
        ROW_B,
        "code",
        "Outcome: not found",
        "deleted or hidden since",
        w=3.4,
        h=1.2,
    )
    box(ax, 14.0, ROW_B, "code", "Outcome: kept", "the edit still stands", w=3.2, h=1.2)
    box(ax, 18.0, ROW_B, "code", "Outcome: reverted", "editors undid the edit", w=3.2, h=1.2)
    box(
        ax,
        14.0,
        ROW_C,
        "code",
        "Outcome logged",
        "append-only, with the\ntime it was read",
        w=3.4,
        h=1.2,
    )
    box(
        ax,
        8.9,
        ROW_C,
        "code",
        "Scored against the queue",
        "was a reverted edit near the top? an\nedit that was not found is left out",
        w=4.4,
        h=1.2,
    )
    box(ax, 4.6, ROW_C, "terminal", "End", w=1.6, h=1.0, pill=True)
    arrow(ax, (3.2, ROW_A), (3.6, ROW_A))
    arrow(ax, (7.0, ROW_A), (7.4, ROW_A))
    arrow(ax, (11.2, ROW_A), (12.1, ROW_A), "yes", offset=(0, 0.22))
    arrow(ax, (9.3, ROW_A - 0.75), (9.3, ROW_B + 0.6), "no", offset=(0.3, 0))
    arrow(ax, (14.0, ROW_A - 0.75), (14.0, ROW_B + 0.6), "no", offset=(0.3, 0))
    path(
        ax,
        [(15.9, ROW_A), (18.0, ROW_A), (18.0, ROW_B + 0.6)],
        "yes",
        at=0,
        offset=(0, 0.22),
    )
    arrow(ax, (14.0, ROW_B - 0.6), (14.0, ROW_C + 0.6))
    path(ax, [(9.3, ROW_B - 0.6), (9.3, JOIN), (14.0, JOIN)])
    path(ax, [(18.0, ROW_B - 0.6), (18.0, JOIN), (14.0, JOIN)])
    arrow(ax, (12.3, ROW_C), (11.1, ROW_C))
    arrow(ax, (6.7, ROW_C), (5.4, ROW_C))

    save(fig, out)


if __name__ == "__main__":
    render()
    print(f"Wrote {OUT}")
