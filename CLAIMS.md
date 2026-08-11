# CLAIMS

**Status: RESOLVED against the pre-registration.** The pre-registered version of this file
(targets, pass and failure conditions, negative controls) was written before implementation
and is preserved in git history. Each claim below moved to EARNED only if its evidence
exists, **its negative control fired**, and its pre-committed failure condition did not occur.

Run that produced every number here:

| | |
|---|---|
| scale | 10,000 events · 500 trajectories × 20 steps · 818 ground-truth-unsafe actions |
| seed | 1234 (scenario), 101 (condition fault schedules) · fault rate 0.12 |
| transport | Redis 7 (container `scp-redis`), real consumer groups, `XADD`/`XREADGROUP`/`XACK`/`XAUTOCLAIM` |
| sealer | `omega_seal` (canonical, not the fallback) |
| host | macOS 15.7.3, arm64, Python 3.13.15 · ~1,200 events/s/arm · ~8.3 s per condition |
| reproduce | `make experiments` |

**Negative controls: 9 of 9 fired.** No claim below is marked VACUOUS.

Frozen substrate, re-used and **not claimed**: evaluator interface, policy engine, in-series
gate mechanism, `omega_seal` ledger, record-level replay.

---

## EARNED

### C1 — Durable ingestion under explicit delivery semantics ✅

At-least-once delivery with exactly-once effect, over a real broker consumer group.

- `double_execution_rate` **0/10,000** with idempotency on, across a schedule containing 120
  `RETRY_DOUBLE` and 120 `DUPLICATE_EVENT` faults.
- 120 evaluator crashes produced **120 genuine broker redeliveries** via `XAUTOCLAIM` — the
  unacked-entry path, not a simulation of it.
- **NC1 fired.** Disabling the idempotency key on the identical fault schedule took double
  executions from **0/10,000 to 686/10,000**. Deduplication is demonstrably what produced the
  clean result.

*Failure condition (double execution with dedup on, or NC1 not firing): did not occur.*

### C2 — Position, holding detection quality constant ✅

With the same evaluator, policy, events and fault schedule, the only variable being whether
the safeguard sits on the path:

| condition | arm | intervention recall | enforcement success given BLOCK |
|---|---|---|---|
| E1 no faults | in-series | **693/818** | **693/693** |
| E1 no faults | monitor | **0/818** | **0/693** |
| E2 all faults, fail-closed | in-series | **697/818** | **900/906** |
| E2 all faults, fail-closed | monitor | **0/818** | **0/1026** |

- **NC2a fired** (confound check, no faults): both arms 818/818 unsafe executed — identical.
- **NC2b fired** (confound check, faults + fail-open): both arms 818/818 — identical.

Both controls confirm the arms are indistinguishable when the mechanism under study is
neutralised, so the separation above is attributable to position.

**A design finding, recorded because the first control failed and was not tuned away.** NC2's
original form asserted the arms must be identical under a null policy *even with faults*. It
failed: 46 vs 50 unsafe executed. That was not a confound — it is the **fail-closed deadline**,
which belongs to the in-series mechanism and has no monitor counterpart. So: *an in-series gate
is not "a monitor plus blocking". Even under a policy that blocks nothing, its fail mode changes
outcomes.* The control was repaired by removing the deadline effect (fail-open), which is the
condition under which an in-series gate genuinely does degenerate to a monitor.

*Failure condition (arms differ under neutralised mechanism): did not occur.*

### C3 — Two-writer divergence detection ✅ — **the headline**

This is the research question answered as a measurement.

**The monitor's dashboard is wrong about every block it reports.** Under E2, the monitor arm's
telemetry reported **1,026 actions as PREVENTED. All 1,026 executed.** Not a degraded rate —
the entire population. A monitor's telemetry records *intentions*, and reads identically to a
record of *outcomes*.

**The single-writer system reports better numbers on identical faults.** E2 and E5 differ in
exactly one thing: whether the effect record is observed by the effector or derived from the
decision.

| | E2 two writers | E5 single writer |
|---|---|---|
| enforcement success given BLOCK | 900/906 = **99.34%** | 906/906 = **100.00%** |
| dashboard says prevented but executed | **6**/9,880 | **0**/9,880 |
| unsafe executions *in the ledger* | 121 | **115** |

Same fault schedule, same policy, same events, same 6 unsafe actions really executing. The
single-writer configuration reports perfect enforcement, zero divergence, and six fewer unsafe
executions than actually occurred. **That is the reassuring dashboard over the unsafe system,
measured rather than argued.**

- **NC3a fired** (power): 136 divergences detected across 10,000 joined decisions, each named
  with its decision id.
- **NC3b fired** (load-bearing): identical planted divergences, effect record derived from the
  decision → **0 detected**. The detector goes blind exactly when the writer split is removed,
  which is what distinguishes "we have two logs" from "the second log is doing the work".

*Failure conditions (detector silent in 3a, or firing in 3b): neither occurred.*

### C4 — Deadline behaviour and late intervention ✅

- Fail-closed vs fail-open on an identical schedule of dropped events and delayed evaluations:
  unsafe executed **106/818 → 222/818**. The deadline path is live, and the safety cost of
  fail-open is measured, not asserted.
- Under the full fault matrix: fail-closed 121 unsafe executed, fail-open 166.
- `LATE` and `NO_DECISION` are recorded as distinct outcomes, never folded into `BLOCKED` or
  `ALLOWED`.
- Latency, in-series, N=9,900: p50 **0.465 ms**, p95 **0.660 ms**, p99 **50.311 ms**, max
  **51.171 ms**. The p99 is the injected 50 ms evaluation delay, visible as designed.
- **NC4 fired**: fail-open strictly worse, so the deadline is not dead code.

*Failure condition (fail-closed executing unsafe actions through the deadline path): did not occur.*

### C5 — Replay under version skew ✅

Strata reported separately, never pooled:

| stratum | agreement | disagreements | attribution |
|---|---|---|---|
| pinned | **10,000/10,000** | 0 | — |
| skewed policy (1.0→1.1) | 9,871/10,000 | 129 | 129/129 attributed to the policy bump |
| skewed evaluator (1.0→1.1) | 9,875/10,000 | 125 | 125/125 attributed to the evaluator bump |
| skewed both | 9,746/10,000 | 254 | 254/254 attributed to both |

Pinned agreement at 100% establishes the decision path is deterministic. Every skewed
disagreement is attributed to a named version change; none is `UNATTRIBUTED`.

- **NC5 fired**: hand-flipping one recorded decision dropped pinned agreement to
  **9,999/10,000**. Replay recomputes from the event and compares; it never reads a stored
  agreement flag. This is the D087 defect made impossible.

*Failure conditions (pinned agreement below 100%, or agreement on a corrupted record): neither occurred.*

### C6 — Incident reconstruction from durable evidence ✅

From the two sealed chains alone, cold, with no in-memory state: **122 incidents**
reconstructed in **2.7 ms total, 0.022 ms per incident**, classified as 116 `NOT_DETECTED` and
6 `ENFORCEMENT_FAILED`.

- **NC6 fired** — the control most likely to fail, and the one most worth running. Deleting the
  decision record for a known incident flipped its reconstructed cause from `not_detected` to
  **`UNKNOWN`**, with `missing_evidence: ['decision record']`. The reconstructor reports what it
  cannot determine instead of inventing a cause.

*Failure conditions (confident wrong cause; UNKNOWN never returned): neither occurred.*

### C7 — Ledger integrity (REUSED, no new claim)

`omega_seal` imported, not reimplemented. **NC7 fired**: intact chain verifies, altering one
sealed field rejects. Exercised as a control on the harness only.

---

## NOT EARNED

Nothing was pre-registered and then abandoned. These are the boundaries the evidence does not
reach, restated after seeing the results:

- **No claim that an in-series control is sufficient.** The evidence shows a monitor is
  insufficient in specific measured ways. The converse does not follow, and E2 shows the
  in-series arm still let 121/818 unsafe actions through.
- **No claim about detection quality.** Recall is 693/818 because the evaluator has a
  deliberate blind spot. That number is a fact about a rule table I wrote.
- **No claim that the fault catalogue is complete.** It is a chosen list of twelve.
- **No claim of novelty** for any failure mode, or for latency/oversight/recovery as challenges
  — Lindner et al. (arXiv:2512.22154, Google DeepMind) name those; ETSI EN 319 102-1 has the
  version-skew catalogue (local D140); AgentChaos (arXiv:2608.06790) established chaos
  engineering on agent systems. The contribution claimed is **synthesis and measurement**:
  applying fault injection to the safeguards path rather than the LLM, and reporting enforcement
  conditional on BLOCK.
- **No claim about production scale, real adversaries, or real deployments.** Faults are
  injected, never encountered.
- **No claim that two writers are sufficient for accountability.** They detect this class of
  accidental divergence. An adversary controlling both defeats them entirely.
- **No claim that these rates transfer anywhere.** Every number is a fact about this testbed
  under a stated fault schedule.

See `LIMITATIONS.md` §10 for the five defects found in this system's own checkers during
development, all caught by the negative-control suite or the unit tests.
