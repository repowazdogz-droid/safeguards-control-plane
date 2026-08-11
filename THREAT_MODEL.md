# Threat model

Scoped narrowly and deliberately. This system models **infrastructure failure**, not
adversaries. Saying so precisely is more useful than claiming broad coverage.

---

## What is being defended

The property: *when the safeguards system reports that an unsafe agent action was
controlled, that action did not happen.*

Note what this is **not**. It is not "the agent cannot do harm" and not "the evaluator
detects all unsafe actions". It is narrower and checkable: **the report matches reality.**

## Trust boundaries

```
┌─ UNTRUSTED ──────────┐   ┌─ UNDER TEST ────────────────────┐   ┌─ TRUSTED BASE ────────┐
│ agent action stream  │──►│ ingestion · evaluation · policy │──►│ Redis Streams internals│
│ (may attempt unsafe  │   │ enforcement · telemetry         │   │ omega_seal sealing     │
│  actions)            │   │ ledger · replay · reconstruction│   │ Python runtime, OS     │
└──────────────────────┘   └─────────────────────────────────┘   │ the fault catalogue    │
                                                                  │ the scenario grammar   │
                                                                  │ evaluator rule table   │
                                                                  └───────────────────────┘
```

The right-hand column is load-bearing and worth restating: **I authored the fault catalogue,
the scenario grammar and the evaluator's rules.** They are premises the measurements inherit,
not evidence the measurements produce.

## Adversary model: deliberately weak

The only "adversary" is the simulated agent, which attempts unsafe actions drawn from a fixed
grammar. It does **not** adapt, does not know the policy, does not target the evaluator's blind
spot, and does not attack the infrastructure.

**Explicitly out of scope**, and none of these is defended against:

| Not modelled | Why it matters |
|---|---|
| An adversary who controls **both** writers | The two-writer split detects *accidental* divergence. Someone who can forge the effector's record defeats it entirely. |
| Prompt injection, jailbreaks, evaluator evasion | Detection quality is held constant by design; this measures position and transport, not detection. |
| A scheming model timing actions around oversight windows | That is the AI-control literature's subject (Lindner et al., ControlArena). Not attempted here. |
| Compromise of the broker, host, or ledger storage | Trusted base. |
| Insider modification of policy or evaluator | Version skew is *detected* at replay, but nothing prevents the change. |
| Denial of service, resource exhaustion, back-pressure collapse | Not modelled. |
| Side channels, covert channels, collusion between agents | Not modelled. |

## The failure model that IS covered

Twelve failure modes, all **non-adversarial** — bugs, races, outages and misconfiguration.
This matters: the `series-vs-monitor` discipline holds that a label-trust failure needs no
adversary, and every one of these sets a misleading label without anyone choosing to.

| # | Failure | Injected as | Where it lands |
|---|---|---|---|
| A | Safety event dropped | `DROP_EVENT` | producer edge; no decision arrives |
| B | Duplicate event | `DUPLICATE_EVENT` | same `event_id` twice |
| C | Reordered event | `REORDER_EVENT` | held back, arrives after successor |
| D | Intervention too late | `DELAY_EVALUATION` | pushes decision past deadline |
| E | Evaluator crashes | `EVALUATOR_CRASH` | unacked → genuine broker redelivery |
| F | Evaluators disagree | `DisagreeingEvaluator` | recorded, never silently resolved |
| G | Policy changed before replay | `PolicyV2` / `EvaluatorV2` | detected and attributed at replay |
| H | Evidence tampered | `Chain.tamper` | rejected by seal (reused capability) |
| I | Enforcement fails after BLOCK | `ENFORCEMENT_FAIL` | gate decided, effector ran anyway |
| J | Telemetry says BLOCK, execution proceeded | consequence of I | visible **only** via the two-writer join |
| K | Retry intervenes twice | `RETRY_DOUBLE` | absorbed by idempotency key |
| L | Safeguards service unavailable | `TRANSPORT_OUTAGE` | publish raises; must fail closed |

**J is the one that matters most.** It is not a separate injected fault — it is what I is
*observed as* on a dashboard. Telemetry is built on the decision chain, exactly as real
dashboards are, because that is the log the safeguards service owns. The gap only becomes
visible when a second, independent writer records what actually ran.

## The structural claim

A monitor and an in-series gate are **not** two implementations of the same safeguard. The
monitor cannot occupy the position required to prevent, and its telemetry is therefore a
record of *intentions*, not *outcomes*. Measured at 10,000 events: the monitor arm's dashboard
reported 1,026 actions as PREVENTED and **all 1,026 executed**.

The corresponding architectural claim: a safeguards system whose telemetry and whose
enforcement record share a single writer **cannot** detect its own enforcement failures. Under
identical faults, the single-writer configuration reported 100% enforcement success while the
two-writer configuration reported 99.34% on the same underlying reality.
