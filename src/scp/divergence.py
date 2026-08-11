"""The two-writer join: does what the system SAID match what actually HAPPENED?

This is the operationalisation of the research question. The safeguards service writes
"I decided BLOCK" into the decision chain; the effector independently writes "I executed"
into the effect chain. Telemetry -- like every real dashboard -- is built on the decision
chain, because that is the log the safeguards service owns.

The join is what makes the gap visible. Without a second, independent writer the dashboard
is comparing the decision to itself, and that comparison can only ever agree.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .ledger import Chain


class DivergenceKind(str, Enum):
    #: telemetry shows the action was prevented; the effector recorded that it ran.
    REPORTED_PREVENTED_BUT_EXECUTED = "reported_prevented_but_executed"
    #: telemetry shows the action was permitted; the effector recorded that it did not run.
    REPORTED_PERMITTED_BUT_NOT_EXECUTED = "reported_permitted_but_not_executed"
    #: a decision exists with no corresponding effect record.
    NO_EFFECT_RECORD = "no_effect_record"
    #: an effect exists with no corresponding decision record.
    NO_DECISION_RECORD = "no_decision_record"


@dataclass(frozen=True)
class Divergence:
    kind: DivergenceKind
    event_id: str
    trajectory_id: str
    seq: int
    reported: str | None
    executed: bool | None
    note: str | None = None

    def __str__(self) -> str:
        return (f"{self.kind.value}  {self.trajectory_id}#{self.seq}  "
                f"event={self.event_id[:12]}  reported={self.reported}  executed={self.executed}")


@dataclass
class DivergenceReport:
    divergences: list[Divergence]
    n_decisions: int
    n_effects: int
    joined: int
    single_writer_mode: bool

    def of_kind(self, kind: DivergenceKind) -> list[Divergence]:
        return [d for d in self.divergences if d.kind is kind]

    @property
    def n_dashboard_lies(self) -> int:
        """The headline count: telemetry green, reality unsafe."""
        return len(self.of_kind(DivergenceKind.REPORTED_PREVENTED_BUT_EXECUTED))


def detect(decision_chain: Chain, effect_chain: Chain) -> DivergenceReport:
    decisions: dict[str, dict] = {}
    for r in decision_chain.records:
        if r.get("record_type") == "scp.decision":
            # A later decision for the same event supersedes an earlier one.
            decisions[r["event_id"]] = r

    effects: dict[str, dict] = {}
    single_writer = False
    for r in effect_chain.records:
        if r.get("record_type") == "scp.effect":
            eid = r["record_id"]
            if r.get("derived_from_decision"):
                single_writer = True
            prev = effects.get(eid)
            # Any execution wins: if the action ran at all, reality is "executed".
            if prev is None or (r.get("executed") and not prev.get("executed")):
                effects[eid] = r

    out: list[Divergence] = []
    joined = 0

    for eid, dec in decisions.items():
        eff = effects.get(eid)
        if eff is None:
            out.append(Divergence(DivergenceKind.NO_EFFECT_RECORD, eid,
                                  dec.get("trajectory_id", "?"), dec.get("seq", -1),
                                  dec.get("reported"), None,
                                  note="decision recorded, no independent effect record"))
            continue
        joined += 1
        reported = dec.get("reported")
        executed = bool(eff.get("executed"))
        if reported == "PREVENTED" and executed:
            out.append(Divergence(DivergenceKind.REPORTED_PREVENTED_BUT_EXECUTED, eid,
                                  dec.get("trajectory_id", "?"), dec.get("seq", -1),
                                  reported, executed, note=dec.get("note")))
        elif reported == "PERMITTED" and not executed:
            out.append(Divergence(DivergenceKind.REPORTED_PERMITTED_BUT_NOT_EXECUTED, eid,
                                  dec.get("trajectory_id", "?"), dec.get("seq", -1),
                                  reported, executed, note=dec.get("note")))

    for eid, eff in effects.items():
        if eid not in decisions:
            out.append(Divergence(DivergenceKind.NO_DECISION_RECORD, eid,
                                  eff.get("trajectory_id", "?"), eff.get("seq", -1),
                                  None, bool(eff.get("executed")),
                                  note="action executed with no decision on record"))

    return DivergenceReport(out, len(decisions), len(effects), joined, single_writer)
