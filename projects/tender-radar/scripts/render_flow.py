"""Render docs/agent-flow.png: the components, and which of them call a model.

uv run python scripts/render_flow.py
"""

from __future__ import annotations

from pathlib import Path

from _draw import arrow, box, canvas, legend, path, save

OUT = Path(__file__).resolve().parents[1] / "docs" / "agent-flow.png"

# Figures shown in the labels. They mirror src/tenderradar: panel.PANEL_SIZE,
# config.Settings.list_size, config.Settings.assess_top and feed.TedClient(max_attempts).
PANEL_SIZE, LIST_SIZE, ASSESS_TOP, FEED_ATTEMPTS = 200, 15, 10, 5

HISTORY, RUN, SIDE = 3.1, 9.0, 14.9  # x of the three columns
ROWS = (14.65, 12.9, 11.15, 9.4, 7.65, 5.9, 4.15, 1.95)  # y of the eight rows
H = 1.2  # box height
LEFT, RIGHT = 2.3, 2.3  # half a box width


def render(out: Path = OUT) -> None:
    fig, ax = canvas(17.7, 16.9)
    ax.text(8.85, 16.35, "Tender radar: components", ha="center", fontsize=15, fontweight="bold")
    ax.text(
        8.85,
        15.82,
        "One step calls a paid model. The embedding model runs on this machine. The rest is code.",
        ha="center",
        fontsize=10,
        color="#5E35B1",
    )
    r1, r2, r3, r4, r5, r6, r7, r8 = ROWS

    # Left column: the feed's history, and who the lists are written for.
    box(
        ax,
        HISTORY,
        r1,
        "terminal",
        "TED feed",
        "EU public tenders and award notices,\nlive search API, no key",
        h=H,
    )
    box(
        ax,
        HISTORY,
        r2,
        "code",
        "Supplier identity",
        "who won what: spellings of one\ncompany grouped into one supplier",
        h=H,
    )
    box(
        ax,
        HISTORY,
        r3,
        "code",
        "Panel profiles",
        f"{PANEL_SIZE} real suppliers, hashed ids;\ncategories, buyers, text of past wins",
        h=H,
    )
    box(
        ax,
        HISTORY,
        r4,
        "code",
        "Client profile",
        "one fictional client,\nwritten by hand in YAML",
        h=H,
    )
    box(
        ax,
        HISTORY,
        r8,
        "code",
        "Outcome log",
        "append-only: who won, and the day\nthe award notice arrived",
        h=H,
    )

    # Middle column: one morning's run, top to bottom.
    box(
        ax,
        RUN,
        r1,
        "code",
        "Feed client",
        f"one request at a time, paced;\nup to {FEED_ATTEMPTS} attempts per request",
        h=H,
    )
    box(
        ax,
        RUN,
        r2,
        "code",
        "Local store",
        "SQLite: tenders and award notices\nby publication day",
        h=H,
    )
    box(
        ax,
        RUN,
        r3,
        "code",
        "Hard rules",
        "deadline, territory, value band,\nprocedure; removals counted by rule",
        h=H,
    )
    box(
        ax,
        RUN,
        r4,
        "code",
        "Rankers",
        "category alert (the baseline), weighted\ncategories, embeddings, the two fused",
        h=H,
    )
    box(
        ax,
        RUN,
        r5,
        "llm",
        "Model assessment: Claude Haiku 5.5",
        f"the only paid model call: reads the\nfirst {ASSESS_TOP} tenders of the client's list",
        h=H,
    )
    box(
        ax,
        RUN,
        r6,
        "code",
        "Gate",
        "every quoted passage must be in the notice;\ndeadline and value come from its fields",
        h=H,
    )
    box(
        ax,
        RUN,
        r7,
        "code",
        "Prediction log",
        "append-only: every list, with\nthe time it was written",
        h=H,
    )
    box(
        ax,
        RUN,
        r8,
        "code",
        "Scoring",
        "joins lists with outcomes; a list counts\nonly if written before the award",
        h=H,
    )

    # Right column: side checks, the local model, and the pages people read.
    box(
        ax,
        SIDE,
        r1,
        "code",
        "Feed health checks",
        "feed answers, records parse,\nfeed is fresh, volume is usual",
        h=H,
    )
    box(
        ax,
        SIDE,
        r2,
        "human",
        "Operator",
        "a failed check fails the run:\nfeed broken or format changed",
        h=H,
    )
    box(
        ax,
        SIDE,
        r4,
        "local",
        "Multilingual embeddings",
        "local model on a CPU;\nno key, not a paid model",
        h=H,
    )
    box(
        ax,
        SIDE,
        r5,
        "code",
        "Daily spend cap",
        "checked before every model call;\nat the cap, tenders are listed unread",
        h=H,
    )
    box(
        ax,
        SIDE,
        r7,
        "human",
        "Morning list page",
        f"at most {LIST_SIZE} tenders; the bid manager\nmarks bid or skip, with a reason",
        h=H,
    )
    box(
        ax,
        SIDE,
        r8,
        "code",
        "Scoreboard page",
        "each ranker next to the baseline,\nlists scored and waiting, cost, feed health",
        h=H,
    )

    half = H / 2
    h_right, r_left, r_right, s_left = HISTORY + RIGHT, RUN - LEFT, RUN + RIGHT, SIDE - LEFT

    # Row 1: the feed is read and checked.
    arrow(ax, (h_right, r1), (r_left, r1), "notices", offset=(0, 0.22))
    arrow(ax, (r_right, r1), (s_left, r1), "result", offset=(0, 0.22))
    arrow(ax, (SIDE, r1 - half), (SIDE, r2 + half), "a check fails", offset=(1.0, 0))
    arrow(ax, (RUN, r1 - half), (RUN, r2 + half))

    # From the store: awards build the profiles, today's tenders start the run.
    arrow(ax, (r_left, r2), (h_right, r2), "awards", offset=(0, 0.22))
    arrow(ax, (HISTORY, r2 - half), (HISTORY, r3 + half), "past wins", offset=(0.85, 0))
    arrow(ax, (RUN, r2 - half), (RUN, r3 + half), "today's tenders", offset=(1.2, 0))

    # Both profiles go to the rules, the rankers and the model.
    bus = (h_right + r_left) / 2
    arrow(ax, (h_right, r3), (r_left, r3))
    arrow(ax, (h_right, r4), (r_left, r4))
    path(ax, [(bus, r3), (bus, r5), (r_left, r5)], "profiles", at=0, offset=(0, (r5 - r4) / 2))

    # The run, top to bottom.
    arrow(ax, (RUN, r3 - half), (RUN, r4 + half), "tenders that pass", offset=(1.3, 0))
    arrow(ax, (RUN, r4 - half), (RUN, r5 + half), f"the first {LIST_SIZE}", offset=(0.95, 0))
    arrow(ax, (RUN, r5 - half), (RUN, r6 + half), "assessment with quotes", offset=(1.6, 0))
    arrow(ax, (RUN, r6 - half), (RUN, r7 + half), "the list", offset=(0.75, 0))
    arrow(ax, (s_left, r4), (r_right, r4), "vectors", offset=(0, 0.22))
    arrow(ax, (s_left, r5), (r_right, r5), "cap reached?", offset=(0, 0.22))
    arrow(ax, (r_right, r7), (s_left, r7), "client's list", offset=(0, 0.22))

    # Months later: award notices name the winners, and the lists are scored.
    ax.plot([0.8, 17.2], [3.1, 3.1], color="#9E9E9E", linewidth=1.0, linestyle=(0, (5, 4)))
    ax.text(
        0.95,
        2.83,
        "Months later, when the award notices are published",
        fontsize=10,
        color="#616161",
        va="center",
    )
    path(
        ax,
        [(HISTORY - LEFT, r2), (0.4, r2), (0.4, r8), (HISTORY - LEFT, r8)],
        "award notices that name a\npanel supplier as the winner\nof a listed tender",
        at=1,
        offset=(1.55, -1.35),
    )
    arrow(ax, (RUN, r7 - half), (RUN, r8 + half), "every list", offset=(0.85, 0.3))
    arrow(ax, (h_right, r8), (r_left, r8), "outcomes", offset=(0, 0.22))
    arrow(ax, (r_right, r8), (s_left, r8), "scores", offset=(0, 0.22))

    legend(
        ax,
        2.6,
        0.55,
        [
            ("code", "Code, no model"),
            ("local", "Local model"),
            ("llm", "Paid model call"),
            ("human", "Person"),
        ],
        step=3.3,
        w=3.0,
    )
    save(fig, out)


if __name__ == "__main__":
    render()
    print(f"Wrote {OUT}")
