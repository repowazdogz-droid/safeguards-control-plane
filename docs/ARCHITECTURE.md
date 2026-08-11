# Architecture

One sentence: **two safeguard arms, identical in everything except position, consume the same
event stream through a real broker whose delivery can be faulted on a deterministic schedule,
and two independent writers record what was decided and what actually happened.**

Everything below serves the single independent variable — *position and transport* — and
holds detection quality fixed by construction.

---

## 1. Data path

```
 scenario generator                                                    ledger (omega_seal)
 (seeded, plants unsafe        ┌──────────────────────┐              ┌──────────────────┐
  actions, ground truth ──►    │  Redis Streams       │              │ decision chain   │
  never shown to evaluator)    │  XADD / XREADGROUP   │              │ effect chain     │
                               │  XACK / XPENDING     │              └────────▲─────────┘
                               └──────────┬───────────┘                       │
                                          │                                   │
                            FaultLab wraps the boundary                       │
                            (drop / dup / reorder / delay /                   │
                             crash-before-ack / outage)                       │
                                          │                                   │
              ┌───────────────────────────┴─────────────────────────┐         │
              │                                                     │         │
   ARM M (monitor)                                        ARM S (in-series)   │
   effector executes FIRST,                     effector SUBMITS and BLOCKS   │
   publishes after.                             until decision or deadline.   │
   Monitor consumes async,                      Gate is on the only path.     │
   emits verdict → telemetry.                   Fail-closed | fail-open.      │
   Cannot prevent anything.                                                   │
              │                                                     │         │
              └──────────────► same evaluator ◄─────────────────────┘         │
                               same policy                                    │
                               same events                                    │
                                          │                                   │
                        ┌─────────────────┴──────────────────┐                │
   WRITER 1 (safeguards service)                  WRITER 2 (effector)         │
   decision_log: "I decided BLOCK"                effect_log: "I executed"    │
   → telemetry / dashboard                        → ground reality ───────────┘
                        └──────────────┬─────────────────────┘
                                       │
                          DIVERGENCE DETECTOR (joins on event_id)
                                       │
                        metrics  ·  replay  ·  reconstruct
```

## 2. The three structural commitments

**(a) The arms differ only in position.** Same evaluator object, same policy object, same
event stream, same broker. The harness hashes the evaluator source, the policy source and the
scenario seed before injection begins and **refuses to run** if the two arms do not see
byte-identical values. Without this, every C2 number is confounded.

**(b) Two writers, never one.** The safeguards service records its *decision*. The effector
independently records its *effect*. Neither reads the other. Agreement between them is
therefore informative; agreement between a record and a function of that record would not be.
The single-writer collapse is implemented as a switchable mode purely so the negative control
C3(b) can demonstrate that the detector goes blind without the split.

**(c) Ground truth is withheld from the evaluator.** The scenario generator knows which
actions are unsafe — that is the denominator for intervention recall. The evaluator never sees
that field; it is stripped at the transport boundary. An evaluator that could read it would
score perfectly and measure nothing.

## 3. Components

| Module | Role | Build status |
|---|---|---|
| `events.py` | Versioned schema, content-addressed `event_id`, canonical serialisation | new |
| `transport.py` | `Transport` protocol; `RedisStreamsTransport` (XADD/XREADGROUP/XACK/XPENDING); `InMemoryTransport` for unit tests | **new — core** |
| `faultlab.py` | Deterministic scheduled fault injection at the transport boundary | **new — core** |
| `evaluator.py` | Deterministic, versioned, pure `event → verdict` | substrate adapter (~50 LOC) |
| `policy.py` | Versioned `verdict × context → ALLOW/BLOCK/ESCALATE` | substrate adapter (~50 LOC) |
| `effector.py` | Simulated agent environment; **independent** effect writer | new (small) |
| `arms.py` | Arm M and Arm S | new (small) |
| `ledger.py` | `omega_seal` import + clearly-labelled fallback | reuse |
| `divergence.py` | Two-writer join and divergence detection | **new — core** |
| `metrics.py` | `Rate(num, den)` — a bare rate cannot be printed | **new — core** |
| `replay.py` | Version-skew replay, stratified | new |
| `reconstruct.py` | Incident reconstruction, returns UNKNOWN when evidence is gone | new |
| `scenario.py` | Seeded trajectory generator with planted unsafe actions | new |

## 4. Why a real broker

Delivery semantics are the object of study. Hand-implementing at-least-once would make the
central evidence *my own code's behaviour* rather than a real system's, and a reviewer would
be right to discount it. Redis Streams consumer groups give genuine `XADD`/`XREADGROUP`/
`XACK`/`XPENDING`, genuine redelivery of unacked entries after a consumer dies, and a
genuine pending-entries list readable from outside my process. Faults are injected at the
boundary around it, not inside it.

`InMemoryTransport` exists only so unit tests run without Docker. No experimental result is
reported from it.

## 5. Determinism

Faults are **scheduled, not probabilistic**: a fault schedule is a list of
`(trajectory, seq, fault_kind)` derived from a seed. Every fault is therefore attributable to
a specific event, and every run is reproducible from `(seed, schedule)`. Randomised chaos
would make time-to-detection and reconstruction correctness unreproducible, which would
destroy the measurements this project exists to make.

## 6. Clocks

All latency uses one monotonic source (`time.monotonic_ns`) sampled in a single process.
Cross-process wall-clock comparison is not used and no latency number crosses a clock domain.
This bounds what latency means here: it is in-process scheduling and broker round-trip, not
distributed clock skew — which is stated in `LIMITATIONS.md`.

## 7. What is deliberately absent

No distributed cluster, no multi-node replication, no Kubernetes, no service mesh, no real
network partition, no LLM in the decision loop, no authorization-policy sophistication. Each
of those would add surface without adding measurement of the thing under study, and the
scope note is explicit that architectural completeness is not the goal.
