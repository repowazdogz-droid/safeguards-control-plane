# LIMITATIONS

Read this before any number in `results/` is quoted anywhere.

---

## 1. The largest limitation: this is a testbed, not a deployment

Every number is a fact about **this synthetic system under a stated fault schedule**. Faults
are *injected*, never encountered. Nothing here observes a real agent, a real safeguards
service, a real adversary, or a real production incident. No rate transfers to any deployed
system, and none is offered as if it did.

## 2. Construct validity — the honest risk

The environment is synthetic and the evaluator is a table of string rules. A testbed like
this **can be built to make the in-series arm win**. Four things constrain that, and none of
them eliminates it:

- Evaluator, policy and scenario are **pinned and hashed** before injection, and
  `assert_arms_comparable` refuses to run if the arms see different values. So the
  comparison cannot be tuned *after* seeing results.
- Ground truth is **withheld** from the evaluator (`SafetyEvent.visible()` strips it).
- The evaluator is **deliberately imperfect** — it has no rule for `send_email` exfiltration,
  which is why recall is 693/818, not 818/818.
- Negative control NC2a/NC2b shows the arms are indistinguishable when the mechanism under
  study is neutralised.

What none of that fixes: **I chose the fault catalogue, the scenario grammar and the
evaluator's blind spot.** Those choices are part of the trusted base, not part of the
evidence. A different catalogue would produce different numbers.

## 3. Scale

10,000 events, 500 trajectories, 20 steps each, ~818 unsafe actions, single machine
(macOS 15.7.3, arm64, Python 3.13.15), one Redis 7 container, ~1,200 events/s per arm,
~8.3 s per condition, ~3 min for the full matrix.

This is **not production scale** and is not described as such. Throughput here is bounded by
a single-threaded Python loop doing a synchronous broker round-trip per event, not by the
broker. No claim is made about behaviour at 10^6 events, under concurrent producers, or with
multiple consumer processes.

## 4. What the fault injection does and does not touch

Faults are injected at the **edge around** the broker (producer-side drop, duplicate,
reorder, outage) and inside the safeguards path (evaluation delay, evaluator crash,
enforcement failure, retry). They **never reach inside Redis**. Redis Streams' own delivery
guarantees are *assumed correct and not verified here*. The consumer-crash path (experiment
E) does exercise genuine broker redelivery via `XAUTOCLAIM`, so at-least-once redelivery is
real rather than simulated — but the broker's correctness is a trusted dependency.

Not modelled: host power loss, disk corruption, multi-node replication and failover, real
network partitions, clock skew between hosts, Byzantine components, concurrent producers,
back-pressure, and consumer-group rebalancing under load.

## 5. Clocks

All latency is measured with `time.monotonic_ns()` **inside a single process**. It therefore
measures in-process scheduling plus broker round-trip. It is **not** distributed latency and
carries no information about clock skew. The p99 of ~50 ms in the in-series arm is dominated
by the injected `DELAY_EVALUATION` fault (50 ms), not by the broker.

## 6. The two arms are more identical than any real pair would be

Arm M and Arm S share one process, one evaluator object, one policy object and one event
stream. That is what makes the comparison clean — position is the only variable — and it is
also why the comparison is **not** a model of a real monitor-vs-gate deployment, where the
two would differ in a dozen other ways. C2 measures the effect of position *in isolation*,
which is a narrower and more defensible thing than "in-series beats monitoring".

## 7. Specific measurement caveats

- **`blocks_unconfirmable`.** Under transport outage the in-series arm refuses the action and
  writes a decision, but the effector never sees the event, so no independent effect record
  exists. Those 120 BLOCKs are excluded from the `enforcement_success_given_BLOCK`
  denominator and reported separately. They are **not** counted as successes: without a second
  writer we cannot confirm non-execution, and that is exactly the point the metric makes.
- **`intervention_recall` conflates two things** — actions the evaluator never flagged and
  actions it flagged but the system failed to stop. `enforcement_success_given_BLOCK` is the
  metric that separates them. Recall alone should not be read as a safeguard quality score.
- **Version-skew strata are never pooled.** A pooled agreement figure across pinned and
  skewed replay would be meaningless.
- **The single-writer condition (E5) reports better numbers than the two-writer condition
  (E2) on identical faults.** That is the finding, not a measurement error. See CLAIMS.md.

## 8. Reused, not built here

Tamper-evident sealing is `omega_seal`, an existing package. It is imported, not
reimplemented, and **no claim is made about it**. `SEALER` is recorded in every run so a
fallback can never be silently mistaken for the canonical sealer. If you run without
`omega_seal` installed, the fallback is used and every result is labelled accordingly.

## 9. Prior art this does not claim to have discovered

- The failure catalogue A–L is **distributed-systems canon** with textbook answers.
- Latency, oversight and recovery as challenges for control monitoring are named in
  Lindner et al., *Practical challenges of control monitoring in frontier AI deployments*,
  arXiv:2512.22154 (Google DeepMind, Dec 2025). This project implements and measures; it did
  not identify them.
- The version-skew catalogue is a rediscovery of long-term signature validation
  (ETSI EN 319 102-1), established locally as D140.
- Chaos engineering on agent systems is established — see AgentChaos, arXiv:2608.06790. The
  difference here is the *target* (the safeguards path, not the LLM API) and the *metric*
  (enforcement conditional on BLOCK, not task success).

## 10. Defects found in this system's own checkers during development

Kept here because a checker-quality claim is worthless without them. All four were caught by
the negative-control suite or the unit tests, and all are fixed:

1. **Duplicate counter measured the wrong thing.** It counted absorbed *deliveries* as
   duplicate *effects*, so it was already non-zero with deduplication ON — leaving NC1 with no
   power. Split into `double_executions` and `absorbed_duplicates`.
2. **`verify_chain` label-trust bug.** The code read `result["ok"]`; `omega_seal` returns
   `chain_intact`. The missing key returned `None`, which is falsy — so a check written as
   "assert the tampered chain is rejected" would have **passed vacuously on every input**.
   Caught only because NC7 also required the intact chain to verify *true*. Now normalised,
   and an unknown sealer contract raises rather than guesses.
3. **No-op tamper.** NC7 "tampered" a record by writing back the value it already held,
   changing no bytes. `Chain.tamper` now refuses a no-op.
4. **Vacuous policy version bump.** `PolicyV2` originally moved the block threshold 0.7 → 0.5,
   but no evaluator risk falls in that band, so the "skewed" replay stratum would have shown
   100% agreement while nothing had changed. Caught by a unit test; threshold moved to 0.8,
   which genuinely flips `transfer_funds`.
5. **Double-counted denominator.** Reordered events produce an extra backlog decision record;
   the block denominator counted both while the divergence join deduped — two different
   denominators over the same population. Now deduped by `event_id` on both sides.

A sixth item is a *design* finding rather than a defect, recorded in CLAIMS.md under C2: the
first version of NC2 asserted the two arms must be identical under a null policy even with
faults. It failed (46 vs 50 unsafe executed). That was not a confound — it is the fail-closed
deadline, which belongs to the in-series mechanism and has no monitor counterpart. The
counterexample was preserved and the control repaired, not tuned away.
