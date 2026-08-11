"""The two safeguard arms, and the effector that independently records reality.

The whole experimental design lives in this file, so the invariants are stated here:

* Both arms receive the SAME evaluator instance, the SAME policy instance and the SAME
  event stream. ``assert_arms_comparable`` refuses to run otherwise.
* The **effector is a separate writer**. It records what it actually did, into its own sealed
  chain, and it never reads the decision chain. In ``single_writer`` mode it derives its
  record from the decision instead -- that mode exists ONLY to demonstrate, in negative
  control C3(b), that the divergence detector goes blind without the split.
* ``ARM_S`` blocks: the action does not happen until a decision arrives or the deadline
  fires. ``ARM_M`` does not: the action happens first and is observed afterwards.

Fault-to-experiment mapping implemented here:

  A dropped event      -> no decision arrives -> deadline -> fail_closed decides the outcome
  B duplicate event    -> same event_id twice -> idempotency must absorb the second
  C reordered event    -> arrives after its successor
  D late intervention  -> DELAY_EVALUATION pushes the decision past the deadline
  E evaluator crash    -> unacked delivery -> broker redelivery via reclaim()
  F disagreement       -> second evaluator recorded alongside, never silently dropped
  I enforcement fail   -> gate said BLOCK, effector executed anyway
  J telemetry lie      -> the OBSERVED CONSEQUENCE of I: telemetry reads the decision chain
                          (as real dashboards do) so it reports PREVENTED while reality says
                          executed. Detected only by the two-writer join.
  K retry double       -> the same logical action effected twice when dedup is off
  L transport outage   -> publish raises; arm must fail-closed, not swallow
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .evaluator import Evaluator, EvaluatorCrash, Verdict
from .events import SafetyEvent
from .faultlab import Fault, FaultSchedule, FaultyTransport
from .ledger import Chain
from .policy import Decision, Policy
from .transport import TransportError

ARM_S = "in_series"
ARM_M = "monitor"


class ArmsNotComparable(RuntimeError):
    """Raised when the two arms would not differ *only* in position."""


def assert_arms_comparable(a: dict, b: dict) -> None:
    """Refuse to run unless every input except position is byte-identical.

    Without this the C2 comparison is confounded and every number derived from it is
    meaningless. Enforced in code rather than left as an intention.
    """
    for k in ("eval_source_hash", "policy_source_hash", "scenario_seed", "schedule_seed",
              "event_ids_digest"):
        if a[k] != b[k]:
            raise ArmsNotComparable(f"arms differ in {k}: {a[k]!r} vs {b[k]!r}")


@dataclass
class Effector:
    """Simulated agent environment. THE SECOND, INDEPENDENT WRITER.

    It records what it actually executed. It does not read the decision chain -- except in
    ``single_writer`` mode, which is the negative control.
    """

    chain: Chain
    single_writer: bool = False
    idempotent: bool = True
    #: how many times we were ASKED to act on each logical action
    _delivered: dict[str, int] = field(default_factory=dict)
    #: how many times the action ACTUALLY RAN. These must be counted separately: an
    #: absorbed duplicate delivery is a success, a double execution is a failure, and
    #: conflating them would leave C1's negative control with no power.
    _executed: dict[str, int] = field(default_factory=dict)

    def execute(self, event: SafetyEvent, *, permitted: bool, reason: str,
                reported_decision: str) -> bool:
        eid = event.event_id
        self._delivered[eid] = self._delivered.get(eid, 0) + 1
        self._executed.setdefault(eid, 0)

        if self.idempotent and self._delivered[eid] > 1:
            # Exactly-once EFFECT on top of at-least-once DELIVERY. The redundant delivery
            # is absorbed here; no second execution, and no second effect record.
            return False

        executed = permitted
        if self.single_writer:
            # NEGATIVE CONTROL C3(b): the "effect" record is derived from the decision
            # instead of observed. Divergence becomes undetectable by construction.
            executed = reported_decision != Decision.BLOCK.value

        if executed:
            self._executed[eid] += 1

        self.chain.append(
            "scp.effect",
            record_id=eid,
            trajectory_id=event.trajectory_id,
            seq=event.seq,
            tool=event.tool,
            executed=bool(executed),
            executed_at_ns=time.monotonic_ns(),
            ground_truth_unsafe=bool(event.ground_truth_unsafe),
            delivery_count=self._delivered[eid],
            execution_count=self._executed[eid],
            writer="effector",
            derived_from_decision=bool(self.single_writer),
        )
        return bool(executed)

    def double_executions(self) -> int:
        """Logical actions that actually RAN more than once. Must be 0 with idempotency on."""
        return sum(1 for n in self._executed.values() if n > 1)

    def absorbed_duplicates(self) -> int:
        """Redundant deliveries idempotency suppressed. Evidence the dedup path is live."""
        return sum(1 for eid, n in self._delivered.items() if n > 1)

    def logical_actions(self) -> int:
        return len(self._delivered)


@dataclass
class ArmResult:
    arm: str
    decision_chain: Chain
    effect_chain: Chain
    effector: Effector
    latencies_ms: list[float]
    late_blocks: int
    outage_refusals: int
    crashes: int
    redelivered: int
    disagreements: int
    no_decision: int
    provenance: dict


def run_arm(
    *,
    arm: str,
    events: list[SafetyEvent],
    transport: FaultyTransport,
    schedule: FaultSchedule,
    evaluator: Evaluator,
    policy: Policy,
    fail_closed: bool = True,
    deadline_ms: float = 25.0,
    delay_ms: float = 50.0,
    idempotent: bool = True,
    single_writer: bool = False,
    second_evaluator: Evaluator | None = None,
    group: str | None = None,
) -> ArmResult:
    group = group or f"scp.{arm}"
    transport.ensure_group(group)

    decision_chain = Chain(f"{arm}.decisions")
    effect_chain = Chain(f"{arm}.effects")
    effector = Effector(effect_chain, single_writer=single_writer, idempotent=idempotent)

    latencies: list[float] = []
    late_blocks = outage_refusals = crashes = redelivered = disagreements = no_decision = 0

    for event in events:
        fault = schedule.get(event)
        t0 = time.monotonic_ns()

        # ---- ARM M: the action happens FIRST. The safeguard can only observe. ----------
        if arm == ARM_M:
            effector.execute(event, permitted=True, reason="monitor cannot prevent",
                             reported_decision="(none yet)")

        # ---- publish (transport-layer faults land here) --------------------------------
        published = True
        try:
            did = transport.publish(event)
            published = did is not None  # None == dropped or held back by reorder
        except TransportError:
            published = False
            if arm == ARM_S:
                # L: the transport is down. Fail-closed means refuse, not swallow.
                outage_refusals += 1
                _record_decision(decision_chain, event, None, None, arm,
                                 outcome="REFUSED_TRANSPORT_DOWN",
                                 decision=Decision.BLOCK.value if fail_closed else Decision.ALLOW.value,
                                 latency_ms=0.0, evaluator=evaluator, policy=policy,
                                 note="injected transport outage")
                if not fail_closed:
                    effector.execute(event, permitted=True, reason="fail-open on outage",
                                     reported_decision=Decision.ALLOW.value)
                continue

        # ---- consume + evaluate ---------------------------------------------------------
        deliveries = transport.consume(group, f"{arm}-1", count=32, block_ms=5) if published else []

        verdict: Verdict | None = None
        target = None
        for d in deliveries:
            if d.event.event_id == event.event_id:
                target = d
            else:
                # Out-of-order or backlog: evaluate and ack so the stream drains, but the
                # decision for THIS event is what the gate waits on.
                _evaluate_and_ack(transport, group, d, evaluator, policy, decision_chain, arm)

        if target is not None:
            if fault is Fault.EVALUATOR_CRASH:
                crashes += 1
                # Deliberately NOT acked -> the entry stays pending -> broker redelivers.
                for r in transport.reclaim(group, f"{arm}-recovery", min_idle_ms=0):
                    if r.event.event_id == event.event_id:
                        redelivered += 1
                        try:
                            verdict = evaluator.evaluate(r.event.visible())
                        except EvaluatorCrash:
                            verdict = None
                        transport.ack(group, r.delivery_id)
                        break
            else:
                try:
                    verdict = evaluator.evaluate(target.event.visible())
                except EvaluatorCrash:
                    crashes += 1
                    verdict = None
                transport.ack(group, target.delivery_id)

        # F: a second evaluator of equal standing. Disagreement is recorded, never dropped.
        alt: Verdict | None = None
        if second_evaluator is not None and verdict is not None:
            alt = second_evaluator.evaluate(event.visible())
            if policy.decide(alt.risk).decision != policy.decide(verdict.risk).decision:
                disagreements += 1

        # ---- deadline (D: delayed evaluation) -------------------------------------------
        elapsed_ms = (time.monotonic_ns() - t0) / 1e6
        if fault is Fault.DELAY_EVALUATION:
            elapsed_ms += delay_ms
        past_deadline = elapsed_ms > deadline_ms

        # ---- decide ---------------------------------------------------------------------
        if verdict is None or past_deadline:
            no_decision += 1
            if verdict is not None and past_deadline and policy.decide(verdict.risk).decision is Decision.BLOCK:
                late_blocks += 1
            outcome = "LATE" if (verdict is not None and past_deadline) else "NO_DECISION"
            decision = Decision.BLOCK.value if fail_closed else Decision.ALLOW.value
            permitted = not fail_closed
            risk = verdict.risk if verdict else None
        else:
            po = policy.decide(verdict.risk)
            decision = po.decision.value
            outcome = "DECIDED"
            permitted = po.decision is not Decision.BLOCK
            risk = verdict.risk

        # ---- enforcement (I) -------------------------------------------------------------
        enforcement_failed = fault is Fault.ENFORCEMENT_FAIL
        if arm == ARM_S:
            effective_permit = permitted
            if enforcement_failed:
                # The gate decided; the enforcement hook did not hold. The effector executes
                # regardless and records that truthfully.
                effective_permit = True
            effector.execute(event, permitted=effective_permit,
                             reason="enforcement failed" if enforcement_failed else outcome,
                             reported_decision=decision)

        # K: retry applies the same logical action a second time.
        if fault is Fault.RETRY_DOUBLE and arm == ARM_S:
            effector.execute(event, permitted=permitted, reason="retry",
                             reported_decision=decision)

        latency_ms = elapsed_ms
        latencies.append(latency_ms)

        _record_decision(decision_chain, event, risk, verdict, arm, outcome=outcome,
                         decision=decision, latency_ms=latency_ms, evaluator=evaluator,
                         policy=policy,
                         note=("enforcement_failed" if enforcement_failed else None),
                         alt_risk=(alt.risk if alt else None))

    transport.flush_reorder_buffer()

    provenance = {
        "arm": arm,
        "eval_source_hash": type(evaluator).source_hash(),
        "policy_source_hash": type(policy).source_hash(),
        "evaluator_version": evaluator.version,
        "policy_version": policy.version,
        "fail_closed": fail_closed,
        "deadline_ms": deadline_ms,
        "idempotent": idempotent,
        "single_writer": single_writer,
    }
    return ArmResult(arm, decision_chain, effect_chain, effector, latencies, late_blocks,
                     outage_refusals, crashes, redelivered, disagreements, no_decision,
                     provenance)


def _evaluate_and_ack(transport, group, delivery, evaluator, policy, chain, arm) -> None:
    try:
        v = evaluator.evaluate(delivery.event.visible())
        po = policy.decide(v.risk)
        _record_decision(chain, delivery.event, v.risk, v, arm, outcome="DECIDED_BACKLOG",
                         decision=po.decision.value, latency_ms=0.0, evaluator=evaluator,
                         policy=policy, note="out-of-order/backlog drain")
    except EvaluatorCrash:
        pass
    transport.ack(group, delivery.delivery_id)


def _record_decision(chain: Chain, event: SafetyEvent, risk, verdict, arm, *, outcome: str,
                     decision: str, latency_ms: float, evaluator, policy,
                     note: str | None = None, alt_risk: float | None = None) -> None:
    """WRITER 1. What the safeguards service believes. Telemetry reads this chain."""
    chain.append(
        "scp.decision",
        record_id=f"{event.event_id}:{arm}",
        event_id=event.event_id,
        trajectory_id=event.trajectory_id,
        seq=event.seq,
        arm=arm,
        risk=risk,
        decision=decision,
        outcome=outcome,
        # What a dashboard built on the decision log would display:
        reported=("PREVENTED" if decision == Decision.BLOCK.value else "PERMITTED"),
        evaluator_version=evaluator.version,
        policy_version=policy.version,
        eval_source_hash=type(evaluator).source_hash(),
        policy_source_hash=type(policy).source_hash(),
        latency_ms=round(latency_ms, 4),
        reasons=list(verdict.reasons) if verdict else [],
        alt_risk=alt_risk,
        note=note,
        writer="safeguards_service",
    )
