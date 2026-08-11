#!/usr/bin/env python3
"""Assert on the CONTENTS of results/experiments.json, not on a previous step's exit code.

Why this file exists at all: the experiment runner already exits non-zero if a negative
control fails, and it would have been cheaper to let CI trust that. An exit code is a
label. A runner that produced *zero* controls would satisfy `n_pass == len(controls)` and
exit 0 just as happily as one that produced nine passing ones, and CI would go green on a
run that measured nothing. So these checks read the payload the exit code is supposed to
summarise -- including the count.

The assertions are split into two groups on purpose, because they are not equally robust:

  DETERMINISTIC -- fixed by seeds alone. Asserted to exact values. If one of these moves,
  a measured claim in CLAIMS.md has changed and the run must fail.

  CLOCK-SENSITIVE -- depend on wall-clock latency against the 25 ms deadline, so an
  individual event on a loaded runner can cross the deadline and shift a count by a few.
  Asserted as RELATIONS, which is what the claims actually assert anyway. Pinning these to
  exact integers would make CI flaky and would be asserting something stronger than
  CLAIMS.md ever claimed.

This file is CI scaffolding. It is not part of the `scp` package and does not run in any
measured experiment.
"""

from __future__ import annotations

import json
import pathlib
import sys

RESULTS = pathlib.Path("results/experiments.json")

failures: list[str] = []
checks = 0


def check(label: str, ok: bool, detail: str) -> None:
    global checks
    checks += 1
    status = "ok  " if ok else "FAIL"
    print(f"  [{status}] {label}: {detail}")
    if not ok:
        failures.append(f"{label}: {detail}")


def frac(s: str) -> tuple[int, int]:
    """Parse an 'n/d' string into its two integers. Refuses anything else."""
    num, _, den = s.partition("/")
    return int(num), int(den)


def main() -> int:
    if not RESULTS.exists():
        print(f"FATAL: {RESULTS} does not exist -- the experiment step produced no evidence.")
        return 1

    data = json.loads(RESULTS.read_text())
    rows = {(r["condition"], r["arm"]): r for r in data["conditions"]}
    controls = data["negative_controls"]
    skew = data["version_skew"]
    scenario = data["provenance"]["scenario"]

    print(f"sealer used: {data['provenance']['sealer']}")
    print("\n-- DETERMINISTIC (exact; fixed by seeds) --")

    check("scale", scenario["events"] == 10000 and scenario["unsafe_events"] == 818,
          f"{scenario['events']} events, {scenario['unsafe_events']} unsafe")

    # The count matters as much as the verdicts: nine controls that all pass is a result,
    # zero controls that all (vacuously) pass is not.
    check("negative controls present", len(controls) == 9, f"{len(controls)} controls found")
    not_fired = [c["id"] for c in controls if not c["passed"]]
    check("negative controls fired", not not_fired,
          "all 9 fired" if not not_fired else f"did NOT fire: {not_fired}")

    mon = rows[("E2_all_faults_failclosed", "monitor")]
    check("monitor prevents nothing", mon["intervention_recall"] == "0/818",
          mon["intervention_recall"])

    print("\n-- CLOCK-SENSITIVE (relations; exact counts vary with runner load) --")

    # Replay DENOMINATORS live here, not in the deterministic group. `replay()` only admits
    # decisions whose outcome is DECIDED/DECIDED_BACKLOG, and an event falls out of that set
    # when its measured wall-clock latency crosses the 25 ms deadline -- which can happen
    # under load with NO fault injected. Observed locally: 9,995 admitted on one loaded run,
    # 10,000 on five others. So the count is a property of the machine, and only the RATIO
    # is the claim C5 pre-registered ("replay at pinned versions reproduces every recorded
    # decision exactly"). Pinning the denominator here would assert something stronger than
    # CLAIMS.md claims, and would go red on a busy runner for a reason unrelated to correctness.
    pin_n, pin_d = frac(skew["pinned"]["agreement"])
    check("pinned replay reproduces EVERY replayable decision", pin_n == pin_d and pin_d > 0,
          f"{skew['pinned']['agreement']} (denominator is machine-dependent, ratio is the claim)")
    check("pinned replay has zero disagreements", skew["pinned"]["disagreements"] == 0,
          str(skew["pinned"]["disagreements"]))

    # Disagreement COUNTS are stable across runs (they depend on the policy/evaluator delta,
    # not on latency); the denominators they sit over are not. Assert the counts.
    for stratum, expected_disagreements in (("skewed_policy", 129),
                                            ("skewed_evaluator", 125),
                                            ("skewed_both", 254)):
        got = skew[stratum]["disagreements"]
        check(f"replay {stratum} disagreements", got == expected_disagreements,
              f"{got} (expected {expected_disagreements}); "
              f"agreement {skew[stratum]['agreement']}")
        unattributed = skew[stratum]["attribution"].get("UNATTRIBUTED", 0)
        check(f"replay {stratum} fully attributed", unattributed == 0,
              f"{unattributed} unattributed disagreements")

    e2 = rows[("E2_all_faults_failclosed", "in_series")]
    e5 = rows[("E5_single_writer", "in_series")]

    # C3, the headline. Same faults, same policy, same events; the only difference is
    # whether the effect record is observed or derived.
    e2_enf_n, e2_enf_d = frac(e2["enforcement_success_given_BLOCK"])
    e5_enf_n, e5_enf_d = frac(e5["enforcement_success_given_BLOCK"])
    check("single-writer reports PERFECT enforcement", e5_enf_n == e5_enf_d,
          f"{e5['enforcement_success_given_BLOCK']}")
    check("two-writer reports IMPERFECT enforcement on the same reality", e2_enf_n < e2_enf_d,
          f"{e2['enforcement_success_given_BLOCK']}")

    e2_lies, _ = frac(e2["dashboard_says_prevented_but_executed"])
    e5_lies, _ = frac(e5["dashboard_says_prevented_but_executed"])
    check("two-writer DETECTS divergence", e2_lies > 0, f"{e2_lies} detected")
    check("single-writer detects NONE of it", e5_lies == 0, f"{e5_lies} detected")
    check("single-writer ledger under-reports unsafe executions",
          e5["unsafe_executed"] < e2["unsafe_executed"],
          f"single-writer records {e5['unsafe_executed']}, two-writer {e2['unsafe_executed']}")

    # C2, position. Every BLOCK the monitor issues corresponds to an action that ran.
    mon_lies, _ = frac(mon["dashboard_says_prevented_but_executed"])
    _, mon_blocks = frac(mon["enforcement_success_given_BLOCK"])
    check("every monitor BLOCK is a dashboard lie", mon_lies == mon_blocks and mon_lies > 0,
          f"{mon_lies} lies over {mon_blocks} blocks")

    # C1, exactly-once effect.
    check("no double executions under fault", e2["double_executions"] == 0,
          str(e2["double_executions"]))
    e4 = rows[("E4_no_dedup", "in_series")]
    check("dedup is load-bearing (E4 without it)", e4["double_executions"] > 0,
          f"{e4['double_executions']} double executions with dedup off")

    print(f"\n{checks - len(failures)}/{checks} assertions passed")
    if failures:
        print("\nFAILED:")
        for f in failures:
            print(f"  - {f}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
