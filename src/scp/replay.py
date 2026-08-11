"""Replay recorded decisions, and detect version skew rather than absorbing it.

Two modes, and they are never pooled:

* **PINNED** -- re-run with the evaluator and policy versions recorded on each decision.
  Agreement must be 100%. Anything less means the decision path is nondeterministic and no
  replay claim is possible at all.
* **SKEWED** -- re-run with today's evaluator and policy. Agreement may legitimately be below
  100%, and every disagreement must be *attributed* to a named version change.

The failure this guards against is the one found on this machine as D087: a replay engine
that reported agreement without computing it. ``replay()`` therefore recomputes the decision
from the recorded event content and compares; it never reads a stored agreement flag.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .evaluator import Evaluator
from .events import SafetyEvent
from .ledger import Chain
from .policy import Policy


@dataclass
class ReplayRow:
    event_id: str
    recorded_decision: str
    replayed_decision: str
    agrees: bool
    recorded_versions: tuple[str, str]
    replay_versions: tuple[str, str]
    attribution: str | None = None


@dataclass
class ReplayReport:
    mode: str
    rows: list[ReplayRow] = field(default_factory=list)
    skipped_no_input: int = 0

    @property
    def n(self) -> int:
        return len(self.rows)

    @property
    def n_agree(self) -> int:
        return sum(1 for r in self.rows if r.agrees)

    def disagreements(self) -> list[ReplayRow]:
        return [r for r in self.rows if not r.agrees]

    def attribution_summary(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in self.disagreements():
            key = r.attribution or "UNATTRIBUTED"
            out[key] = out.get(key, 0) + 1
        return out


def replay(
    decision_chain: Chain,
    events_by_id: dict[str, SafetyEvent],
    *,
    evaluator: Evaluator,
    policy: Policy,
    mode: str,
) -> ReplayReport:
    """Recompute each recorded decision from the event and compare. Never trusts a flag."""
    rep = ReplayReport(mode=mode)
    for rec in decision_chain.records:
        if rec.get("record_type") != "scp.decision":
            continue
        # Only decisions that actually reached a policy verdict are replayable. NO_DECISION
        # and outage refusals were produced by the fail-mode, not by the policy, so replaying
        # them would be replaying the deadline, not the decision.
        if rec.get("outcome") not in ("DECIDED", "DECIDED_BACKLOG"):
            continue
        ev = events_by_id.get(rec["event_id"])
        if ev is None:
            rep.skipped_no_input += 1
            continue

        v = evaluator.evaluate(ev.visible())
        replayed = policy.decide(v.risk).decision.value
        recorded = rec["decision"]
        agrees = replayed == recorded

        attribution = None
        if not agrees:
            rec_v = (rec.get("evaluator_version"), rec.get("policy_version"))
            now_v = (evaluator.version, policy.version)
            changed = [
                f"{name}: {a} -> {b}"
                for name, a, b in (("evaluator", rec_v[0], now_v[0]),
                                   ("policy", rec_v[1], now_v[1]))
                if a != b
            ]
            attribution = "; ".join(changed) if changed else "UNATTRIBUTED (versions identical)"

        rep.rows.append(ReplayRow(
            event_id=rec["event_id"],
            recorded_decision=recorded,
            replayed_decision=replayed,
            agrees=agrees,
            recorded_versions=(rec.get("evaluator_version"), rec.get("policy_version")),
            replay_versions=(evaluator.version, policy.version),
            attribution=attribution,
        ))
    return rep
