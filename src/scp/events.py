"""Versioned safety-event schema with content-addressed identity.

Two things here are load-bearing and easy to get wrong.

1. ``event_id`` is a hash of the *logical action only* -- trajectory, sequence, tool and
   arguments. It deliberately excludes timestamps and delivery metadata, so a redelivered or
   duplicated event carries the SAME id as the original. That is what makes it usable as an
   idempotency key: exactly-once *effect* on top of at-least-once *delivery*.

2. ``ground_truth_unsafe`` is simulator bookkeeping. It is the denominator for intervention
   recall and it is NEVER visible to the evaluator -- ``visible()`` strips it. An evaluator
   that could read it would score perfectly and measure nothing.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

SCHEMA_VERSION = "scp.event/1.0"


def canonical(obj: Any) -> str:
    """Deterministic JSON: sorted keys, no whitespace, ASCII-escaped."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _digest(obj: Any) -> str:
    return hashlib.sha256(canonical(obj).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SafetyEvent:
    """One agent action submitted for safety evaluation."""

    trajectory_id: str
    seq: int
    tool: str
    args: dict[str, Any]
    emitted_at_ns: int
    ground_truth_unsafe: bool = False
    schema_version: str = SCHEMA_VERSION

    @property
    def event_id(self) -> str:
        """Content address over the logical action. Stable across redelivery."""
        return _digest(
            {
                "schema_version": self.schema_version,
                "trajectory_id": self.trajectory_id,
                "seq": self.seq,
                "tool": self.tool,
                "args": self.args,
            }
        )

    def visible(self) -> dict[str, Any]:
        """What the evaluator is allowed to see. Ground truth is stripped here."""
        return {
            "schema_version": self.schema_version,
            "event_id": self.event_id,
            "trajectory_id": self.trajectory_id,
            "seq": self.seq,
            "tool": self.tool,
            "args": dict(self.args),
        }

    def to_wire(self) -> dict[str, str]:
        """Flat string map for the broker. Ground truth rides along because the
        *simulator* needs it downstream; the evaluator boundary strips it, not the wire."""
        return {
            "schema_version": self.schema_version,
            "event_id": self.event_id,
            "trajectory_id": self.trajectory_id,
            "seq": str(self.seq),
            "tool": self.tool,
            "args": canonical(self.args),
            "emitted_at_ns": str(self.emitted_at_ns),
            "ground_truth_unsafe": "1" if self.ground_truth_unsafe else "0",
        }

    @staticmethod
    def from_wire(d: dict[str, str]) -> SafetyEvent:
        return SafetyEvent(
            trajectory_id=d["trajectory_id"],
            seq=int(d["seq"]),
            tool=d["tool"],
            args=json.loads(d["args"]),
            emitted_at_ns=int(d["emitted_at_ns"]),
            ground_truth_unsafe=d.get("ground_truth_unsafe") == "1",
            schema_version=d.get("schema_version", SCHEMA_VERSION),
        )


# --- schema versioning -------------------------------------------------------------------
# A second schema version exists so version-skew replay (C5) has something real to skew
# across, rather than a hypothetical. v1.1 adds `risk_hint`, which v1.0 consumers ignore.

SCHEMA_VERSION_V11 = "scp.event/1.1"


def migrate_v10_to_v11(wire: dict[str, str]) -> dict[str, str]:
    """Forward migration. Additive only: v1.0 readers still parse a v1.1 record."""
    out = dict(wire)
    out["schema_version"] = SCHEMA_VERSION_V11
    out.setdefault("risk_hint", "")
    return out
