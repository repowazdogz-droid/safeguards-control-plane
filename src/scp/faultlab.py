"""Deterministic, scheduled fault injection into the safeguards path.

Faults are *scheduled*, not probabilistic. A schedule is built once from a seed and maps
specific ``(trajectory_id, seq)`` positions to specific fault kinds. Two consequences, both
required by the measurement plan:

* every run is reproducible from ``(seed, fault_rate, kinds)``;
* every observed failure is attributable to a *named* fault at a *known* position, which is
  what makes incident reconstruction (C6) gradeable against ground truth.

Randomised chaos would make time-to-detection and reconstruction correctness
unreproducible, destroying the measurements this project exists to make.

Faults land at three different layers, so the schedule is a shared object each layer
queries rather than a single wrapper:

* transport boundary  -- DROP, DUPLICATE, REORDER, OUTAGE
* evaluation          -- DELAY, CRASH, DISAGREE
* enforcement/report  -- ENFORCEMENT_FAIL, TELEMETRY_LIE, RETRY_DOUBLE
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import Enum

from .events import SafetyEvent
from .transport import Delivery, Transport, TransportError


class Fault(str, Enum):
    # A-C, L: transport boundary
    DROP_EVENT = "drop_event"
    DUPLICATE_EVENT = "duplicate_event"
    REORDER_EVENT = "reorder_event"
    TRANSPORT_OUTAGE = "transport_outage"
    # D-F: evaluation
    DELAY_EVALUATION = "delay_evaluation"
    EVALUATOR_CRASH = "evaluator_crash"
    EVALUATOR_DISAGREE = "evaluator_disagree"
    # I-K: enforcement and reporting
    ENFORCEMENT_FAIL = "enforcement_fail"
    TELEMETRY_LIE = "telemetry_lie"
    RETRY_DOUBLE = "retry_double"


TRANSPORT_FAULTS = {
    Fault.DROP_EVENT,
    Fault.DUPLICATE_EVENT,
    Fault.REORDER_EVENT,
    Fault.TRANSPORT_OUTAGE,
}


@dataclass
class FaultSchedule:
    """Immutable-after-build map from event position to injected fault."""

    seed: int
    entries: dict[tuple[str, int], Fault] = field(default_factory=dict)

    def get(self, event: SafetyEvent) -> Fault | None:
        return self.entries.get((event.trajectory_id, event.seq))

    def has(self, event: SafetyEvent, kind: Fault) -> bool:
        return self.get(event) is kind

    def count(self, kind: Fault) -> int:
        return sum(1 for f in self.entries.values() if f is kind)

    def total(self) -> int:
        return len(self.entries)

    def summary(self) -> dict[str, int]:
        return {k.value: self.count(k) for k in Fault if self.count(k)}

    @staticmethod
    def build(
        events: list[SafetyEvent],
        *,
        seed: int,
        fault_rate: float,
        kinds: list[Fault] | None = None,
    ) -> FaultSchedule:
        """Pick ``fault_rate`` of positions, round-robin the requested kinds over them.

        Round-robin rather than random *choice* of kind, so each kind gets a predictable
        share and no kind can vanish from a run by chance.
        """
        kinds = list(kinds or list(Fault))
        rng = random.Random(seed)
        n = round(len(events) * fault_rate)
        picked = rng.sample(range(len(events)), k=min(n, len(events)))
        picked.sort()
        entries: dict[tuple[str, int], Fault] = {}
        for i, idx in enumerate(picked):
            ev = events[idx]
            entries[(ev.trajectory_id, ev.seq)] = kinds[i % len(kinds)]
        return FaultSchedule(seed=seed, entries=entries)

    @staticmethod
    def empty(seed: int = 0) -> FaultSchedule:
        return FaultSchedule(seed=seed, entries={})


class FaultyTransport:
    """Wraps a real transport and injects the four transport-layer faults at publish time.

    Note what this does NOT do: it never reaches inside the broker. Redis' own delivery
    guarantees stay intact; the faults model a lossy or misbehaving *edge* around it -- a
    producer whose write is lost, a retry that double-writes, an out-of-order arrival, a
    connection outage. That is the honest scope of the injection.
    """

    def __init__(self, inner: Transport, schedule: FaultSchedule):
        self.inner = inner
        self.schedule = schedule
        self.dropped: list[str] = []
        self.duplicated: list[str] = []
        self.outaged: list[str] = []
        self._reorder_buffer: list[SafetyEvent] = []

    # --- producer side ---------------------------------------------------------------
    def publish(self, event: SafetyEvent) -> str | None:
        fault = self.schedule.get(event)

        if fault is Fault.DROP_EVENT:
            # The producer believes it published. Nothing reaches the broker.
            self.dropped.append(event.event_id)
            return None

        if fault is Fault.TRANSPORT_OUTAGE:
            self.outaged.append(event.event_id)
            raise TransportError(f"injected outage publishing {event.event_id[:12]}")

        if fault is Fault.REORDER_EVENT:
            # Hold this one back; it is released after the NEXT publish, so it arrives late
            # and out of sequence.
            self._reorder_buffer.append(event)
            return None

        did = self.inner.publish(event)

        if fault is Fault.DUPLICATE_EVENT:
            self.inner.publish(event)  # same event_id -- dedup must absorb this
            self.duplicated.append(event.event_id)

        while self._reorder_buffer:
            held = self._reorder_buffer.pop(0)
            self.inner.publish(held)

        return did

    def flush_reorder_buffer(self) -> int:
        """Release anything still held, so a run does not end with events stuck in the edge."""
        n = len(self._reorder_buffer)
        while self._reorder_buffer:
            self.inner.publish(self._reorder_buffer.pop(0))
        return n

    # --- consumer side (pass-through) ------------------------------------------------
    def ensure_group(self, group: str) -> None:
        self.inner.ensure_group(group)

    def consume(self, group, consumer, count=10, block_ms=100) -> list[Delivery]:
        return self.inner.consume(group, consumer, count=count, block_ms=block_ms)

    def reclaim(self, group, consumer, min_idle_ms=0) -> list[Delivery]:
        return self.inner.reclaim(group, consumer, min_idle_ms=min_idle_ms)

    def ack(self, group: str, delivery_id: str) -> None:
        self.inner.ack(group, delivery_id)

    def pending(self, group: str) -> int:
        return self.inner.pending(group)

    def stream_length(self) -> int:
        return self.inner.stream_length()

    def reset(self) -> None:
        self.inner.reset()
