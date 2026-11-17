"""One alert, end to end: features, routing, triage, lead with workers, policy gate."""

from __future__ import annotations

import re
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from . import features, tools
from .agent import LoopResult, run_loop
from .bankdb import BankDB
from .llm import BudgetExceeded, CassetteMiss, LeakBlocked, LLMProvider
from .models import Alert, CaseResult, Disposition, RuleHit, Step, parse_submission, parse_triage
from .policy import RULES, Policy, gate, triage_close_hits
from .privacy import Gateway, PartyIndex, Vault
from .roles import ModelConfig, lead_role, triage_role
from .tools import CaseContext, ToolError
from .workers import WorkerReplyError, run_worker

MAX_BRIEF_ROWS = 15


def build_brief(ctx: CaseContext, flags: list[str], triage_notes: str | None = None) -> str:
    alert, db = ctx.alert, ctx.db
    owner = db.customer(alert.customer_id)
    rows = [db.transaction(t) for t in alert.txn_ids[:MAX_BRIEF_ROWS]]
    activity = features.activity_summary(
        db,
        alert.account_id,
        alert.window_start,
        alert.window_end,
        owner["expected_monthly_turnover"],
        ctx.policy,
    )
    activity["top_counterparties"] = [
        {"counterparty": ctx.gateway.tok("CPTY", row.pop("counterparty_id")), **row}
        for row in activity["top_counterparties"]
    ]
    brief = {
        "alert": ctx.gateway.alert_view(alert),
        "alert_transactions": [ctx.gateway.txn_view(r) for r in rows],
        "window_activity": activity,
        "hard_flags": flags,
    }
    if triage_notes is not None:
        brief["triage_notes"] = triage_notes
    return tools.dumps(brief)


TOKEN_RE = re.compile(r"\b[A-Z]+_\d+\b")


class _Shown:
    """The tokens one model was actually sent in this case: its brief and its tool results.

    A finding may cite only these. A token issued in the case but shown only to another
    model (a worker's raw rows, say) is not evidence the citing model can have read.
    """

    def __init__(self, vault: Vault):
        self.vault = vault
        self.tokens: set[str] = set()

    def add(self, text: str) -> str:
        self.tokens |= set(TOKEN_RE.findall(text)) & self.vault.issued()
        return text


@dataclass
class _Case:
    """Mutable state shared by the lead's tool calls."""

    worker_steps: list[Step | None] = field(default_factory=list)  # one per delegate call
    delegations: int = 0
    failure: str | None = None

    def cost(self) -> float:
        return sum(s.cost_usd for s in self.worker_steps if s is not None)


def _merge(case: _Case, loop: LoopResult) -> list[Step]:
    """Loop steps and the worker calls they triggered, in order of occurrence.

    Every delegate call has one entry in `worker_steps`: the worker's step, or None
    when no model call was made for it (limit reached, bad arguments, blocked).
    """
    workers = list(case.worker_steps)
    ordered: list[Step] = []
    for step in loop.steps:
        ordered.append(step)
        for call in step.tool_calls:
            if call.name == "delegate" and workers:
                worker = workers.pop(0)
                if worker is not None:
                    ordered.append(worker)
    return ordered + [w for w in workers if w is not None]


def _watch(shown: _Shown, execute):
    """Wrap a tool executor so every result the model receives is added to `shown`."""

    def run(name: str, tool_input: dict) -> tuple[str, bool]:
        output, is_error = execute(name, tool_input)
        return shown.add(output), is_error

    return run


def investigate(
    alert: Alert,
    *,
    db: BankDB,
    provider: LLMProvider,
    cfg: ModelConfig,
    policy: Policy | None = None,
    mode: str = "assisted",
    index: PartyIndex | None = None,
) -> tuple[CaseResult, Vault]:
    policy = policy or Policy()
    vault = Vault(alert.alert_id)
    ctx = CaseContext(db, alert, vault, Gateway(db, vault, alert.as_of, index), policy)
    flags = features.hard_flags(db, alert, policy)
    done: list[Step] = []
    notes = ""
    shown = {"triage": _Shown(vault), "lead": _Shown(vault)}

    def finish(
        tier: str, disposition: Disposition | None, failure: str | None
    ) -> tuple[CaseResult, Vault]:
        used = any(entry["granted"] for entry in ctx.sensitive_log)
        queue, rules = gate(
            disposition,
            tier=tier,
            hard_flags=flags,
            shown=shown[tier].tokens,
            failure=failure,
            sensitive_used=used,
            mode=mode,
            alert_id=alert.alert_id,
            policy=policy,
            instruction_in_data=bool(ctx.gateway.instructions_seen),
        )
        keep = None if failure else disposition  # a rejected output is kept for the analyst to see
        return CaseResult(
            alert_id=alert.alert_id,
            config=cfg.name,
            tier=tier,
            disposition=keep,
            queue=queue,
            rules=rules,
            hard_flags=flags,
            steps=done,
            sensitive_requests=ctx.sensitive_log,
            triage_notes=notes,
            cost_usd=round(sum(s.cost_usd for s in done), 6),
            latency_s=round(sum(s.latency_s for s in done), 3),
        ), vault

    # --- tier 1: triage (skipped on a hard flag, or when the configuration has no tier 1)
    if not flags and cfg.triage:
        loop = run_loop(
            provider,
            triage_role(cfg),
            shown["triage"].add(build_brief(ctx, flags)),
            _watch(shown["triage"], lambda name, args: tools.execute(ctx, "triage", name, args)),
            parse_triage,
            tag=f"{alert.alert_id}/triage",
        )
        done.extend(loop.steps)
        if loop.failure:
            return finish("triage", None, loop.failure)
        disposition, notes = loop.submission
        if disposition is not None:
            hits = triage_close_hits(disposition, shown["triage"].tokens, policy)
            if not hits:
                return finish("triage", disposition, None)
            notes = "Triage proposed a close that policy rejected: " + ", ".join(
                h.code for h in hits
            )

    # --- tier 2: lead investigator with workers ---------------------------------------
    case = _Case()

    def execute(name: str, tool_input: dict) -> tuple[str, bool]:
        if name != "delegate":
            return tools.execute(ctx, "lead", name, tool_input)
        # Tool calls of one case run one after another, so this state needs no lock.
        case.delegations += 1
        case.worker_steps.append(None)  # replaced below if a worker call was paid for
        if case.delegations > policy.max_delegations:
            case.failure = "limit"
            return "Delegation limit reached.", True
        try:
            digest, step = run_worker(
                provider, cfg, ctx, tag=f"{alert.alert_id}/lead", **tool_input
            )
        except WorkerReplyError as exc:  # paid for and unusable: keep the step
            case.worker_steps[-1] = exc.step
            return str(exc), True
        except (ToolError, TypeError) as exc:
            return str(exc), True
        except LeakBlocked:
            case.failure = "leak_blocked"
            return "Blocked by the outbound check.", True
        case.worker_steps[-1] = step
        return tools.dumps(digest), False

    spent = sum(s.cost_usd for s in done)
    live: list[Step] = []  # the lead's own steps, visible to cost_left while the loop runs

    def cost_left() -> float:
        if case.failure:  # the case is already bound for a person: stop paying for it
            return -1.0
        return policy.case_cost_limit_usd - spent - case.cost() - sum(s.cost_usd for s in live)

    loop = run_loop(
        provider,
        lead_role(cfg),
        shown["lead"].add(build_brief(ctx, flags, notes)),
        _watch(shown["lead"], execute),
        parse_submission,
        tag=f"{alert.alert_id}/lead",
        cost_left=cost_left,
        on_step=live.append,
    )
    done.extend(_merge(case, loop))
    return finish("lead", loop.submission, case.failure or loop.failure)


@dataclass
class RunReport:
    cases: list[tuple[CaseResult, Vault]]
    stopped: str | None = None


def run_alerts(
    alerts: list[Alert],
    *,
    db: BankDB,
    provider: LLMProvider,
    cfg: ModelConfig,
    policy: Policy | None = None,
    mode: str = "assisted",
    concurrency: int = 4,
) -> RunReport:
    """Investigate many alerts. A budget stop ends the run and keeps finished cases."""
    index = PartyIndex(db)
    report = RunReport(cases=[])
    stop = threading.Event()

    def one(alert: Alert):
        if stop.is_set():
            return None
        try:
            return investigate(
                alert, db=db, provider=provider, cfg=cfg, policy=policy, mode=mode, index=index
            )
        except BudgetExceeded as exc:
            stop.set()
            report.stopped = str(exc)
            return None
        except CassetteMiss:
            raise  # nothing recorded for this configuration: the caller decides
        except Exception as exc:  # one broken case must not lose the finished ones
            rule = RuleHit(
                code="INTERNAL_ERROR",
                message=f"{RULES['INTERNAL_ERROR']} ({type(exc).__name__})",
            )
            failed = CaseResult(
                alert_id=alert.alert_id,
                config=cfg.name,
                tier="lead",
                disposition=None,
                queue="manual_investigation",
                rules=[rule],
            )
            return failed, Vault(alert.alert_id)

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        report.cases = [case for case in pool.map(one, alerts) if case is not None]
    return report
