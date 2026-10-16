# Changelog

## 0.1.0 (2026-10-16)

First tagged release: `master` at `08e5254` (2026-08-12), plus README, metadata and CI changes on top.

- Fault-injected testbed: two arms (in-series and monitor) over the same 10,000 events, evaluator, policy and twelve scheduled faults (A to L), against a real Redis Streams consumer group.
- Two-writer divergence detection, with a `single_writer` mode as its negative control (NC3b): with the effect record derived from the decision, the detector reports 0 of the divergences the two-writer configuration finds.
- Nine negative controls (NC1 to NC7, with NC2 and NC3 split into a and b), all firing; results in `results/experiments.json` and `results/full_run.txt`.
- 31 unit tests (`pytest`, in-memory transport, no broker).
- CI (`.github/workflows/ci.yml`): unit tests, the full matrix against a Redis 7.4 service container, and assertions on the contents of `results/experiments.json` rather than on the runner's exit code. The committed results file is deleted before the run so the assertions cannot read it.
- CI steps run under pipefail, so a failing experiment cannot pass through `tee`.
- README opens with the claim, a reproduction command and its limits; added CITATION.cff and this file.
