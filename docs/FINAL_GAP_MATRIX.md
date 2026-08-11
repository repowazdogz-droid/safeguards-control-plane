# Final gap matrix

Against the **verified** job requirements from Phase 0 (§1 of `PHASE0_GAP_AUDIT.md`). Only
postings fetched at primary source are scored; the two that 403'd are excluded rather than
guessed at.

Scoring is deliberately strict. **CLOSED** means the artifact is direct evidence a reviewer
could check. **PARTIAL** means it evidences the judgement but not the operational experience.
**OPEN** means the artifact says nothing.

---

## Against Anthropic Safeguards Labs (J1) — the closest-fitting role

| Requirement (verbatim) | Before | After | Evidence |
|---|---|---|---|
| "designing, building, and operating **production backend systems or data pipelines**" | OPEN | **PARTIAL** | Real broker with consumer groups, at-least-once delivery, idempotency keys, redelivery via `XAUTOCLAIM`, fail-closed deadlines. Designed and built — *not* operated in production. |
| "measure whether safeguards **actually work**, including in agentic settings" | PARTIAL | **CLOSED** | This is the artifact's whole subject. Enforcement conditional on BLOCK, measured with denominators, under twelve injected failure modes. |
| "hardened into production services that integrate with a **real-time safeguards path**" | PARTIAL | **PARTIAL** | An in-series real-time path exists and is measured end to end (p50 0.465 ms, p99 50.3 ms). Single-process; not a hardened service. |
| "Own **deployment, monitoring, and reliability**" | OPEN | **PARTIAL** | Reliability engineering is the core: fault injection, fail-open vs fail-closed, recovery after crash. No deployment, no on-call, no SLOs, no alerting. |
| "**testing, monitoring, and reliability** work" | PARTIAL | **CLOSED** | 31 unit tests, 9 negative controls, 5 documented defects found in the system's own checkers. |
| "**Python** and comfort working with **large datasets**" | CLOSED / OPEN | CLOSED / **OPEN** | Python yes. 10,000 events is not a large dataset and is not presented as one. |

## Against Safeguards Evals (J2)

| Requirement (verbatim) | Before | After | Evidence |
|---|---|---|---|
| "evaluation, **regression**, and release pipelines" | PARTIAL | **PARTIAL** | Version-skew replay is exactly regression-detection across evaluator and policy versions, with every disagreement attributed. Not wired into CI. |
| "building and maintaining **data pipelines**" | OPEN | **PARTIAL** | See J1. |
| "red teaming, **adversarial testing**" | CLOSED | CLOSED | Pre-existing strength; this adds infrastructure-fault adversarial testing to it. |

## Against Review Tooling (J3)

| Requirement (verbatim) | Before | After | Evidence |
|---|---|---|---|
| "**audit trails**, data-access controls" | CLOSED | CLOSED | Pre-existing (`omega_seal`), reused not rebuilt. Access controls: OPEN. |
| "surfacing metrics on ... **decision quality**" | PARTIAL | **CLOSED** | Metric layer where a rate cannot be printed without its denominator; exclusions named in the rate. |
| "**investigation** ... tooling" | OPEN | **PARTIAL** | Cold incident reconstruction from sealed evidence, 122 incidents in 2.7 ms, returns UNKNOWN when evidence is destroyed. CLI only — no reviewer UI, no queues. |

## Against Data Engineer, Safeguards (J4)

| Requirement (verbatim) | Before | After | Evidence |
|---|---|---|---|
| "**data quality frameworks**, monitoring, and alerting" | OPEN | **PARTIAL** | Evidence-completeness and divergence detection are data-quality checks on a safety pipeline, each with a negative control proving it can fail. No alerting. |
| dbt / Airflow / Spark / BigQuery | OPEN | OPEN | Untouched. |
| dashboards (Looker etc.) | OPEN | OPEN | Untouched — and note the artifact's own finding is that a dashboard is the *problem*, not the deliverable. |

---

## What actually moved

**Closed (3):** measuring whether safeguards work in agentic settings · testing and reliability
practice · decision-quality metrics with denominators.

**Partial (7):** production pipeline design · real-time safeguards path · reliability ownership ·
regression pipelines · data-pipeline maintenance · investigation tooling · data-quality frameworks.

**Still open (4):** large-scale data (10^6+, real corpora) · managed data stack (dbt/Airflow/
Spark/warehouse) · deployment and operations (cloud, on-call, SLOs, alerting) · reviewer-facing
UI and queue tooling.

## The honest summary

This artifact moves the candidate from *"formal-methods and authorization person"* to
*"formal-methods person who has also built and broken a safety-critical data path and measured
what happened"*. That is a real and checkable change.

It does **not** make anyone a distributed-systems or data-platform engineer. A hiring manager
reading the repo sees a single-machine, 10,000-event, three-minute experiment. The strength is
the *rigour* — pre-registered claims, nine negative controls, five self-inflicted defects
documented rather than hidden, denominators everywhere — not the scale.

The single most defensible thing to point at is the E2-vs-E5 comparison: under identical
faults, a single-writer safeguards system reported **100% enforcement success and zero
divergence** while the two-writer system on the same reality reported **99.34% and six
divergences**. That is a specific, reproducible, architectural finding about safety telemetry,
and it is the kind of thing the Safeguards Labs posting means by "measure whether safeguards
actually work".

## Cheapest next increments, ranked by evidence gained per hour

1. **CI** (GitHub Actions running `make test` + a small matrix) — closes "testing" properly and
   costs an hour.
2. **Concurrent consumers** (2–4 consumer processes on one group) — would exercise real
   consumer-group rebalancing and partial-failure, the biggest single gap in the transport claim.
3. **A second broker backend** (NATS JetStream behind the same `Transport` protocol) — turns
   the seam into a demonstrated abstraction and lets the same fault suite run against both.
4. **Scale to 10^6 events** with batched pipelining — would let "large datasets" be claimed
   without qualification.

None of these changes the findings; they widen the evidence base. Whether any is worth doing is
a judgement about marginal information gain, addressed in the closing report.
