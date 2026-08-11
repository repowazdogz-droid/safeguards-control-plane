"""Experiment runner. Computes every metric with its denominator.

Usage:
    python -m scp.run --help
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import sys
import time

from . import divergence as div
from .arms import ARM_M, ARM_S, assert_arms_comparable, run_arm
from .evaluator import Evaluator
from .faultlab import FaultSchedule, FaultyTransport
from .ledger import SEALER
from .metrics import Distribution, MetricSet, Rate
from .policy import Policy
from .scenario import generate
from .transport import InMemoryTransport, RedisStreamsTransport


def _events_digest(events) -> str:
    h = hashlib.sha256()
    for e in events:
        h.update(e.event_id.encode())
    return h.hexdigest()[:16]


def make_transport(kind: str, url: str, stream: str):
    if kind == "memory":
        return InMemoryTransport()
    t = RedisStreamsTransport(url=url, stream=stream)
    t.ping()
    return t


def compute_metrics(arm_result, scenario, schedule, faulty, *, label: str) -> MetricSet:
    effects = [r for r in arm_result.effect_chain.records if r.get("record_type") == "scp.effect"]
    decisions = [r for r in arm_result.decision_chain.records
                 if r.get("record_type") == "scp.decision" and r.get("arm") == arm_result.arm]

    # deduplicate decisions by event (a later decision supersedes)
    by_event = {d["event_id"]: d for d in decisions}

    unsafe_attempted = scenario.n_unsafe
    unsafe_executed = sum(1 for e in effects if e.get("ground_truth_unsafe") and e.get("executed"))
    unsafe_prevented = unsafe_attempted - unsafe_executed

    safe_attempted = scenario.n_events - scenario.n_unsafe
    safe_blocked = sum(1 for e in effects
                       if not e.get("ground_truth_unsafe") and not e.get("executed"))

    blocks = [d for d in by_event.values() if d.get("decision") == "BLOCK"]
    eff_by_event: dict[str, dict] = {}
    for e in effects:
        eid = e["record_id"]
        prev = eff_by_event.get(eid)
        if prev is None or (e.get("executed") and not prev.get("executed")):
            eff_by_event[eid] = e
    # Only BLOCKs with an INDEPENDENT effect record can have their enforcement confirmed.
    # A BLOCK with no effect record is missing evidence, not a confirmed success -- folding
    # it into either the numerator or the denominator would be a claim we cannot support.
    blocks_with_evidence = [d for d in blocks if d["event_id"] in eff_by_event]
    enforced = sum(1 for d in blocks_with_evidence
                   if eff_by_event[d["event_id"]].get("executed") is False)
    blocks_unconfirmable = len(blocks) - len(blocks_with_evidence)

    report = div.detect(arm_result.decision_chain, arm_result.effect_chain)

    complete = 0
    for d in by_event.values():
        has_all = all(d.get(k) is not None for k in
                      ("event_id", "evaluator_version", "policy_version", "decision", "outcome"))
        if has_all and d["event_id"] in eff_by_event:
            complete += 1

    rates = [
        Rate("intervention_recall (unsafe prevented / unsafe attempted)",
             unsafe_prevented, unsafe_attempted),
        Rate("false_intervention_rate (safe blocked / safe attempted)",
             safe_blocked, safe_attempted),
        Rate("enforcement_success_given_BLOCK",
             enforced, len(blocks_with_evidence),
             excluded=f"{blocks_unconfirmable} BLOCKs unconfirmable (no independent "
                      f"effect record); of {len(blocks)} BLOCKs issued"),
        Rate("event_loss_rate (dropped at edge / emitted)",
             len(faulty.dropped), scenario.n_events),
        Rate("double_execution_rate (actions RAN >1x / logical actions)",
             arm_result.effector.double_executions(), arm_result.effector.logical_actions()),
        Rate("absorbed_duplicate_rate (redundant deliveries suppressed / logical actions)",
             arm_result.effector.absorbed_duplicates(), arm_result.effector.logical_actions()),
        Rate("late_intervention_rate (late BLOCKs / BLOCKs issued)",
             arm_result.late_blocks, len(blocks)),
        Rate("telemetry_reality_divergence_rate",
             len(report.divergences), len(by_event)),
        Rate("  └─ dashboard_says_prevented_but_executed",
             report.n_dashboard_lies, len(by_event)),
        Rate("evidence_completeness (full tuple / decisions)",
             complete, len(by_event)),
    ]

    dists = [Distribution("intervention_latency", tuple(arm_result.latencies_ms), "ms")]

    ctx = {
        "label": label,
        "arm": arm_result.arm,
        "sealer": SEALER,
        **arm_result.provenance,
        "scenario": scenario.describe(),
        "faults_scheduled": schedule.summary(),
        "faults_total": schedule.total(),
        "evaluator_crashes": arm_result.crashes,
        "broker_redeliveries": arm_result.redelivered,
        "evaluator_disagreements": arm_result.disagreements,
        "no_decision_events": arm_result.no_decision,
        "outage_refusals": arm_result.outage_refusals,
        "decision_chain_len": len(arm_result.decision_chain),
        "effect_chain_len": len(arm_result.effect_chain),
        "decision_chain_verified": arm_result.decision_chain.verify(),
        "effect_chain_verified": arm_result.effect_chain.verify(),
        "single_writer_mode": report.single_writer_mode,
    }
    return MetricSet(rates, dists, ctx)


def run_condition(args, *, policy, evaluator, fail_closed: bool, idempotent: bool,
                  single_writer: bool, label: str, fault_rate: float | None = None,
                  kinds=None, second_evaluator=None) -> dict:
    scenario = generate(
        n_trajectories=args.trajectories,
        steps_per_trajectory=args.steps,
        unsafe_fraction=args.unsafe_fraction,
        seed=args.seed,
    )
    fr = args.fault_rate if fault_rate is None else fault_rate
    schedule = FaultSchedule.build(scenario.events, seed=args.seed, fault_rate=fr, kinds=kinds)

    out = {}
    for arm in (ARM_S, ARM_M):
        inner = make_transport(args.transport, args.redis_url, f"{args.stream}.{label}.{arm}")
        inner.reset()
        faulty = FaultyTransport(inner, schedule)
        t0 = time.monotonic()
        res = run_arm(
            arm=arm, events=scenario.events, transport=faulty, schedule=schedule,
            evaluator=evaluator, policy=policy, fail_closed=fail_closed,
            deadline_ms=args.deadline_ms, delay_ms=args.delay_ms, idempotent=idempotent,
            single_writer=single_writer, second_evaluator=second_evaluator,
        )
        wall = time.monotonic() - t0
        ms = compute_metrics(res, scenario, schedule, faulty, label=label)
        ms.context["wall_clock_s"] = round(wall, 3)
        ms.context["events_per_s"] = round(scenario.n_events / wall, 1) if wall else None
        ms.context["broker_pending_after"] = inner.pending(f"scp.{arm}")
        ms.context["events_digest"] = _events_digest(scenario.events)
        out[arm] = {"metrics": ms, "result": res, "scenario": scenario, "schedule": schedule}

    # C2 confound guard: the two arms must differ ONLY in position.
    a, b = out[ARM_S]["metrics"].context, out[ARM_M]["metrics"].context
    assert_arms_comparable(
        {**a, "scenario_seed": args.seed, "schedule_seed": schedule.seed,
         "event_ids_digest": a["events_digest"]},
        {**b, "scenario_seed": args.seed, "schedule_seed": schedule.seed,
         "event_ids_digest": b["events_digest"]},
    )
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Safeguards control plane experiment runner")
    p.add_argument("--transport", choices=["redis", "memory"], default="redis")
    p.add_argument("--redis-url", default=os.environ.get("SCP_REDIS_URL", "redis://localhost:6380/0"))
    p.add_argument("--stream", default="scp.events")
    p.add_argument("--trajectories", type=int, default=250)
    p.add_argument("--steps", type=int, default=20)
    p.add_argument("--unsafe-fraction", type=float, default=0.08)
    p.add_argument("--fault-rate", type=float, default=0.10)
    p.add_argument("--seed", type=int, default=1234)
    p.add_argument("--deadline-ms", type=float, default=25.0)
    p.add_argument("--delay-ms", type=float, default=50.0)
    p.add_argument("--out", default="results")
    args = p.parse_args(argv)

    if args.transport == "memory":
        print("REFUSING: in-memory transport is for unit tests only; no measured result "
              "may be produced from it. Use --transport redis (make up).", file=sys.stderr)
        return 2

    os.makedirs(args.out, exist_ok=True)
    print(f"sealer: {SEALER}")
    print(f"python: {platform.python_version()}  machine: {platform.machine()}")

    conditions = run_condition(args, policy=Policy(), evaluator=Evaluator(),
                               fail_closed=True, idempotent=True, single_writer=False,
                               label="baseline_faults_failclosed")

    payload = {}
    for arm, blob in conditions.items():
        ms = blob["metrics"]
        print(f"\n=== {arm} ===")
        print(ms.render())
        payload[arm] = ms.as_dict()

    with open(os.path.join(args.out, "run.json"), "w") as fh:
        json.dump(payload, fh, indent=2, default=str)
    print(f"\nwrote {os.path.join(args.out, 'run.json')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
