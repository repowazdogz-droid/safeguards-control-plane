# CI

`.github/workflows/ci.yml` reproduces the evidence in `CLAIMS.md` from a clean checkout.
Locally: `make ci-local`.

## What it runs

| step | check |
|---|---|
| `pytest -q` | 31 unit tests (in-memory transport, no broker) |
| `python -m scp.experiments --trajectories 500 --steps 20` | 9 negative controls, condition matrix E1–E6, version-skew replay, incident reconstruction — 10,000 events against a real Redis Streams consumer group |
| `python .github/assert_results.py` | 20 assertions read from `results/experiments.json` |

## Two deliberate choices

**It asserts on the evidence, not on the exit code.** The runner already exits non-zero when a
negative control fails, and trusting that would have been cheaper. An exit code is a label: a
run that produced *zero* controls satisfies `n_pass == len(controls)` and exits 0 exactly like
a run that produced nine passing ones. So `assert_results.py` reads the payload the exit code
summarises — including `len(controls) == 9`.

**It does not install `omega_seal`.** That package is local and private, so CI exercises the
labelled fallback sealer. This was verified to produce **byte-identical measured numbers**: the
sealer determines content hashes, not decisions. Every run prints which sealer was used, so a
fallback can never be silently mistaken for the canonical one.

## Deterministic vs clock-sensitive assertions

The assertions are split, because they are not equally robust.

**Deterministic** — fixed by seeds alone, asserted to exact values: scenario scale
(10,000 events / 818 unsafe), the presence and passing of all 9 negative controls, and that the
monitor arm prevents nothing (`0/818`).

**Clock-sensitive** — asserted as relations or as counts that do not depend on latency. The
in-series gate compares measured wall-clock latency against a 25 ms deadline, so under load an
event can cross it with no fault injected and drop out of the replayable set. Replay
*denominators* therefore vary by machine (9,995 and 9,999 observed, 10,000 on most runs).

Disagreement *counts* are stable only conditional on a full denominator, which an earlier version
of this document got wrong. If the event that dropped out was itself a disagreement, the count
falls with it: denominator 10,000 gives 125 `skewed_evaluator` disagreements and denominator 9,999
gives 124, both observed directly. CI therefore asserts the ratio for the pinned stratum, and for
the skewed strata asserts the count within the band the denominator allows, since each dropped
event can remove at most one disagreement.

Pinning the denominator would assert something stronger than `CLAIMS.md` claims and would go
red for reasons unrelated to correctness. See `LIMITATIONS.md` §11 for the unimplemented fix
(injected logical time source) that would make the counts hard constants.

## Pinned versions

| | pinned to | why |
|---|---|---|
| runner | `ubuntu-24.04` | `ubuntu-latest` silently changes the toolchain under a workflow whose job is reproducing fixed numbers |
| Redis | `redis:7.4-alpine` | matches the local container that produced the committed results (`redis-server v=7.4.10`); `docker-compose.yml` is pinned to the same tag |
| Python | `3.13` | development was on 3.13.15; the runner's patch version may differ |
| dependencies | `requirements-ci.txt`, exact `==` pins including transitives | a resolver free to move transitive deps is not reproducible |
| actions | full commit SHAs, with the version tag in a trailing comment | a mutable tag is not a pin |

Installed with `pip install -e . --no-deps` so `pyproject.toml`'s looser ranges cannot override
the pinned set.
