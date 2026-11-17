"""Render docs/agent-flowchart.png: every decision for one new issue, and every exit to a person.

uv run python scripts/render_flowchart.py
"""

from __future__ import annotations

from pathlib import Path

from _draw import arrow, box, canvas, diamond, legend, path, save

OUT = Path(__file__).resolve().parents[1] / "docs" / "agent-flowchart.png"

# Figures shown in the labels. They mirror triagedesk.yaml (candidates, model_candidates,
# truth_after_days) and gate.MAX_PER_GROUP.
CANDIDATES, SHOWN, TRUTH_DAYS, MAX_PER_GROUP = 20, 8, 14, 3

# x of the four lanes: the issue and the model, what a "no" leaves there, the gate,
# and what the gate's answers lead to.
MAIN, SIDE, GATE, EXIT = 2.5, 7.1, 12.3, 17.2
BUS = 9.45  # x of the line that carries every answer up to the gate
EDGE = 19.55  # x of the line down the right margin
MAIN_W, SIDE_W, GATE_W, EXIT_W = 4.4, 3.6, 4.4, 4.0

HEIGHT = 25.7
Y0 = HEIGHT - 3.1  # y of the start

# y of each step in the left lane, top to bottom.
STORE, SEEN, SEARCH, CLASSIFY = Y0 - 1.6, Y0 - 3.45, Y0 - 5.3, Y0 - 7.05
KEY, CAP, MODEL, ANSWERED = Y0 - 8.9, Y0 - 10.95, Y0 - 12.8, Y0 - 14.65
WRAP = Y0 - 15.85  # y of the line that takes the model's answer across to the bus

# y of each step in the gate lane, top to bottom.
LABELS, NAMED, SHOWN_OLDER, SURE = Y0 - 0.6, Y0 - 2.8, Y0 - 5.0, Y0 - 7.35
ALONE, LOG, APPLY, END = Y0 - 9.3, Y0 - 11.0, Y0 - 12.95, Y0 - 14.9
JOIN_NAMED = NAMED + 0.65 + 0.22  # where the first gate exit rejoins the lane
JOIN_SURE = SURE + 0.85 + 0.22  # where "no duplicate named" and the second exit rejoin

# The second run, below the dashed line: three rows.
RULE = Y0 - 16.5
ROW_A, ROW_B, ROW_C = Y0 - 18.0, Y0 - 19.95, Y0 - 21.75


def render(out: Path = OUT) -> None:
    fig, ax = canvas(19.9, HEIGHT)
    ax.text(
        9.95,
        HEIGHT - 0.55,
        "Decision flow for one new issue",
        ha="center",
        fontsize=16,
        fontweight="bold",
    )
    ax.text(
        9.95,
        HEIGHT - 1.05,
        "Every answer passes the gate, and every answer is logged. The chart follows the desk's own answer; "
        "a rules row and a classifier row are logged beside it as baselines.",
        ha="center",
        fontsize=10,
        color="#5E35B1",
    )
    legend(
        ax,
        3.35,
        HEIGHT - 1.9,
        [
            ("code", "Code"),
            ("ml", "Classical ML"),
            ("local", "Local model"),
            ("llm", "Paid model call"),
            ("human", "Person"),
        ],
        step=3.3,
        w=3.0,
    )

    # Left lane: the issue arrives, earlier issues are found, the model is asked.
    box(ax, MAIN, Y0, "terminal", "A new issue is fetched", w=MAIN_W, pill=True)
    box(
        ax,
        MAIN,
        STORE,
        "code",
        "Store it as first seen",
        "title, text, labels and state as they are now",
        w=MAIN_W,
        h=1.1,
    )
    diamond(ax, MAIN, SEEN, "Tracker already shows\na taxonomy label\nor a close?", w=MAIN_W)
    box(
        ax,
        MAIN,
        SEARCH,
        "local",
        "Search earlier issues",
        f"words and vectors, only issues opened\nbefore this one; the best {CANDIDATES} are kept",
        w=MAIN_W,
        h=1.2,
    )
    box(
        ax,
        MAIN,
        CLASSIFY,
        "ml",
        "Classifier scores every label",
        "and names the most similar earlier\nissue, if it is similar enough",
        w=MAIN_W,
        h=1.2,
    )
    diamond(ax, MAIN, KEY, "A model key is set?", w=MAIN_W)
    diamond(ax, MAIN, CAP, "Today's spend under\nthe daily cap?", w=MAIN_W)
    box(
        ax,
        MAIN,
        MODEL,
        "llm",
        "Model reads the issue (Haiku 5.5)",
        f"with the label definitions and {SHOWN} earlier\nissues: labels, and a duplicate or none",
        w=MAIN_W,
        h=1.2,
    )
    diamond(ax, MAIN, ANSWERED, "Model gave a\nusable answer?", w=MAIN_W)

    # What a "yes" or a "no" in the left lane leaves behind.
    box(
        ax,
        SIDE,
        SEEN,
        "code",
        "Row flagged seen first",
        "answered like any other issue,\nbut left out of the live scores",
        w=SIDE_W,
        h=1.2,
    )
    box(
        ax,
        SIDE,
        KEY,
        "code",
        "No model row is written",
        "the classifier's answer\nis the desk's answer",
        w=SIDE_W,
        h=1.2,
    )
    box(
        ax,
        SIDE,
        MODEL,
        "human",
        "A person will look",
        "reason: no model answer; the\nclassifier's answer is used",
        w=SIDE_W,
        h=1.2,
    )

    # Gate lane: the checks in code, then the route, the log and the tracker.
    diamond(
        ax,
        GATE,
        LABELS,
        f"Every label in the\ntaxonomy, at most\n{MAX_PER_GROUP} of one kind?",
        w=GATE_W,
    )
    diamond(ax, GATE, NAMED, "A duplicate named?", w=GATE_W, h=1.3)
    diamond(
        ax,
        GATE,
        SHOWN_OLDER,
        "One of the earlier\nissues shown, and older\nthan this one?",
        w=GATE_W,
    )
    diamond(
        ax,
        GATE,
        SURE,
        "A high-confidence area label,\nno label or duplicate it is\nunsure of, no reason noted?",
        w=GATE_W + 0.4,
        h=1.7,
    )
    box(ax, GATE, ALONE, "code", "Handled alone", "no person is asked", w=GATE_W, h=1.0)
    box(
        ax,
        GATE,
        LOG,
        "code",
        "Write the log row",
        "append-only: the issue's number and\nthe answer, no issue text",
        w=GATE_W,
        h=1.2,
    )
    diamond(
        ax,
        GATE,
        APPLY,
        "Run started in apply mode?\n(it starts only for a repository\non the allow-list)",
        w=GATE_W + 0.4,
        h=1.7,
    )
    box(
        ax,
        GATE,
        END,
        "terminal",
        "Prediction logged",
        "end of the hourly part",
        w=GATE_W,
        h=1.1,
        pill=True,
    )

    # Where the gate's answers lead.
    box(
        ax,
        EXIT,
        LABELS,
        "human",
        "A person will look",
        "reason: the gate changed the answer;\nthe label is dropped",
        w=EXIT_W,
        h=1.2,
    )
    box(
        ax,
        EXIT,
        SHOWN_OLDER,
        "human",
        "A person will look",
        "reason: the gate changed the answer;\nthe duplicate is dropped",
        w=EXIT_W,
        h=1.2,
    )
    box(
        ax,
        EXIT,
        SURE,
        "human",
        "To a person, with the reasons",
        "no area label it is sure of, a label or a\nduplicate it is unsure of, or one noted above",
        w=EXIT_W,
        h=1.3,
    )
    box(
        ax,
        EXIT,
        APPLY,
        "code",
        "Write to the tracker",
        "add the high-confidence labels, one\ncomment for a sure duplicate; never close;\nnothing if the gate changed the answer",
        w=EXIT_W,
        h=1.2,
    )

    main_right, side_left, side_right = MAIN + MAIN_W / 2, SIDE - SIDE_W / 2, SIDE + SIDE_W / 2
    gate_left, gate_right, exit_left = GATE - GATE_W / 2, GATE + GATE_W / 2, EXIT - EXIT_W / 2

    # Left lane arrows
    arrow(ax, (MAIN, Y0 - 0.5), (MAIN, STORE + 0.55))
    arrow(ax, (MAIN, STORE - 0.55), (MAIN, SEEN + 0.75))
    arrow(ax, (main_right, SEEN), (side_left, SEEN), "yes", offset=(0, 0.22))
    arrow(ax, (MAIN, SEEN - 0.75), (MAIN, SEARCH + 0.6), "no", offset=(0.3, 0))
    path(ax, [(SIDE, SEEN - 0.6), (SIDE, SEARCH), (main_right, SEARCH)])
    arrow(ax, (MAIN, SEARCH - 0.6), (MAIN, CLASSIFY + 0.6))
    arrow(ax, (MAIN, CLASSIFY - 0.6), (MAIN, KEY + 0.75))
    arrow(ax, (main_right, KEY), (side_left, KEY), "no", offset=(0, 0.22))
    arrow(ax, (MAIN, KEY - 0.75), (MAIN, CAP + 0.75), "yes", offset=(0.35, 0))
    path(
        ax, [(main_right, CAP), (SIDE, CAP), (SIDE, MODEL + 0.6)], "no", at=0, offset=(-0.85, 0.22)
    )
    arrow(ax, (MAIN, CAP - 0.75), (MAIN, MODEL + 0.6), "yes", offset=(0.35, 0))
    arrow(ax, (MAIN, MODEL - 0.6), (MAIN, ANSWERED + 0.75))
    path(
        ax,
        [(main_right, ANSWERED), (SIDE, ANSWERED), (SIDE, MODEL - 0.6)],
        "no",
        at=0,
        offset=(-0.85, 0.22),
    )

    # Every answer goes up one line to the gate.
    path(
        ax,
        [(MAIN, ANSWERED - 0.75), (MAIN, WRAP), (BUS, WRAP), (BUS, LABELS), (gate_left, LABELS)],
        "yes",
        at=0,
        offset=(0.35, 0.1),
    )
    arrow(ax, (side_right, KEY), (BUS, KEY))
    arrow(ax, (side_right, MODEL), (BUS, MODEL))
    ax.text(
        BUS,
        (SEEN + KEY) / 2,
        "a model\nlabel stays\nsure only\nif the\nclassifier\nagrees;\nthen the\ngate",
        ha="center",
        va="center",
        fontsize=8.5,
        style="italic",
        color="#444444",
        bbox=dict(facecolor="white", edgecolor="none", pad=1.0),
    )

    # Gate lane arrows
    arrow(ax, (gate_right, LABELS), (exit_left, LABELS), "no", offset=(0, 0.22))
    arrow(ax, (GATE, LABELS - 0.75), (GATE, NAMED + 0.65), "yes", offset=(-0.35, 0.12))
    path(ax, [(EXIT, LABELS - 0.6), (EXIT, JOIN_NAMED), (GATE, JOIN_NAMED)])
    arrow(ax, (GATE, NAMED - 0.65), (GATE, SHOWN_OLDER + 0.75), "yes", offset=(-0.35, 0))
    path(
        ax,
        [(gate_right, NAMED), (EDGE, NAMED), (EDGE, JOIN_SURE), (GATE, JOIN_SURE)],
        "no",
        at=0,
        offset=(-2.2, 0.22),
    )
    arrow(ax, (gate_right, SHOWN_OLDER), (exit_left, SHOWN_OLDER), "no", offset=(0, 0.22))
    arrow(ax, (EXIT, SHOWN_OLDER - 0.6), (EXIT, JOIN_SURE))
    arrow(ax, (GATE, SHOWN_OLDER - 0.75), (GATE, SURE + 0.85), "yes", offset=(-0.35, 0.12))
    arrow(ax, (gate_right + 0.2, SURE), (exit_left, SURE), "no", offset=(0, 0.22))
    arrow(ax, (GATE, SURE - 0.85), (GATE, ALONE + 0.5), "yes", offset=(0.35, 0))
    arrow(ax, (GATE, ALONE - 0.5), (GATE, LOG + 0.6))
    path(ax, [(EXIT, SURE - 0.65), (EXIT, LOG), (gate_right, LOG)])
    arrow(ax, (GATE, LOG - 0.6), (GATE, APPLY + 0.85))
    arrow(ax, (gate_right + 0.2, APPLY), (exit_left, APPLY), "yes", offset=(0, 0.22))
    arrow(
        ax,
        (GATE, APPLY - 0.85),
        (GATE, END + 0.55),
        "no: shadow mode, the default",
        offset=(1.85, 0),
    )
    path(ax, [(EXIT, APPLY - 0.6), (EXIT, END), (gate_right, END)])

    # Fourteen days later: a separate run reads what the maintainers did.
    ax.plot([0.2, 19.7], [RULE, RULE], color="#9E9E9E", linewidth=1.0, linestyle=(0, (5, 4)))
    ax.text(
        0.3,
        RULE - 0.3,
        f"{TRUTH_DAYS} days later, a separate run: what the maintainers did with the issue",
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
        f"{TRUTH_DAYS} days after it was opened",
        w=2.9,
        h=1.2,
        pill=True,
    )
    box(
        ax,
        5.25,
        ROW_A,
        "code",
        "Read the issue again",
        "its labels and its close reason",
        w=3.3,
        h=1.2,
    )
    diamond(ax, 8.9, ROW_A, "Closed as a\nduplicate?", w=3.4)
    box(
        ax,
        16.2,
        ROW_A,
        "code",
        "Read the issue's timeline",
        "the original is named in a comment, not in a field; if the\n"
        "request quota runs out, the timeline is read on a later run",
        w=6.0,
        h=1.2,
    )
    diamond(ax, 17.3, ROW_B, "One earlier issue of\nthis tracker named?", w=3.8)
    box(
        ax,
        12.9,
        ROW_B,
        "code",
        "Original found",
        "the outcome names\nthe earlier issue",
        w=3.4,
        h=1.2,
    )
    box(
        ax,
        8.9,
        ROW_B,
        "code",
        "Outcome logged",
        "append-only, with the\ntime it was read",
        w=3.2,
        h=1.2,
    )
    box(
        ax,
        4.6,
        ROW_B,
        "code",
        "Scored against the prediction",
        "rows flagged seen first are left\nout of the live scores",
        w=4.2,
        h=1.2,
    )
    box(ax, 1.15, ROW_B, "terminal", "End", w=1.5, h=1.0, pill=True)
    box(
        ax,
        17.3,
        ROW_C,
        "code",
        "Original not found",
        "outcome marked outside or unresolved;\nleft out of duplicate recall",
        w=3.8,
        h=1.2,
    )
    arrow(ax, (3.2, ROW_A), (3.6, ROW_A))
    arrow(ax, (6.9, ROW_A), (7.2, ROW_A))
    arrow(ax, (10.6, ROW_A), (13.2, ROW_A), "yes", offset=(0, 0.22))
    arrow(ax, (8.9, ROW_A - 0.75), (8.9, ROW_B + 0.6), "no", offset=(0.3, 0))
    arrow(ax, (17.3, ROW_A - 0.6), (17.3, ROW_B + 0.75))
    arrow(ax, (15.4, ROW_B), (14.6, ROW_B), "yes", offset=(0, 0.22))
    arrow(ax, (11.2, ROW_B), (10.5, ROW_B))
    arrow(ax, (7.3, ROW_B), (6.7, ROW_B))
    arrow(ax, (2.5, ROW_B), (1.9, ROW_B))
    arrow(ax, (17.3, ROW_B - 0.75), (17.3, ROW_C + 0.6), "no", offset=(0.3, 0))
    path(ax, [(15.4, ROW_C), (8.9, ROW_C), (8.9, ROW_B - 0.6)])

    save(fig, out)


if __name__ == "__main__":
    render()
    print(f"Wrote {OUT}")
