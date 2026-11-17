"""Render docs/agent-flow.png: the components and where the data boundary sits.

uv run python scripts/render_flow.py
"""

from __future__ import annotations

from pathlib import Path

from _draw import arrow, box, canvas, legend, path, save

OUT = Path(__file__).resolve().parents[1] / "docs" / "agent-flow.png"
BANK, GATE, MODEL = 2.9, 8.5, 14.1  # x of the three columns
ROWS = (9.0, 7.3, 5.6, 3.9)  # y of the four rows


def render(out: Path = OUT) -> None:
    fig, ax = canvas(17, 11.5)
    ax.text(
        8.5,
        11.0,
        "AML alert investigation agent: components",
        ha="center",
        fontsize=15,
        fontweight="bold",
    )
    ax.text(BANK, 10.1, "Bank side (real data)", ha="center", fontsize=10, color="#1565C0")
    ax.text(GATE, 10.1, "Privacy gateway (the boundary)", ha="center", fontsize=10, color="#00695C")
    ax.text(MODEL, 10.1, "Models (tokens only)", ha="center", fontsize=10, color="#5E35B1")
    r1, r2, r3, r4 = ROWS

    box(
        ax,
        BANK,
        r1,
        "code",
        "Alert intake",
        "risk features and hard flags,\ncomputed on real data",
        h=1.2,
    )
    box(
        ax,
        BANK,
        r2,
        "code",
        "Read-only tools",
        "profile, activity, transactions,\nscreening, media, related parties",
        h=1.2,
    )
    box(
        ax,
        BANK,
        r3,
        "code",
        "Synthetic bank data",
        "SQLite: customers, accounts,\ntransactions, watchlist",
        h=1.2,
    )

    box(
        ax,
        GATE,
        r1,
        "privacy",
        "Tokens and minimisation",
        "CUST_01, TXN_0007 per case;\nfield allow-list per role",
        h=1.2,
    )
    box(
        ax,
        GATE,
        r2,
        "privacy",
        "Free-text scrubbing",
        "known parties become tokens;\nunknown names are redacted",
        h=1.2,
    )
    box(
        ax,
        GATE,
        r3,
        "privacy",
        "Outbound check",
        "every request is scanned;\na hit blocks the call",
        h=1.2,
    )
    box(
        ax,
        GATE,
        r4,
        "privacy",
        "Vault = evidence ledger",
        "only tokens issued in the case\ncan be cited",
        h=1.2,
    )

    box(
        ax,
        MODEL,
        r1,
        "llm",
        "Triage: Haiku 5.5",
        "closes clear false positives\nor escalates",
        h=1.2,
    )
    box(
        ax,
        MODEL,
        r2,
        "llm",
        "Lead: Opus 5.5",
        "plans, checks, decides;\ndelegates bulk reading",
        h=1.2,
    )
    box(
        ax,
        MODEL,
        r3,
        "llm",
        "Workers: Haiku 5.5",
        "transaction digest, screening\ncheck, prior-alert summary",
        h=1.2,
    )
    box(
        ax,
        MODEL,
        r4,
        "code",
        "Provider layer (every model call)",
        "leak guard, record and replay,\nbudget, Anthropic or NVIDIA",
        h=1.2,
    )

    box(
        ax,
        5.6,
        1.9,
        "code",
        "Policy gate",
        "named rules; citations verified;\nno failure produces a close",
        w=5.2,
        h=1.2,
    )
    box(
        ax,
        12.0,
        1.9,
        "human",
        "Analyst queue",
        "real values restored;\napprove or override",
        w=5.2,
        h=1.2,
    )

    # Row 1: the brief goes out as tokens.
    arrow(ax, (5.2, r1), (6.2, r1), "brief", offset=(0, 0.22))
    arrow(ax, (10.8, r1), (11.8, r1), "tokens", offset=(0, 0.22))
    # Row 2: tool calls come in as tokens, results go back scrubbed.
    arrow(ax, (11.8, r2 + 0.25), (10.8, r2 + 0.25), "tool calls", offset=(0, 0.22))
    arrow(ax, (6.2, r2 + 0.25), (5.2, r2 + 0.25))
    arrow(ax, (5.2, r2 - 0.25), (6.2, r2 - 0.25), "results", offset=(0, -0.22))
    arrow(ax, (10.8, r2 - 0.25), (11.8, r2 - 0.25))
    arrow(ax, (BANK, r2 - 0.6), (BANK, r3 + 0.6))
    # Model column.
    arrow(ax, (MODEL, r1 - 0.6), (MODEL, r2 + 0.6), "escalate", offset=(0.75, 0))
    arrow(ax, (MODEL, r2 - 0.6), (MODEL, r3 + 0.6), "delegate", offset=(0.75, 0))
    # Outcome.
    path(
        ax,
        [(11.8, r2 - 0.45), (11.3, r2 - 0.45), (11.3, 2.9), (7.9, 2.9), (7.9, 2.5)],
        "disposition",
        at=2,
        offset=(0, -0.2),
    )
    arrow(ax, (6.9, r4 - 0.6), (6.9, 2.5), "issued tokens", offset=(-0.95, 0))
    arrow(ax, (8.2, 1.9), (9.4, 1.9))

    legend(
        ax,
        2.5,
        0.5,
        [("code", "Code"), ("privacy", "Privacy"), ("llm", "Model call"), ("human", "Person")],
    )
    save(fig, out)


if __name__ == "__main__":
    render()
    print(f"Wrote {OUT}")
