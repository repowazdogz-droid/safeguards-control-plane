# Safeguards Control Plane

A fault-injected testbed for the question: **when a safeguards system reports that it
controlled an agent, what evidence establishes that the intervention actually happened?**

The interesting object here is the *infrastructure*, not the model. The agent environment is
synthetic and deterministic on purpose, so that everything measured is a property of the
safeguards pipeline — ingestion, evaluation, policy, enforcement, telemetry, evidence, replay
— and not of model intelligence.

```bash
./demo.sh          # broker up, negative controls, small matrix (~40 s)
make experiments   # full measured run, 10,000 events (~3 min)
make test          # 31 unit tests, no broker needed
```

---

## The result, in one table

Two arms consume the **same events**, with the **same evaluator**, the **same policy** and the
**same injected faults**. The only difference is *position*: one sits on the path and can
refuse, the other observes alongside.

| condition | arm | intervention recall | enforcement success given BLOCK | dashboard says prevented but executed |
|---|---|---|---|---|
| all faults, fail-closed | **in-series** | 697/818 | 900/906 | 6/9,880 |
| all faults, fail-closed | **monitor** | 0/818 | 0/1,026 | **1,026/10,000** |

The monitor's dashboard reported 1,026 actions as PREVENTED. **All 1,026 executed.** Not a
degraded detection rate — the entire population. A monitor's telemetry is a record of
intentions that reads exactly like a record of outcomes.

And the finding that matters more, because it is about *architecture* rather than position:

| | two writers | single writer |
|---|---|---|
| enforcement success given BLOCK | 900/906 = 99.34% | 906/906 = **100.00%** |
| divergences detected | 6 | **0** |
| unsafe executions in the ledger | 121 | **115** |

Identical faults, identical policy, identical events, and the same six unsafe actions really
executing. The single-writer configuration reports **perfect enforcement, zero divergence, and
six fewer unsafe executions than actually happened**. A safeguards system whose telemetry and
whose enforcement record come from the same writer cannot detect its own enforcement failures.

## Why that works: two writers, never one

```
                 ┌──────── Arm M (MONITOR) ────────┐
  scenario  ──►  │ observes; cannot prevent        │──► telemetry ──► DASHBOARD
  generator      └─────────────────────────────────┘
      │          ┌──────── Arm S (IN-SERIES) ──────┐
      └───────►  │ on the only path; can refuse    │──► EFFECTOR
                 └─────────────────────────────────┘         │
                                                              │
   WRITER 1  safeguards service: "I decided BLOCK"  ──────────┼──► sealed decision chain
   WRITER 2  effector:           "I executed"       ──────────┘──► sealed effect chain
                                    │
                     DIVERGENCE DETECTOR (joins on event_id)
```

Telemetry is built on the **decision** chain, exactly as real dashboards are, because that is
the log the safeguards service owns. The gap becomes visible only when a second, independent
writer records what actually ran. Agreement between a record and a function of that same
record is a tautology; `single_writer` mode exists purely to demonstrate that the detector
goes blind without the split.

## What is injected, and where

Faults are **scheduled, not probabilistic** — built from a seed onto specific
`(trajectory, seq)` positions, so every run reproduces and every failure is attributable to a
named fault at a known position. That is what makes incident reconstruction gradeable.

| | failure | lands at |
|---|---|---|
| A | dropped safety event | producer edge |
| B | duplicate event | same `event_id` twice |
| C | reordered event | held, arrives after successor |
| D | intervention too late | 50 ms evaluation delay vs 25 ms deadline |
| E | evaluator crashes | unacked → **real** `XAUTOCLAIM` redelivery |
| F | evaluators disagree | recorded, never silently resolved |
| G | policy changed before replay | detected and attributed at replay |
| H | evidence tampered | rejected by seal (reused capability) |
| I | enforcement fails after BLOCK | gate decided, effector ran anyway |
| J | telemetry says BLOCK, execution proceeded | the observed consequence of I |
| K | retry intervenes twice | absorbed by idempotency key |
| L | safeguards service unavailable | publish raises; must fail closed |

## Negative controls — 9 of 9 fired

Every checker is demonstrated capable of failing. A checker that cannot fail reports safety it
never established.

| control | plants | result |
|---|---|---|
| NC1 idempotency power | dedup OFF | 0/10,000 → **686/10,000** double executions |
| NC2a arm confound | null policy, no faults | 818 = 818 ✓ |
| NC2b arm confound | null policy, faults, fail-open | 818 = 818 ✓ |
| NC3a divergence power | planted enforcement failures | **136** detected and named |
| NC3b divergence load-bearing | same, single writer | **0** detected — detector goes blind |
| NC4 deadline power | fail-open | 106/818 → **222/818** unsafe executed |
| NC5 replay power | one decision hand-flipped | 100% agreement → **exactly one short** |
| NC6 reconstruction honesty | delete an incident's evidence | cause → **UNKNOWN**, not a guess |
| NC7 ledger integrity | alter a sealed field | intact ✓, tampered ✗ |

Five defects in this system's own checkers were found this way during development — including
one where reading a non-existent result key would have made the integrity check **pass
vacuously on every input**. All five are documented in [`LIMITATIONS.md`](LIMITATIONS.md) §10.

## Metrics

No rate is reportable without its denominator: `Rate` carries both and formats as `n/d = p%`,
and an empty denominator prints `undefined`, never `0%`. Version-skew replay strata are never
pooled. BLOCKs with no independent effect record are reported as **unconfirmable** and excluded
from the enforcement denominator — without a second writer, non-execution cannot be confirmed,
which is the whole point.

## Layout

| | |
|---|---|
| [`CLAIMS.md`](CLAIMS.md) | what is earned, what is not, with the pre-registered controls |
| [`LIMITATIONS.md`](LIMITATIONS.md) | scale, construct validity, trusted base, own-checker defects |
| [`THREAT_MODEL.md`](THREAT_MODEL.md) | trust boundaries; the adversary model is deliberately weak |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | the three structural commitments |
| [`docs/CI.md`](docs/CI.md) | how CI reproduces these checks from a clean checkout |
| [`docs/PHASE0_GAP_AUDIT.md`](docs/PHASE0_GAP_AUDIT.md) | prior art, duplication analysis, build verdict |
| `src/scp/` | `transport` · `faultlab` · `arms` · `divergence` · `metrics` · `replay` · `reconstruct` |
| `results/` | `full_run.txt`, `experiments.json` |

## Scale, stated plainly

10,000 events · 500 trajectories · 818 unsafe actions · single machine · one Redis container ·
~1,200 events/s per arm · ~8.3 s per condition · ~3 min for the full matrix.

**This is not production scale** and is not described as such. Throughput is bounded by a
single-threaded Python loop doing a synchronous broker round-trip per event, not by the broker.

## What this does not claim

No failure mode here is novel — A–L are distributed-systems canon. Latency, oversight and
recovery as challenges for control monitoring are named in Lindner et al.,
[arXiv:2512.22154](https://arxiv.org/abs/2512.22154) (Google DeepMind); this project
implements and measures them, it did not identify them. Chaos engineering on agent systems is
established ([AgentChaos, arXiv:2608.06790](https://arxiv.org/abs/2608.06790)) — the difference
here is the target (the safeguards path, not the LLM API) and the metric (enforcement
conditional on BLOCK, not task success). The version-skew catalogue is long-term signature
validation (ETSI EN 319 102-1) rediscovered.

The contribution is **synthesis and measurement**, not law. Full boundaries in
[`CLAIMS.md`](CLAIMS.md).
