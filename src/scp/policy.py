"""Versioned policy engine. SUBSTRATE -- not the object of study.

Deliberately trivial: threshold rules over the evaluator's risk score. Policy sophistication
is an already-demonstrated capability and adding more of it here would not measure anything
this project is about.

Two things are load-bearing:

* ``version`` and ``source_hash`` are recorded on every decision, so replay can pin them and
  version skew is *detected* rather than silently absorbed (C5).
* ``NullPolicy`` blocks nothing. It is the confound check for C2: under NullPolicy the two
  arms must produce identical outcomes, because nothing but position differs between them.
"""

from __future__ import annotations

import hashlib
import inspect
from dataclasses import dataclass
from enum import Enum


class Decision(str, Enum):
    ALLOW = "ALLOW"
    BLOCK = "BLOCK"
    ESCALATE = "ESCALATE"


@dataclass(frozen=True)
class PolicyOutcome:
    decision: Decision
    policy_version: str
    threshold: float


class Policy:
    """v1 -- the pinned production policy."""

    version = "policy/1.0"
    block_at = 0.7
    escalate_at = 0.4

    def decide(self, risk: float) -> PolicyOutcome:
        if risk >= self.block_at:
            d = Decision.BLOCK
        elif risk >= self.escalate_at:
            d = Decision.ESCALATE
        else:
            d = Decision.ALLOW
        return PolicyOutcome(d, self.version, self.block_at)

    @classmethod
    def source_hash(cls) -> str:
        return hashlib.sha256(inspect.getsource(cls).encode()).hexdigest()[:16]


class PolicyV2(Policy):
    """v1.1 -- block threshold RAISED after an over-blocking incident. Skew source for C5.

    The threshold is 0.8 for a specific reason. The first draft used 0.5, which looks like a
    bigger change but is a NO-OP: the evaluator only ever emits risks of
    0.0/0.75/0.8/0.85/0.9, none of which fall in [0.5, 0.7), so every decision came out
    identical and the skewed-replay stratum would have reported 100% agreement while nothing
    had actually changed -- a vacuous "skew handled correctly" result. 0.8 sits between 0.75
    and 0.8, so ``transfer_funds`` (0.75) genuinely flips BLOCK -> ESCALATE.

    A version bump that changes no behaviour cannot test whether version skew is detected.
    """

    version = "policy/1.1"
    block_at = 0.8


class NullPolicy(Policy):
    """Blocks nothing. The C2 confound check: with this policy the two arms MUST agree."""

    version = "policy/null"
    block_at = 2.0
    escalate_at = 2.0
