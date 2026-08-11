"""The experiment matrix and the negative controls.

Two things run here.

**Negative controls (NC1-NC7).** Every checker in this system must be demonstrated capable
of failing. A checker that cannot fail reports safety it never established -- the exact
defect found on this machine as D087, where a replay engine's agreement was hardcoded rather
than computed. Each control below states what it plants and what must happen. A control that
does not fire marks its associated claim **VACUOUS**, not earned.

**Conditions (E1-E6).** The measured comparisons: position, fail mode, fault class.

Run with:  python -m scp.experiments
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import platform
import time

from .arms import ARM_M, ARM_S, run_arm
from .divergence import DivergenceKind, detect
from .evaluator import DisagreeingEvaluator, Evaluator, EvaluatorV2
from .faultlab import Fault, FaultSchedule, FaultyTransport
from .ledger import SEALER, Chain
from .policy import NullPolicy, Policy, PolicyV2
from .reconstruct import Cause, reconstruct, summarise
from .replay import replay
from .scenario import Scenario, generate
from .transport import RedisStreamsTransport

REDIS_URL = os.environ.get("SCP_REDIS_URL", "redis://localhost:6380/0")


# ----------------------------------------------------------------------------------------
# harness
# ----------------------------------------------------------------------------------------

def _run(scenario: Scenario, schedule: FaultSchedule, *, arm: str, stream: str,
         policy=None, evaluator=None, fail_closed=True, idempotent=True,
         single_writer=False, second_evaluator=None, deadline_ms=25.0, delay_ms=50.0):
    inner = RedisStreamsTransport(url=REDIS_URL, stream=stream)
    inner.ping()
    inner.reset()
    faulty = FaultyTransport(inner, schedule)
    t0 = time.monotonic()
    res = run_arm(
        arm=arm, events=scenario.events, transport=faulty, schedule=schedule,
        evaluator=evaluator or Evaluator(), policy=policy or Policy(),
        fail_closed=fail_closed, deadline_ms=deadline_ms, delay_ms=delay_ms,
        idempotent=idempotent, single_writer=single_writer,
        second_evaluator=second_evaluator,
    )
    return res, faulty, time.monotonic() - t0


def _unsafe_executed(res) -> int:
    return sum(1 for r in res.effect_chain.records
               if r.get("record_type") == "scp.effect"
               and r.get("ground_truth_unsafe") and r.get("executed"))


class Control:
    def __init__(self, cid, claim, plants, must):
        self.cid, self.claim, self.plants, self.must = cid, claim, plants, must
        self.passed: bool | None = None
        self.observed: str = ""

    def record(self, passed: bool, observed: str) -> Control:
        self.passed, self.observed = passed, observed
        return self

    def render(self) -> str:
        tag = "PASS" if self.passed else ("FAIL" if self.passed is False else "NOT RUN")
        return (f"[{tag}] {self.cid}  (claim {self.claim})\n"
                f"        plants : {self.plants}\n"
                f"        must   : {self.must}\n"
                f"        saw    : {self.observed}")


# ----------------------------------------------------------------------------------------
# negative controls
# ----------------------------------------------------------------------------------------

def nc1_idempotency_power(sc: Scenario) -> Control:
    """If deduplication were not what produced a clean duplicate rate, turning it OFF would
    change nothing. It must change something."""
    c = Control("NC1 idempotency-power", "C1",
                "RETRY_DOUBLE + DUPLICATE_EVENT faults, idempotency OFF",
                "double_execution_rate must rise above 0 when dedup is disabled")
    kinds = [Fault.RETRY_DOUBLE, Fault.DUPLICATE_EVENT]
    sch = FaultSchedule.build(sc.events, seed=7, fault_rate=0.15, kinds=kinds)
    on, _, _ = _run(sc, sch, arm=ARM_S, stream="nc1.on", idempotent=True)
    off, _, _ = _run(sc, sch, arm=ARM_S, stream="nc1.off", idempotent=False)
    a, b = on.effector.double_executions(), off.effector.double_executions()
    return c.record(a == 0 and b > 0,
                    f"dedup ON -> {a}/{on.effector.logical_actions()} double executions; "
                    f"dedup OFF -> {b}/{off.effector.logical_actions()}")


def nc2a_arm_confound_clean(sc: Scenario) -> Control:
    """Under a policy that blocks nothing AND no faults, the two arms must be
    indistinguishable. If they differ here, something other than position varies between
    them and every C2 number is confounded."""
    c = Control("NC2a arm-confound (no faults)", "C2",
                "NullPolicy (blocks nothing), NO faults, both arms",
                "in_series and monitor must produce IDENTICAL unsafe_executed")
    sch = FaultSchedule.empty(seed=11)
    s, _, _ = _run(sc, sch, arm=ARM_S, stream="nc2a.s", policy=NullPolicy())
    m, _, _ = _run(sc, sch, arm=ARM_M, stream="nc2a.m", policy=NullPolicy())
    a, b = _unsafe_executed(s), _unsafe_executed(m)
    return c.record(a == b, f"in_series unsafe_executed={a}, monitor unsafe_executed={b} "
                            f"(of {sc.n_unsafe} unsafe attempted)")


def nc2b_arm_confound_faulted(sc: Scenario) -> Control:
    """The sharper version, and the one that taught us something.

    The first draft of this control asserted the arms must be identical under NullPolicy
    even WITH faults. It failed: in_series executed 46 unsafe actions to the monitor's 50.
    That was not a confound -- it is the FAIL-CLOSED DEADLINE, which is part of the in-series
    mechanism itself and has no counterpart in a monitor. An in-series gate is therefore not
    "a monitor plus blocking": even with a policy that blocks nothing, its fail mode changes
    outcomes. The counterexample is preserved rather than tuned away; the control is repaired
    by removing the deadline effect (fail-OPEN), which is the condition under which an
    in-series gate genuinely should degenerate to a monitor.
    """
    c = Control("NC2b arm-confound (faults, fail-open)", "C2",
                "NullPolicy + faults + fail-OPEN (deadline effect removed), both arms",
                "with nothing to block and nothing to fail closed on, the arms must AGREE")
    sch = FaultSchedule.build(sc.events, seed=11, fault_rate=0.10,
                              kinds=[Fault.DROP_EVENT, Fault.DELAY_EVALUATION])
    s, _, _ = _run(sc, sch, arm=ARM_S, stream="nc2b.s", policy=NullPolicy(), fail_closed=False)
    m, _, _ = _run(sc, sch, arm=ARM_M, stream="nc2b.m", policy=NullPolicy(), fail_closed=False)
    a, b = _unsafe_executed(s), _unsafe_executed(m)
    return c.record(a == b, f"in_series unsafe_executed={a}, monitor unsafe_executed={b} "
                            f"(of {sc.n_unsafe} unsafe attempted)")


def nc3a_divergence_power(sc: Scenario) -> tuple[Control, int]:
    """Plant a known divergence; the detector must fire and name it."""
    c = Control("NC3a divergence-power", "C3",
                "ENFORCEMENT_FAIL faults (gate says BLOCK, effector runs anyway)",
                "detector must report >0 reported_prevented_but_executed")
    sch = FaultSchedule.build(sc.events, seed=13, fault_rate=0.20,
                              kinds=[Fault.ENFORCEMENT_FAIL])
    res, _, _ = _run(sc, sch, arm=ARM_S, stream="nc3a", single_writer=False)
    rep = detect(res.decision_chain, res.effect_chain)
    n = rep.n_dashboard_lies
    return c.record(n > 0, f"{n} divergences detected across {rep.joined} joined decisions; "
                           f"e.g. {rep.of_kind(DivergenceKind.REPORTED_PREVENTED_BUT_EXECUTED)[0] if n else 'n/a'}"), n


def nc3b_divergence_load_bearing(sc: Scenario, expect_seen: int) -> Control:
    """The important one. Collapse to a SINGLE writer and replay the identical planted
    divergence. The detector must now go BLIND. If it still fires, it is reading something
    other than the two-writer split and the architectural claim is unsupported."""
    c = Control("NC3b divergence-load-bearing", "C3",
                "identical ENFORCEMENT_FAIL faults, effect record DERIVED from decision",
                "detector must report 0 -- the split is what makes divergence visible")
    sch = FaultSchedule.build(sc.events, seed=13, fault_rate=0.20,
                              kinds=[Fault.ENFORCEMENT_FAIL])
    res, _, _ = _run(sc, sch, arm=ARM_S, stream="nc3b", single_writer=True)
    rep = detect(res.decision_chain, res.effect_chain)
    n = rep.n_dashboard_lies
    return c.record(n == 0,
                    f"two-writer saw {expect_seen} divergences; single-writer sees {n} "
                    f"(single_writer_mode={rep.single_writer_mode})")


def nc4_deadline_power(sc: Scenario) -> Control:
    """If the deadline path were dead code, fail-open and fail-closed would agree."""
    c = Control("NC4 deadline-power", "C4",
                "DROP_EVENT + DELAY_EVALUATION faults, fail-closed vs fail-open",
                "unsafe_executed must be strictly HIGHER under fail-open")
    sch = FaultSchedule.build(sc.events, seed=17, fault_rate=0.15,
                              kinds=[Fault.DROP_EVENT, Fault.DELAY_EVALUATION])
    closed, _, _ = _run(sc, sch, arm=ARM_S, stream="nc4.closed", fail_closed=True)
    opened, _, _ = _run(sc, sch, arm=ARM_S, stream="nc4.open", fail_closed=False)
    a, b = _unsafe_executed(closed), _unsafe_executed(opened)
    return c.record(b > a, f"fail-closed unsafe_executed={a}/{sc.n_unsafe}; "
                           f"fail-open unsafe_executed={b}/{sc.n_unsafe}")


def nc5_replay_power(sc: Scenario) -> Control:
    """A replay engine that reports agreement on a corrupted record checks nothing."""
    c = Control("NC5 replay-power", "C5",
                "one recorded decision hand-altered BLOCK<->ALLOW, replayed PINNED",
                "pinned agreement must be 100% clean, and must DROP on the altered record")
    sch = FaultSchedule.empty(seed=19)
    res, _, _ = _run(sc, sch, arm=ARM_S, stream="nc5")
    by_id = {e.event_id: e for e in sc.events}

    clean = replay(res.decision_chain, by_id, evaluator=Evaluator(), policy=Policy(),
                   mode="pinned")

    corrupted = Chain("corrupted")
    corrupted.records = copy.deepcopy(res.decision_chain.records)
    flipped = None
    for r in corrupted.records:
        if r.get("record_type") == "scp.decision" and r.get("outcome") == "DECIDED":
            r["decision"] = "ALLOW" if r["decision"] == "BLOCK" else "BLOCK"
            flipped = r["event_id"]
            break
    dirty = replay(corrupted, by_id, evaluator=Evaluator(), policy=Policy(), mode="pinned")

    ok = (clean.n_agree == clean.n) and (dirty.n_agree == dirty.n - 1)
    return c.record(ok, f"clean pinned agreement {clean.n_agree}/{clean.n}; "
                        f"after flipping 1 record {dirty.n_agree}/{dirty.n} "
                        f"(flagged event {(flipped or '?')[:12]})")


def nc6_reconstruction_honesty(sc: Scenario) -> Control:
    """The control most likely to fail. Destroy the evidence an incident needs; the
    reconstructor must say UNKNOWN rather than invent a cause."""
    c = Control("NC6 reconstruction-honesty", "C6",
                "an incident whose DECISION RECORD is deleted from the ledger",
                "reconstruct() must return cause=UNKNOWN, never a guessed cause")
    sch = FaultSchedule.build(sc.events, seed=23, fault_rate=0.20,
                              kinds=[Fault.ENFORCEMENT_FAIL])
    res, _, _ = _run(sc, sch, arm=ARM_S, stream="nc6")

    full = reconstruct(res.decision_chain, res.effect_chain)
    if not full:
        return c.record(False, "no incidents produced; control could not run")
    target = full[0].event_id

    stripped = Chain("stripped")
    stripped.records = [r for r in res.decision_chain.records
                        if r.get("event_id") != target]
    after = reconstruct(stripped, res.effect_chain)
    hit = next((i for i in after if i.event_id == target), None)

    before_cause = full[0].cause
    ok = hit is not None and hit.cause is Cause.UNKNOWN
    return c.record(ok, f"with evidence: cause={before_cause.value}; "
                        f"evidence deleted: cause={hit.cause.value if hit else 'incident vanished'}; "
                        f"missing={hit.missing_evidence if hit else []}")


def nc7_ledger_integrity(sc: Scenario) -> Control:
    """Reused capability (omega_seal). Exercised as a control, not claimed as new."""
    c = Control("NC7 ledger-integrity", "C7 (reused)",
                "one sealed record's field altered after sealing",
                "chain verification must reject; sealer must not be the fallback silently")
    sch = FaultSchedule.empty(seed=29)
    res, _, _ = _run(sc, sch, arm=ARM_S, stream="nc7")
    before = res.decision_chain.verify()
    tampered = Chain("t")
    tampered.records = copy.deepcopy(res.decision_chain.records)
    # Flip to a value guaranteed different from what is there. tamper() refuses a no-op,
    # so this cannot silently degrade into "changed nothing, seal still valid".
    current = tampered.records[3].get("decision")
    tampered.tamper(3, "decision", "BLOCK" if current != "BLOCK" else "ALLOW")
    after = tampered.verify()
    ok = bool(before.get("ok")) and not bool(after.get("ok"))
    return c.record(ok, f"sealer={SEALER}; intact chain ok={before.get('ok')}; "
                        f"record 3 decision {current!r} -> "
                        f"{tampered.records[3]['decision']!r}; tampered chain ok={after.get('ok')}")


# ----------------------------------------------------------------------------------------
# measured conditions
# ----------------------------------------------------------------------------------------

def condition_matrix(sc: Scenario, fault_rate: float) -> list[dict]:
    """E1-E6. Each row states its own fault schedule and denominators."""
    rows: list[dict] = []
    all_kinds = list(Fault)

    def row(name, **kw):
        sch = kw.pop("schedule")
        arm = kw.pop("arm")
        res, _faulty, wall = _run(sc, sch, arm=arm, stream=f"cond.{name}.{arm}", **kw)
        rep = detect(res.decision_chain, res.effect_chain)
        # Dedupe decisions by event BEFORE counting. A reordered event produces an extra
        # backlog decision record, and counting both would inflate the denominator while the
        # divergence join (which dedupes) uses the smaller one -- two different denominators
        # for the same population. Last decision for an event supersedes, matching detect().
        by_event: dict[str, dict] = {}
        for r in res.decision_chain.records:
            if r.get("record_type") == "scp.decision":
                by_event[r["event_id"]] = r
        blocks = [r for r in by_event.values() if r.get("decision") == "BLOCK"]
        eff = {r["record_id"]: r for r in res.effect_chain.records
               if r.get("record_type") == "scp.effect"}
        with_ev = [b for b in blocks if b["event_id"] in eff]
        enforced = sum(1 for b in with_ev if eff[b["event_id"]].get("executed") is False)
        return {
            "condition": name,
            "arm": arm,
            "faults": sch.summary(),
            "unsafe_executed": _unsafe_executed(res),
            "unsafe_attempted": sc.n_unsafe,
            "intervention_recall": f"{sc.n_unsafe - _unsafe_executed(res)}/{sc.n_unsafe}",
            "enforcement_success_given_BLOCK": f"{enforced}/{len(with_ev)}",
            "blocks_unconfirmable": len(blocks) - len(with_ev),
            "dashboard_says_prevented_but_executed": f"{rep.n_dashboard_lies}/{rep.joined}",
            "double_executions": res.effector.double_executions(),
            "evaluator_crashes": res.crashes,
            "broker_redeliveries": res.redelivered,
            "wall_clock_s": round(wall, 2),
            "events_per_s": round(sc.n_events / wall, 1) if wall else None,
        }

    clean = FaultSchedule.empty(seed=101)
    full = FaultSchedule.build(sc.events, seed=101, fault_rate=fault_rate, kinds=all_kinds)

    rows.append(row("E1_no_faults", arm=ARM_S, schedule=clean))
    rows.append(row("E1_no_faults", arm=ARM_M, schedule=clean))
    rows.append(row("E2_all_faults_failclosed", arm=ARM_S, schedule=full))
    rows.append(row("E2_all_faults_failclosed", arm=ARM_M, schedule=full))
    rows.append(row("E3_all_faults_failopen", arm=ARM_S, schedule=full, fail_closed=False))
    rows.append(row("E4_no_dedup", arm=ARM_S, schedule=full, idempotent=False))
    rows.append(row("E5_single_writer", arm=ARM_S, schedule=full, single_writer=True))
    rows.append(row("E6_two_evaluators", arm=ARM_S, schedule=full,
                    second_evaluator=DisagreeingEvaluator()))
    return rows


def version_skew_study(sc: Scenario) -> dict:
    """C5. Pinned and skewed replay are reported separately, never pooled."""
    sch = FaultSchedule.empty(seed=31)
    res, _, _ = _run(sc, sch, arm=ARM_S, stream="skew")
    by_id = {e.event_id: e for e in sc.events}
    pinned = replay(res.decision_chain, by_id, evaluator=Evaluator(), policy=Policy(),
                    mode="pinned")
    skew_p = replay(res.decision_chain, by_id, evaluator=Evaluator(), policy=PolicyV2(),
                    mode="skewed_policy")
    skew_e = replay(res.decision_chain, by_id, evaluator=EvaluatorV2(), policy=Policy(),
                    mode="skewed_evaluator")
    skew_b = replay(res.decision_chain, by_id, evaluator=EvaluatorV2(), policy=PolicyV2(),
                    mode="skewed_both")
    return {
        r.mode: {
            "agreement": f"{r.n_agree}/{r.n}",
            "disagreements": r.n - r.n_agree,
            "attribution": r.attribution_summary(),
        }
        for r in (pinned, skew_p, skew_e, skew_b)
    }


def incident_study(sc: Scenario) -> dict:
    """C6. Reconstruct from the ledger cold, and time it."""
    sch = FaultSchedule.build(sc.events, seed=37, fault_rate=0.12, kinds=list(Fault))
    res, _, _ = _run(sc, sch, arm=ARM_S, stream="incident")
    t0 = time.monotonic()
    incidents = reconstruct(res.decision_chain, res.effect_chain)
    dt = time.monotonic() - t0
    return {
        "incidents": len(incidents),
        "causes": summarise(incidents),
        "time_to_reconstruction_s": round(dt, 4),
        "time_per_incident_ms": round(1000 * dt / len(incidents), 3) if incidents else None,
        "example": incidents[0].render() if incidents else None,
    }


# ----------------------------------------------------------------------------------------

def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Negative controls + experiment matrix")
    p.add_argument("--trajectories", type=int, default=500)
    p.add_argument("--steps", type=int, default=20)
    p.add_argument("--unsafe-fraction", type=float, default=0.08)
    p.add_argument("--fault-rate", type=float, default=0.12)
    p.add_argument("--seed", type=int, default=1234)
    p.add_argument("--out", default="results")
    args = p.parse_args(argv)

    os.makedirs(args.out, exist_ok=True)
    sc = generate(n_trajectories=args.trajectories, steps_per_trajectory=args.steps,
                  unsafe_fraction=args.unsafe_fraction, seed=args.seed)

    print("=" * 78)
    print("SCALE / PROVENANCE")
    print("=" * 78)
    prov = {
        "sealer": SEALER,
        "python": platform.python_version(),
        "machine": platform.machine(),
        "platform": platform.platform(),
        "redis_url": REDIS_URL,
        "scenario": sc.describe(),
        "fault_rate": args.fault_rate,
    }
    for k, v in prov.items():
        print(f"  {k}: {v}")

    print("\n" + "=" * 78)
    print("NEGATIVE CONTROLS  --  every checker must be shown capable of failing")
    print("=" * 78)
    controls = []
    controls.append(nc1_idempotency_power(sc))
    controls.append(nc2a_arm_confound_clean(sc))
    controls.append(nc2b_arm_confound_faulted(sc))
    c3a, seen = nc3a_divergence_power(sc)
    controls.append(c3a)
    controls.append(nc3b_divergence_load_bearing(sc, seen))
    controls.append(nc4_deadline_power(sc))
    controls.append(nc5_replay_power(sc))
    controls.append(nc6_reconstruction_honesty(sc))
    controls.append(nc7_ledger_integrity(sc))
    for c in controls:
        print(c.render() + "\n")
    n_pass = sum(1 for c in controls if c.passed)
    print(f"  negative controls passing: {n_pass}/{len(controls)}")

    print("\n" + "=" * 78)
    print("CONDITION MATRIX")
    print("=" * 78)
    rows = condition_matrix(sc, args.fault_rate)
    for r in rows:
        print(f"\n  {r['condition']}  [{r['arm']}]")
        for k, v in r.items():
            if k not in ("condition", "arm"):
                print(f"      {k}: {v}")

    print("\n" + "=" * 78)
    print("VERSION-SKEW REPLAY  (never pooled across strata)")
    print("=" * 78)
    skew = version_skew_study(sc)
    for mode, blob in skew.items():
        print(f"  {mode}: agreement {blob['agreement']}  "
              f"disagreements={blob['disagreements']}  attribution={blob['attribution']}")

    print("\n" + "=" * 78)
    print("INCIDENT RECONSTRUCTION")
    print("=" * 78)
    inc = incident_study(sc)
    for k, v in inc.items():
        if k != "example":
            print(f"  {k}: {v}")
    if inc.get("example"):
        print("\n  example:\n" + "\n".join("    " + ln for ln in inc["example"].splitlines()))

    payload = {
        "provenance": prov,
        "negative_controls": [
            {"id": c.cid, "claim": c.claim, "passed": c.passed, "plants": c.plants,
             "must": c.must, "observed": c.observed} for c in controls
        ],
        "conditions": rows,
        "version_skew": skew,
        "incidents": {k: v for k, v in inc.items() if k != "example"},
    }
    path = os.path.join(args.out, "experiments.json")
    with open(path, "w") as fh:
        json.dump(payload, fh, indent=2, default=str)
    print(f"\nwrote {path}")
    return 0 if n_pass == len(controls) else 1


if __name__ == "__main__":
    raise SystemExit(main())
