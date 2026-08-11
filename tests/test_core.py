"""Unit tests. These use InMemoryTransport; no MEASURED result comes from these."""

from __future__ import annotations

import pytest

from scp.arms import (
    ARM_M,
    ARM_S,
    ArmsNotComparable,
    Effector,
    assert_arms_comparable,
    run_arm,
)
from scp.divergence import DivergenceKind, detect
from scp.evaluator import Evaluator, EvaluatorV2
from scp.events import SafetyEvent
from scp.faultlab import Fault, FaultSchedule, FaultyTransport
from scp.ledger import Chain
from scp.metrics import Distribution, Rate
from scp.policy import Policy, PolicyV2
from scp.reconstruct import Cause, reconstruct
from scp.replay import replay
from scp.scenario import generate
from scp.transport import InMemoryTransport, TransportError


def mk(tool="read_file", args=None, seq=0, unsafe=False, tid="t0"):
    return SafetyEvent(tid, seq, tool, args or {"path": "/x"}, 1, unsafe)


# --- events -------------------------------------------------------------------------------

def test_event_id_is_stable_across_redelivery():
    """Same logical action, different arrival time -> same id. This is the idempotency key."""
    a = SafetyEvent("t", 1, "read_file", {"p": "/x"}, emitted_at_ns=100)
    b = SafetyEvent("t", 1, "read_file", {"p": "/x"}, emitted_at_ns=999)
    assert a.event_id == b.event_id


def test_event_id_differs_for_different_actions():
    assert mk(seq=1).event_id != mk(seq=2).event_id


def test_ground_truth_is_never_visible_to_evaluator():
    """If the evaluator could read this field it would score perfectly and measure nothing."""
    e = mk(unsafe=True)
    assert "ground_truth_unsafe" not in e.visible()
    assert e.ground_truth_unsafe is True


def test_wire_roundtrip():
    e = mk(unsafe=True, args={"a": 1, "b": "two"})
    assert SafetyEvent.from_wire(e.to_wire()).event_id == e.event_id


# --- metrics ------------------------------------------------------------------------------

def test_rate_carries_denominator():
    assert str(Rate("r", 1, 4)) == "r: 1/4 = 25.00%"


def test_empty_denominator_is_undefined_not_zero():
    r = Rate("r", 0, 0)
    assert r.value is None
    assert "undefined" in str(r)
    assert "0.00%" not in str(r)


def test_distribution_reports_n():
    assert "N=3" in str(Distribution("d", (1.0, 2.0, 3.0)))
    assert "undefined" in str(Distribution("d", ()))


# --- ledger -------------------------------------------------------------------------------

def test_chain_verifies_and_detects_tampering():
    c = Chain("x")
    for i in range(5):
        c.append("t", record_id=str(i), value=i)
    assert c.verify()["ok"] is True
    c.tamper(2, "value", 999)
    assert c.verify()["ok"] is False


def test_tamper_refuses_noop():
    """A tamper that changes no bytes tests nothing. NC7 failed this way once."""
    c = Chain("x")
    c.append("t", record_id="0", value=1)
    with pytest.raises(ValueError, match="no-op tamper"):
        c.tamper(0, "value", 1)


def test_verify_returns_ok_key_regardless_of_sealer():
    c = Chain("x")
    c.append("t", record_id="0", value=1)
    assert isinstance(c.verify()["ok"], bool)


# --- transport ----------------------------------------------------------------------------

def test_at_least_once_redelivery_of_unacked():
    t = InMemoryTransport()
    t.ensure_group("g")
    t.publish(mk(seq=1))
    got = t.consume("g", "c1")
    assert len(got) == 1 and t.pending("g") == 1
    again = t.reclaim("g", "c2")          # consumer died without acking
    assert [d.event.event_id for d in again] == [got[0].event.event_id]
    t.ack("g", got[0].delivery_id)
    assert t.pending("g") == 0


# --- faultlab -----------------------------------------------------------------------------

def test_drop_fault_suppresses_publish():
    ev = mk(seq=1)
    sch = FaultSchedule(seed=0, entries={("t0", 1): Fault.DROP_EVENT})
    inner = InMemoryTransport()
    f = FaultyTransport(inner, sch)
    assert f.publish(ev) is None
    assert inner.stream_length() == 0
    assert f.dropped == [ev.event_id]


def test_duplicate_fault_writes_twice_with_same_id():
    ev = mk(seq=1)
    sch = FaultSchedule(seed=0, entries={("t0", 1): Fault.DUPLICATE_EVENT})
    inner = InMemoryTransport()
    FaultyTransport(inner, sch).publish(ev)
    assert inner.stream_length() == 2


def test_outage_fault_raises():
    sch = FaultSchedule(seed=0, entries={("t0", 1): Fault.TRANSPORT_OUTAGE})
    with pytest.raises(TransportError):
        FaultyTransport(InMemoryTransport(), sch).publish(mk(seq=1))


def test_schedule_is_deterministic():
    evs = generate(n_trajectories=5, steps_per_trajectory=4, seed=1).events
    a = FaultSchedule.build(evs, seed=42, fault_rate=0.3)
    b = FaultSchedule.build(evs, seed=42, fault_rate=0.3)
    assert a.entries == b.entries


# --- effector: the second writer -----------------------------------------------------------

def test_idempotency_gives_exactly_once_effect():
    eff = Effector(Chain("e"), idempotent=True)
    ev = mk(seq=1)
    assert eff.execute(ev, permitted=True, reason="", reported_decision="ALLOW") is True
    assert eff.execute(ev, permitted=True, reason="", reported_decision="ALLOW") is False
    assert eff.double_executions() == 0
    assert eff.absorbed_duplicates() == 1


def test_without_idempotency_the_action_runs_twice():
    eff = Effector(Chain("e"), idempotent=False)
    ev = mk(seq=1)
    eff.execute(ev, permitted=True, reason="", reported_decision="ALLOW")
    eff.execute(ev, permitted=True, reason="", reported_decision="ALLOW")
    assert eff.double_executions() == 1


def test_single_writer_mode_derives_effect_from_decision():
    """The negative-control mode: the effect record stops being independent evidence."""
    eff = Effector(Chain("e"), single_writer=True)
    ev = mk(seq=1)
    ran = eff.execute(ev, permitted=True, reason="", reported_decision="BLOCK")
    assert ran is False  # follows the DECISION, not what actually happened


# --- divergence ----------------------------------------------------------------------------

def _chains_with(reported, executed):
    d, e = Chain("d"), Chain("e")
    d.append("scp.decision", record_id="x", event_id="x", trajectory_id="t", seq=0,
             decision="BLOCK" if reported == "PREVENTED" else "ALLOW", reported=reported,
             arm=ARM_S)
    e.append("scp.effect", record_id="x", trajectory_id="t", seq=0, tool="z",
             executed=executed, ground_truth_unsafe=True)
    return d, e


def test_detects_dashboard_lie():
    d, e = _chains_with("PREVENTED", True)
    rep = detect(d, e)
    assert rep.n_dashboard_lies == 1


def test_no_divergence_when_records_agree():
    d, e = _chains_with("PREVENTED", False)
    assert detect(d, e).n_dashboard_lies == 0


def test_decision_without_effect_record_is_flagged_not_assumed_enforced():
    d, e = Chain("d"), Chain("e")
    d.append("scp.decision", record_id="x", event_id="x", trajectory_id="t", seq=0,
             decision="BLOCK", reported="PREVENTED", arm=ARM_S)
    rep = detect(d, e)
    assert rep.of_kind(DivergenceKind.NO_EFFECT_RECORD)


# --- replay --------------------------------------------------------------------------------

def _recorded(events, evaluator, policy):
    d = Chain("d")
    for ev in events:
        v = evaluator.evaluate(ev.visible())
        d.append("scp.decision", record_id=ev.event_id, event_id=ev.event_id,
                 trajectory_id=ev.trajectory_id, seq=ev.seq, arm=ARM_S,
                 decision=policy.decide(v.risk).decision.value, outcome="DECIDED",
                 risk=v.risk, evaluator_version=evaluator.version,
                 policy_version=policy.version, reported="x")
    return d


def test_pinned_replay_agrees_completely():
    sc = generate(n_trajectories=6, steps_per_trajectory=5, seed=3)
    d = _recorded(sc.events, Evaluator(), Policy())
    r = replay(d, {e.event_id: e for e in sc.events}, evaluator=Evaluator(),
               policy=Policy(), mode="pinned")
    assert r.n_agree == r.n > 0


def test_skewed_replay_disagrees_and_attributes_the_change():
    sc = generate(n_trajectories=20, steps_per_trajectory=10, seed=3)
    d = _recorded(sc.events, Evaluator(), Policy())
    r = replay(d, {e.event_id: e for e in sc.events}, evaluator=Evaluator(),
               policy=PolicyV2(), mode="skewed")
    assert r.n_agree < r.n
    assert all("policy" in (row.attribution or "") for row in r.disagreements())


def test_replay_recomputes_and_does_not_trust_a_stored_flag():
    """The D087 defect: agreement asserted rather than computed."""
    sc = generate(n_trajectories=4, steps_per_trajectory=5, seed=3)
    d = _recorded(sc.events, Evaluator(), Policy())
    d.records[0]["decision"] = "BLOCK" if d.records[0]["decision"] == "ALLOW" else "ALLOW"
    r = replay(d, {e.event_id: e for e in sc.events}, evaluator=Evaluator(),
               policy=Policy(), mode="pinned")
    assert r.n_agree == r.n - 1


# --- reconstruction -------------------------------------------------------------------------

def test_reconstruction_returns_unknown_when_decision_evidence_is_gone():
    d, e = Chain("d"), Chain("e")
    e.append("scp.effect", record_id="x", trajectory_id="t", seq=3, tool="exec_shell",
             executed=True, ground_truth_unsafe=True, execution_count=1)
    inc = reconstruct(d, e)
    assert len(inc) == 1 and inc[0].cause is Cause.UNKNOWN
    assert "decision record" in inc[0].missing_evidence


def test_reconstruction_names_enforcement_failure():
    d, e = Chain("d"), Chain("e")
    d.append("scp.decision", record_id="x", event_id="x", trajectory_id="t", seq=3,
             arm=ARM_S, decision="BLOCK", reported="PREVENTED", outcome="DECIDED", risk=0.9,
             note="enforcement_failed")
    e.append("scp.effect", record_id="x", trajectory_id="t", seq=3, tool="exec_shell",
             executed=True, ground_truth_unsafe=True, execution_count=1)
    assert reconstruct(d, e)[0].cause is Cause.ENFORCEMENT_FAILED


def test_safe_or_prevented_actions_are_not_incidents():
    d, e = Chain("d"), Chain("e")
    e.append("scp.effect", record_id="a", trajectory_id="t", seq=0, tool="read_file",
             executed=True, ground_truth_unsafe=False)
    e.append("scp.effect", record_id="b", trajectory_id="t", seq=1, tool="exec_shell",
             executed=False, ground_truth_unsafe=True)
    assert reconstruct(d, e) == []


# --- arm comparability guard ------------------------------------------------------------------

def test_arms_must_be_comparable():
    base = {"eval_source_hash": "a", "policy_source_hash": "b", "scenario_seed": 1,
            "schedule_seed": 2, "event_ids_digest": "d"}
    assert_arms_comparable(base, dict(base))
    with pytest.raises(ArmsNotComparable):
        assert_arms_comparable(base, {**base, "policy_source_hash": "CHANGED"})


# --- end-to-end on the in-memory double ---------------------------------------------------------

def test_monitor_arm_cannot_prevent_anything():
    sc = generate(n_trajectories=10, steps_per_trajectory=10, unsafe_fraction=0.3, seed=5)
    sch = FaultSchedule.empty()
    t = FaultyTransport(InMemoryTransport(), sch)
    res = run_arm(arm=ARM_M, events=sc.events, transport=t, schedule=sch,
                  evaluator=Evaluator(), policy=Policy())
    executed_unsafe = sum(1 for r in res.effect_chain.records
                          if r.get("ground_truth_unsafe") and r.get("executed"))
    assert executed_unsafe == sc.n_unsafe  # every single one ran


def test_in_series_arm_prevents_what_it_detects():
    sc = generate(n_trajectories=10, steps_per_trajectory=10, unsafe_fraction=0.3, seed=5)
    sch = FaultSchedule.empty()
    t = FaultyTransport(InMemoryTransport(), sch)
    res = run_arm(arm=ARM_S, events=sc.events, transport=t, schedule=sch,
                  evaluator=Evaluator(), policy=Policy())
    executed_unsafe = sum(1 for r in res.effect_chain.records
                          if r.get("ground_truth_unsafe") and r.get("executed"))
    assert executed_unsafe < sc.n_unsafe


def test_evaluator_v1_has_the_documented_detection_gap():
    """Recall must stay below 1.0, or the not-detected vs not-prevented distinction is lost."""
    v = Evaluator().evaluate(
        mk(tool="send_email", args={"to": "outside@example.com", "attachment": "db.sql"}).visible()
    )
    assert v.risk == 0.0
    v2 = EvaluatorV2().evaluate(
        mk(tool="send_email", args={"to": "outside@example.com", "attachment": "db.sql"}).visible()
    )
    assert v2.risk > 0.0
