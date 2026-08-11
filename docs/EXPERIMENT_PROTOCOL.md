# Experiment protocol

## Pre-registration discipline

`CLAIMS.md` was written **before implementation**, with each claim carrying a pre-committed
pass condition, failure condition, and a negative control. The original is in git history.
This ordering is the main defence against designing experiments toward a green result: the
conditions were fixed while the outcome was still unknown.

Two things follow, and both were honoured:

- **A failing control is repaired only when the control itself is shown to be mis-specified,
  and the counterexample is preserved in writing.** This happened once (NC2 — see `CLAIMS.md`
  C2). It was not tuned away; the finding it produced is recorded.
- **A defect found in a checker is documented, not quietly fixed.** Five were found; all are
  in `LIMITATIONS.md` §10.

## Order of operations

1. **Provenance.** Record sealer, Python version, host, Redis URL, scenario description, fault
   rate. Printed at the head of every run and stored in `results/experiments.json`.
2. **Negative controls first.** All nine. If a control does not fire, its claim is reported
   VACUOUS rather than earned, and the condition matrix result for that claim is not used.
3. **Condition matrix.** E1–E6, each with its own stated fault schedule and denominators.
4. **Version-skew replay.** Four strata, reported separately, never pooled.
5. **Incident reconstruction.** Cold, from the sealed chains only.

The runner exits non-zero if any negative control fails, so a green run is a claim that all
nine fired.

## Controlled variables

Held identical across the two arms, and enforced in code by `assert_arms_comparable`, which
refuses to run on mismatch:

- evaluator source hash and version
- policy source hash and version
- scenario seed and the digest of all event ids
- fault schedule seed and entries

Varied deliberately, one at a time:

| condition | what changes |
|---|---|
| E1 → E2 | fault schedule: none → all twelve classes at rate 0.12 |
| E2 → E3 | fail mode: closed → open |
| E2 → E4 | idempotency: on → off |
| E2 → E5 | **writers: two → one** (the architectural variable) |
| E2 → E6 | second evaluator added |
| arm S → arm M | **position** (the positional variable) |

## Determinism

Fault schedules are built from a seed onto specific `(trajectory_id, seq)` positions and
round-robin the fault kinds, so no kind can vanish by chance and every fault is attributable
to a known position. Re-running with the same seeds reproduces the schedule exactly
(`test_schedule_is_deterministic`).

The one non-deterministic element is wall-clock latency, which depends on machine load. Latency
percentiles will vary between runs; decision outcomes will not, except where a decision depends
on the deadline (the `DELAY_EVALUATION` path), which is why `LATE` is recorded as a distinct
outcome rather than folded into a decision.

## Denominator rules

- Every rate is a `Rate(numerator, denominator)`; there is no API that yields a bare percentage.
- `denominator == 0` prints `undefined`, never `0%` or `100%`.
- Exclusions are named in the rate itself — e.g. BLOCKs with no independent effect record are
  `unconfirmable`, excluded from the enforcement denominator, and counted separately.
- Decision records are deduplicated by `event_id` before counting, because a reordered event
  produces an extra backlog decision record and counting both would give two different
  denominators over the same population.

## Reproducing

```bash
make install          # venv + deps + omega_seal (editable, from ~/omega-seal)
make up               # Redis 7 on :6380 (6379 is deliberately left alone)
make test             # 31 unit tests, InMemoryTransport, no broker
make experiments      # full matrix → results/full_run.txt + results/experiments.json
```

The in-memory transport is a test double only. `scp.run` refuses to produce a measured result
from it, and no number in `CLAIMS.md` comes from it.
