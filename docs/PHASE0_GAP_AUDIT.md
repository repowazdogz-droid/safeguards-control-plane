# Phase 0 — Gap audit for a Safeguards Control Plane

Date: 2026-08-11. Nothing has been built. This document decides whether anything should be.

Verdict is at the end. Read §5 first if you only read one section.

---

## 0. What this audit is testing

The proposal is a production-shaped safeguards pipeline:

```
agent events → ingestion → evaluation → policy decision → enforcement
            → telemetry → evidence ledger → replay / incident reconstruction
```

with the research question: *when a safeguards system reports that it successfully
controlled an agent, what evidence establishes that the intervention actually happened
correctly and in time?*

Three ways this could fail Phase 0:

1. It reproduces existing work on this machine (the stated risk).
2. It reproduces existing public work.
3. It exercises capabilities already demonstrated rather than the missing one.

All three are tested below. Two of them come back partially positive, which is why the
verdict is REVISE rather than GO.

---

## 1. Live job requirements (primary sources)

Fetched directly from the posting pages on 2026-08-11, not from search summaries. Quotes
are verbatim from the fetched page. Where a page could not be retrieved it is marked
UNVERIFIED and no quote is attributed to it.

| # | Role | Source | Status |
|---|---|---|---|
| J1 | [Pipeline] Software Engineer, Safeguards Labs — Anthropic | [jobs.generalcatalyst.com/…/79002360](https://jobs.generalcatalyst.com/companies/anthropic/jobs/79002360-pipeline-software-engineer-safeguards-labs) | fetched, verbatim |
| J2 | Staff+ Software Engineer, Safeguards Evals — Anthropic | [job-boards.greenhouse.io/anthropic/jobs/5251671008](https://job-boards.greenhouse.io/anthropic/jobs/5251671008) | fetched, verbatim |
| J3 | Staff+ Software Engineer, Safeguards Review Tooling — Anthropic | [job-boards.greenhouse.io/anthropic/jobs/5342935008](https://job-boards.greenhouse.io/anthropic/jobs/5342935008) | fetched, verbatim |
| J4 | Data Engineer, Safeguards — Anthropic | [job-boards.greenhouse.io/anthropic/jobs/5240422008](https://job-boards.greenhouse.io/anthropic/jobs/5240422008) | fetched, bullets truncated by source page |
| J5 | Research Engineer, Frontier Safety Loss of Control — Google DeepMind | google.com/about/careers/…/111517850937500358 | **UNVERIFIED** — 403/truncated on fetch; only a search-engine summary was obtained. Treated as a lead, not evidence. |
| J6 | Security Engineer, Agent Security — OpenAI | openai.com/careers/security-engineer-agent-security-san-francisco | **UNVERIFIED** — HTTP 403 on fetch. Search summary only. |

### The requirement lines that matter

**J1, Safeguards Labs** (verbatim):
- "Take research prototypes and harden them into production services that integrate with Anthropic's **real-time safeguards path**."
- "Build data and evaluation infrastructure that lets the team iterate on prototypes quickly and **measure whether safeguards actually work**, including **in agentic settings**."
- "Own **deployment, monitoring, and reliability** for systems Labs ships."
- Minimum: "A track record of **designing, building, and operating production backend systems or data pipelines**."
- Minimum: "Experience taking software from prototype to production, including **testing, monitoring, and reliability work**."

**J2, Safeguards Evals** (verbatim):
- "Ship successful research into **evaluation, regression, and release pipelines** that run during model training, on every agent change, prompt update, and underlying model upgrade."
- Minimum: "Experience building and maintaining **data pipelines**."

**J3, Review Tooling** (verbatim):
- "Build in the guardrails that sensitive internal tools require: granular permissions, **audit trails**, data-access controls."
- "**Instrument the tools you ship** — surfacing metrics on queue health, reviewer throughput, and **decision quality**."

**J4, Data Engineer** (verbatim, truncated by the source page):
- "Implement **data quality frameworks, monitoring, and alerting** to ensure…"
- "Design, build, and maintain scalable data pipelines that support safety…"

### What the postings are actually asking for

Stripped of phrasing, the recurring demand is **operating a safety-critical data path**:
delivery, reliability, latency, instrumentation, audit trail, and measurement of whether the
safeguard worked. It is systems and data engineering under a safety obligation.

Not one of the four verified postings asks for formal verification, theorem proving, or
authorization-policy design. That is the shape of the gap, and it matches the mission's own
statement of it.

---

## 2. Public prior art

Citations resolved against the primary record (arXiv abstract page or the repository
itself), with title, authors and date confirmed. Findings quoted are from the abstract or
README text actually retrieved.

### 2.1 The nearest neighbour, and it is very near

**Lindner, Griffin, Korbak, Zimmermann, Irving, Farquhar, Cooney — "Practical challenges of
control monitoring in frontier AI deployments", arXiv:2512.22154, submitted 15 Dec 2025.**

Abstract, verbatim: *"Prior work has explored control monitoring in simplified settings, but
scaling monitoring to real-world deployments introduces additional dynamics: parallel agent
instances, non-negligible oversight latency, incremental attacks between agent instances,
and the difficulty of identifying scheming agents based on individual harmful actions. In
this paper, we analyse design choices to address these challenges, focusing on three forms
of monitoring with different latency-safety trade-offs: synchronous, semi-synchronous, and
asynchronous monitoring. We introduce a high-level safety case sketch as a tool for
understanding and comparing these monitoring protocols. Our analysis identifies three
challenges -- oversight, latency, and recovery -- and explores them in four case studies of
possible future AI deployments."*

This matters in two directions.

**Against the project:** the conceptual territory is occupied, by a Google DeepMind frontier
safety team, eight months ago. Latency-safety trade-offs and recovery are *their* named
axes. Any claim that this project *discovered* the latency or recovery problem would be
false.

**For the project:** the paper is an **analysis** — "we analyse design choices",
"safety case *sketch*", "four case studies of *possible future* deployments". It argues
about monitoring protocols; it does not build one, inject faults into it, or report
measured rates. The three challenges it names — oversight, latency, recovery — are stated
as open design problems, not as measured quantities.

So the correct framing is not *"nobody has thought about this"*. It is *"a frontier lab's
safety team named these three challenges as open, and nobody has built the testbed that
measures them."* That is a materially stronger and more honest position for a portfolio
artifact than a novelty claim would have been, and it removes the temptation to make one.

### 2.2 Control evaluation frameworks

**ControlArena** — UK AI Security Institute + Redwood Research
([github.com/UKGovernmentBEIS/control-arena](https://github.com/UKGovernmentBEIS/control-arena)).
README, verbatim: *"ControlArena is a Python library for running AI Control experiments,
created by the UK AI Security Institute and Redwood Research."* Provides settings, model
organisms, policies, monitors, and safety/usefulness metrics.

Checked directly against the repository: **no fault-injection mechanism** for dropped,
duplicated, reordered or delayed events; **no delivery-failure, enforcement-failure or
service-outage modelling**; metrics are suspicion scores and task completion, **not
enforcement success conditional on a block decision**.

The structural point: ControlArena holds the *infrastructure* fixed and varies the
*adversary*. It asks whether the monitor catches a scheming model. It assumes that when the
protocol decides to intervene, the intervention happens. That assumption is precisely what
this project proposes to attack.

### 2.3 Runtime enforcement frameworks

- **LlamaFirewall** (Meta, arXiv:2505.03574) — real-time guardrail framework, layered
  detectors (PromptGuard 2, Alignment Checks, CodeShield).
- **Progent** (arXiv:2504.11703) — privilege control at tool-call level, symbolic rules,
  deterministic enforcement with no LLM in the decision loop.
- **AgentSpec** (arXiv:2503.18666, ICSE'26) — DSL for runtime constraints on LLM agents.

All three are **detection and policy** systems. Each defines what should be blocked and
demonstrates that it blocks it on a benign transport. None reports what happens to its
guarantee when the transport underneath it drops, duplicates, reorders or delays the events
it is deciding on, and none measures whether a BLOCK decision resulted in a blocked action.

### 2.4 Fault injection into agent systems

**Tan et al. — "AgentChaos: Chaos Engineering for Agent Systems via Programmatic Fault
Injection", arXiv:2608.06790, submitted 7 Aug 2026** (four days old at time of audit).

Abstract, verbatim in relevant part: *"we inject faults at this shared layer without
modifying source code. We define crash, omission, and value faults on content and tool call
fields, intercept and modify LLM API responses at runtime… pass@1 dropping by up to 50
percentage points."*

This is the closest methodological neighbour and it is worth being precise about the
difference, because a careless reading makes this project look like a duplicate:

| | AgentChaos | This project |
|---|---|---|
| Fault injected into | the **LLM API** (the thing being governed) | the **safeguards path** (the thing doing the governing) |
| Fault types | crash, omission, value on model responses | drop, duplicate, reorder, delay, partial failure on safety events and enforcement acks |
| Metric | `pass@1` — task success | enforcement success conditional on BLOCK; telemetry–reality divergence |
| Question | does the agent still do its job when the model misbehaves | does the safeguard still control when the pipeline misbehaves |

Different target, different metric, opposite question. Its existence is useful rather than
threatening: it establishes chaos engineering on agent systems as a legitimate method and
shows that the safeguards-path variant is the one nobody ran.

### 2.5 What is *not* novel, stated plainly

The failure catalogue A–L is **canon in distributed systems**. At-least-once delivery,
idempotency keys, duplicate suppression, out-of-order arrival, fail-open versus fail-closed,
timeout-versus-cancel races, retry amplification — these are decades old and have textbook
answers. Nothing in this project discovers them.

Related: the version-skew catalogue (policy changes between execution and replay,
experiment G) is a rediscovery of **long-term signature validation** — ETSI EN 319 102-1
ships the same catalogue as normative status codes. That was already established on this
machine as decision D140 and it stands; it is why experiment G is scoped as *engineering
that handles a known problem*, not as a finding.

The claimable contribution is therefore **synthesis and measurement**, not law:
*applying an established fault-injection discipline to a component class where nobody has
applied it, and reporting the resulting rates with denominators.* Claimed as anything more
than that, it would be overclaiming.

---

## 3. Duplication against existing work on this machine

Assessed by reading implementations, not by matching repository names.

### 3.1 Capability matrix — the ten stated system requirements

| # | Requirement | Already built here? | Where | Call |
|---|---|---|---|---|
| 1 | Versioned event schema | Partial | `proof-carrying-tool-calls/schemas` (RFC 8785 pinned), `agent-ops-console/schemas` | Schema design is demonstrated. **Version *skew* handling is not.** |
| 2 | Durable event ingestion | **No** | — | `agent-ops-console` has a SQLite ledger of *tasks*, not an event stream with delivery semantics. **Genuinely new.** |
| 3 | Deterministic evaluator interface | **Yes, heavily** | `proof-carrying-evals`, `agent-governance-arena`, `evidence-audit` | **Do not rebuild.** Reuse a trivial one. |
| 4 | Policy / intervention engine | **Yes, heavily** | `spendfuse`, `mcp-assurance-lab/broker.py`, `ai-research-security-plane/src/arsp/policy.py`, Cedar work | **Do not rebuild.** |
| 5 | In-series intervention point | **Yes — this is the signature strength** | D100 (inline egress gate), D104, D106 (Mode 3 guardrail), `spendfuse`, `arsp/flow_guard.py` | **Do not re-prove.** Reuse as a fixed component. |
| 6 | Append-only evidence ledger | **Yes** | `omega_seal` (installed package) | **Import it.** Standing rule: never copy a sealer. |
| 7 | Replay from recorded inputs | **Yes** | `omega-replay-engine`, `inspect-replay` (D144/D145) | Record-level replay is done. **Replay under version skew is not.** |
| 8 | Policy / evaluator version tracking | Partial, and constrained | D140 killed the *benchmark* framing on prior art | Build as plumbing, claim nothing. |
| 9 | Metrics | Partial | `governed-fork-capture` (Observed Over-Block Rate) | One metric exists. The pipeline-failure metrics do not. |
| 10 | Incident reconstruction | Partial | `omega-replay-engine` | Record-level. **Pipeline-level reconstruction across a fault is not.** |

**Five of ten requirements are already substantially built.** Taken as a component list,
this project is roughly half a duplicate. That is a real finding and it is why the verdict
is not a clean GO.

### 3.2 Where the duplication *stops*

Direct search of the corpus for the machinery each of the twelve adversarial experiments
would need:

| Machinery | Present anywhere in the corpus? |
|---|---|
| Fault injection into a safeguards path | **None.** (Grep hits for "reorder" resolve to hash-chain splice detection in `omega_replay/seal.py` and `governed-fork-capture`, i.e. ledger tampering — not event delivery.) |
| Duplicate / dropped / reordered event handling | **None.** |
| At-least-once or idempotency semantics | **None.** |
| Enforcement success conditional on a BLOCK decision | **None.** Zero matches across all candidate repos. |
| Telemetry-versus-execution divergence detection | **None.** Zero matches. |
| Intervention latency, deadlines, late-arrival handling | **None** in the safeguards repos (`mcp-assurance-lab` latency hits are HTTP client timing in vendored reference code). |

So the corpus covers every **component** and none of the **seams**. Every one of the twelve
experiments A–L lives in a seam. Eight of them (A, B, C, D, I, J, K, L) are purely
transport and timing failures with no existing coverage at all; H is fully covered by
`omega_seal` and should be reused rather than rebuilt; E, F, G are partial.

### 3.3 Adjacent live thread — declare the overlap

`~/ai-research-security-plane` (ARSP) was in active development yesterday, 2026-08-10, and
contains `policy.py`, `telemetry.py`, `flow_guard.py`, an in-series integrity gate, and
deployed GCP infrastructure. Its domain is **model-artifact supply chain integrity**, not
agent-event safeguards, so the two do not collide on subject matter. They do collide on
*apparent* subject matter to an outside reader. If both end up in a portfolio, the
distinction has to be stated, or the two artifacts will be read as one idea told twice.

---

## 4. Smallest useful architecture

The duplication analysis dictates the design: **do not build components, build the seams
between them, and make the position of the safeguard the independent variable.**

### 4.1 The spine — two arms, one event stream

```
                       ┌──────────── Arm M (MONITOR) ─────────────┐
                       │  observes out-of-band, cannot prevent    │
                       │  → verdict → telemetry → DASHBOARD       │
   simulated  ──►  INGEST                                          
   agent env       (durable,  ├──────────── Arm S (IN-SERIES) ────────────┐
                    ordered,  │  sits on the ONLY path to the effector.   │
                    at-least- │  effector blocks until S returns a        │
                    once)     │  decision, or until the deadline fires.   │
                              └────────────────► EFFECTOR ────────────────┘
                                                    │
                              ┌─────────────────────┴──────────────────┐
                              │  effector writes its OWN independent   │
                              │  record of what it actually executed   │
                              └────────────────────────────────────────┘
                                                    │
                              SEALED LEDGER (omega_seal) ──► REPLAY ──► RECONSTRUCT
```

Both arms run the **same evaluator** and the **same policy**, on the **same events**. That
is the whole point: detection quality is held fixed by construction, so any measured
divergence between the two arms is attributable to **position and transport alone**. It
turns "a monitor is not a control" from an assertion into a controlled experiment with one
independent variable.

### 4.2 The independence constraint, and why it is load-bearing

The safeguards service records *"I decided BLOCK"*. The effector independently records
*"I executed / I did not execute"*. **These must be two separate writers.** If the same
component writes both, their agreement is a tautology — it is guaranteed by construction and
carries no information about whether enforcement actually happened.

This is the direct lesson of D087 on this machine, where a replay engine's set-level
agreement was found to be *hardcoded* `agreement=True` rather than checked. That defect is
the exact shape of the defect this project exists to detect, which makes it the first thing
the negative controls must rule out in this system's own code.

Experiment J (telemetry says BLOCK, execution proceeded) is **only detectable** because of
this two-writer split. Any architecture with a single writer cannot test J at all. This is
the one structural design decision the project cannot compromise on.

### 4.3 Components — build, reuse, or import

| Component | Status |
|---|---|
| `events/` versioned schema + content-addressed event id | **build** (small) |
| `ingest/` durable append-only log, sequence numbers, dedup by event id, explicit delivery semantics | **build — this is the new capability** |
| `evaluator/` deterministic, versioned, pure `event → verdict`; crashable on command | **reuse pattern**, ~50 lines |
| `policy/` versioned `verdict × context → ALLOW / BLOCK / ESCALATE` | **reuse pattern**, ~50 lines |
| `effector/` simulated agent environment; independent execution record | **build** (small) |
| `ledger/` sealed, append-only | **import `omega_seal`** — do not write a sealer |
| `replay/` re-run at pinned versions, detect skew | **build on `omega-replay-engine` prior art**, new part is version skew |
| `faultlab/` the injector for A–L | **build — this is the new capability** |
| `metrics/` every rate with its denominator | **build** |
| `reconstruct/` incident report CLI | **build** (small) |

Roughly 70% reuse of ideas already demonstrated, with new code concentrated in `ingest`,
`faultlab`, `metrics`, and the two-writer divergence check.

### 4.4 Metrics — defined before any experiment runs

Every rate below is written as numerator over denominator. A number without its denominator
does not get reported.

| Metric | Numerator | Denominator |
|---|---|---|
| Intervention recall | unsafe actions prevented in series | ground-truth-unsafe actions attempted (known — the simulator plants them) |
| False intervention rate | safe actions blocked | safe actions attempted |
| **Enforcement success given BLOCK** | BLOCK decisions where the *effector's own* record shows no execution | BLOCK decisions issued |
| Event loss rate | events emitted by producer, absent from ingest log | events emitted by producer |
| Late-intervention rate | BLOCKs delivered after the action already executed | BLOCKs issued |
| Intervention latency | distribution p50/p95/p99/max of (gate release − event emit) | gated actions, N stated |
| Replay agreement | replayed decisions identical to recorded decision | decisions replayed, **stratified by version-skew condition** |
| Evidence completeness | decisions with a full tuple {event, evaluator version, policy version, verdict, decision, enforcement outcome} | decisions issued |
| **Telemetry–reality divergence** | decisions where telemetry-reported outcome ≠ effector-recorded outcome | decisions issued |
| Time to detection | per fault class; **censoring reported** (faults never detected are not dropped from the denominator) | injected faults, per class |
| Time to reconstruction | wall-clock to a correct incident report | seeded incidents, N stated |

The two bolded metrics are the ones the corpus has no existing coverage of and the ones the
research question actually turns on.

### 4.5 Negative controls

Every checker must be demonstrated capable of failing, because a checker that cannot fail is
worse than no checker — it reports safety it never established.

1. **Vacuity check on every checker.** For each, exhibit an input on which it returns FAIL.
   Any checker with no such input is reported as vacuous and fixed or withdrawn. This is the
   D087 defect (`agreement=True` hardcoded) turned into a standing test.
2. **Planted violations.** Known-unsafe actions injected at known positions; confirm each is
   caught, and report any that are not.
3. **The green-dashboard control** — the one the mission singles out as most valuable, and
   the one this architecture is built to produce:
   > Inject a fault that breaks the effector's enforcement while leaving Arm M's observation
   > path intact. Arm M reports the unsafe action was detected and blocked; the dashboard is
   > green; **the effector's independent record shows the action executed.**

   That is a measured dashboard-reality gap, not an argued one. It is the answer to the
   research question stated as an experiment rather than as an opinion.

### 4.6 Scale — stated honestly

Single machine, no distributed cluster. Target: 10^4–10^5 synthetic events across ~10^3
trajectories, enough to expose throughput, ordering and recovery behaviour. Exact counts,
seeds, hardware and wall-clock recorded per run. This is **not** production scale and will
not be described as such.

---

## 5. Verdict — **REVISE, then GO**

Not a clean GO. Not a STOP.

**Why not STOP.** The seams are genuinely uncovered — locally and publicly. Locally: zero
matches for enforcement-conditional-on-BLOCK and zero for telemetry-versus-execution
divergence, across every candidate repository. Publicly: ControlArena's own repository
confirms no infrastructure-fault modelling; AgentChaos injects into the LLM, not the
safeguard; Lindner et al. name latency and recovery as open design problems and analyse
rather than measure them. And the capability the artifact exercises — operating a
safety-critical data path under fault — is the one the verified job postings actually ask
for and the one the existing corpus does not evidence.

**Why not a clean GO.** Five of the ten stated system requirements are already built here.
Run as specified, this project would spend most of its effort re-implementing an evaluator
interface, a policy engine, an in-series gate and a sealed ledger — all four of which are
already demonstrated, and the last of which there is a standing rule against rewriting. That
is the failure mode the mission asked to be checked for, and it is real.

**The required revision**, in one line: *stop treating the ten requirements as a build list;
treat components 3–7 as fixed reused parts and make the transport between them the object of
study, with safeguard position as the single independent variable.*

Concretely:
- **Do not build**: evaluator interface, policy engine, in-series gate, sealer, record-level replay.
- **Do build**: durable ingestion with explicit delivery semantics; the fault injector; the
  two-writer divergence check; the metric layer; version-skew replay; incident reconstruction.
- **Do not claim**: novelty of any failure mode in A–L, or of the latency and recovery
  challenges (Lindner et al. name them; ETSI has the version-skew catalogue per D140).
- **Do claim**: an implemented, fault-injected, measured testbed for challenges that a
  frontier lab's safety team named as open, with every rate carrying its denominator.

### 5.1 Claims the artifact could establish

- Measured rates, with denominators, for enforcement success given BLOCK, telemetry–reality
  divergence, event loss, intervention latency, replay agreement and evidence completeness,
  under each named fault class.
- A **demonstrated** case where safeguards telemetry reports a controlled outcome while an
  independent execution record shows the action occurred — the dashboard-reality gap as a
  reproducible measurement.
- That specific delivery or ordering guarantees are **necessary** for specific safety
  properties, by exhibiting a property violation when the guarantee is removed.
- Time-to-reconstruction for seeded incidents, with censoring reported.
- That a monitor and an in-series control, **holding detection quality constant**, produce
  measurably different safety outcomes under identical infrastructure faults.

### 5.2 Claims the artifact could NOT establish

- Anything about model intelligence, or whether an evaluator's *judgement* is correct. The
  evaluator is deterministic and simulated; construct validity is bounded to the simulation.
- Anything about production scale, real network partitions, real multi-tenant load, or a
  real adversary. Faults are injected, not encountered.
- That the fault catalogue is **complete**. It is a chosen list; absence of a failure mode
  from it is not evidence of its absence in reality.
- That an in-series control is **sufficient**. The design can show a monitor is insufficient
  in specific measured ways; it cannot show the converse.
- That the metric set is complete, or that the rates transfer to any real deployment.
- Any claim about what frontier labs actually run internally. Nothing here observes that.

### 5.3 The honest risk

The largest remaining risk is not prior art — it is **construct validity**. A simulated
environment with a deterministic evaluator can be built to make the in-series arm win. The
mitigation is that the simulator, the evaluator and the policy are shared identically
between the two arms and pinned before fault injection begins, so the experiment cannot be
tuned to a preferred outcome after seeing results. This must be enforced in code and stated
in `LIMITATIONS.md`, not left as an intention.

---

## 6. Sources

Job postings (fetched 2026-08-11):
- [Software Engineer, Safeguards Labs — Anthropic](https://jobs.generalcatalyst.com/companies/anthropic/jobs/79002360-pipeline-software-engineer-safeguards-labs)
- [Staff+ Software Engineer, Safeguards Evals — Anthropic](https://job-boards.greenhouse.io/anthropic/jobs/5251671008)
- [Staff+ Software Engineer, Safeguards Review Tooling — Anthropic](https://job-boards.greenhouse.io/anthropic/jobs/5342935008)
- [Data Engineer, Safeguards — Anthropic](https://job-boards.greenhouse.io/anthropic/jobs/5240422008)
- Google DeepMind Frontier Safety Loss of Control — UNVERIFIED, fetch failed
- OpenAI Security Engineer, Agent Security — UNVERIFIED, HTTP 403

Literature and systems:
- [arXiv:2512.22154 — Practical challenges of control monitoring in frontier AI deployments](https://arxiv.org/abs/2512.22154)
- [arXiv:2608.06790 — AgentChaos: Chaos Engineering for Agent Systems via Programmatic Fault Injection](https://arxiv.org/abs/2608.06790)
- [github.com/UKGovernmentBEIS/control-arena — ControlArena](https://github.com/UKGovernmentBEIS/control-arena)
- [arXiv:2505.03574 — LlamaFirewall](https://arxiv.org/abs/2505.03574)
- [arXiv:2504.11703 — Progent: Securing AI Agents with Privilege Control](https://arxiv.org/abs/2504.11703)
- [arXiv:2503.18666 — AgentSpec: Customizable Runtime Enforcement for Safe and Reliable LLM Agents](https://arxiv.org/abs/2503.18666)
- [arXiv:2602.16666 — Towards a Science of AI Agent Reliability](https://arxiv.org/abs/2602.16666)
- [arXiv:2603.00495 — AI Runtime Infrastructure](https://arxiv.org/abs/2603.00495)

Local decisions referenced: D087 (hardcoded agreement), D100/D104/D106 (in-series gates),
D140 (version-skew catalogue is ETSI prior art), D144/D145 (inspect-replay).
