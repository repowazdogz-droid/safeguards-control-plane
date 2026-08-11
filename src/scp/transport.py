"""Event transport with explicit delivery semantics.

``RedisStreamsTransport`` is the one used for every reported result. It uses real consumer
groups -- XADD / XREADGROUP / XACK / XPENDING / XAUTOCLAIM -- so at-least-once delivery and
redelivery-after-consumer-death are the broker's behaviour, not a simulation of it written by
me. The pending-entries count is read back from Redis, so it is externally checkable.

``InMemoryTransport`` exists only so unit tests run without Docker. No experimental number in
this repository is produced from it, and ``run.py`` refuses to use it for a measured run.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .events import SafetyEvent


@dataclass(frozen=True)
class Delivery:
    """One delivery attempt. ``redelivered`` is true when the broker is re-handing us an
    entry a previous consumer took but never acked."""

    delivery_id: str
    event: SafetyEvent
    redelivered: bool = False


class TransportError(RuntimeError):
    """Raised when the transport itself is unavailable (experiment L)."""


class Transport(Protocol):
    def publish(self, event: SafetyEvent) -> str: ...
    def consume(
        self, group: str, consumer: str, count: int = 10, block_ms: int = 100
    ) -> list[Delivery]: ...
    def ack(self, group: str, delivery_id: str) -> None: ...
    def pending(self, group: str) -> int: ...
    def reclaim(self, group: str, consumer: str, min_idle_ms: int = 0) -> list[Delivery]: ...
    def ensure_group(self, group: str) -> None: ...
    def reset(self) -> None: ...


class RedisStreamsTransport:
    """At-least-once delivery over a Redis Stream consumer group."""

    def __init__(self, url: str = "redis://localhost:6379/0", stream: str = "scp.events"):
        import redis  # imported here so unit tests need no redis installed

        self._r = redis.Redis.from_url(url, decode_responses=True)
        self.stream = stream

    def ping(self) -> bool:
        try:
            return bool(self._r.ping())
        except Exception as exc:  # pragma: no cover - environment dependent
            raise TransportError(f"redis unreachable: {exc}") from exc

    def ensure_group(self, group: str) -> None:
        import redis

        try:
            self._r.xgroup_create(self.stream, group, id="0", mkstream=True)
        except redis.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    def publish(self, event: SafetyEvent) -> str:
        return self._r.xadd(self.stream, event.to_wire())

    def consume(
        self, group: str, consumer: str, count: int = 10, block_ms: int = 100
    ) -> list[Delivery]:
        resp = self._r.xreadgroup(
            group, consumer, {self.stream: ">"}, count=count, block=block_ms
        )
        out: list[Delivery] = []
        for _stream, entries in resp or []:
            for did, fields in entries:
                out.append(Delivery(did, SafetyEvent.from_wire(fields), redelivered=False))
        return out

    def reclaim(self, group: str, consumer: str, min_idle_ms: int = 0) -> list[Delivery]:
        """Take over entries a dead consumer never acked. This is the real redelivery path
        exercised by experiment E (evaluator crashes) and K (retry causes double action)."""
        out: list[Delivery] = []
        cursor = "0-0"
        while True:
            res = self._r.xautoclaim(
                self.stream, group, consumer, min_idle_time=min_idle_ms, start_id=cursor,
                count=100,
            )
            cursor, entries = res[0], res[1]
            for did, fields in entries:
                if fields:
                    out.append(Delivery(did, SafetyEvent.from_wire(fields), redelivered=True))
            if cursor in ("0-0", None) or not entries:
                break
        return out

    def ack(self, group: str, delivery_id: str) -> None:
        self._r.xack(self.stream, group, delivery_id)

    def pending(self, group: str) -> int:
        info = self._r.xpending(self.stream, group)
        return int(info["pending"]) if isinstance(info, dict) else int(info[0] or 0)

    def stream_length(self) -> int:
        return int(self._r.xlen(self.stream))

    def reset(self) -> None:
        self._r.delete(self.stream)


class InMemoryTransport:
    """Test double. Same interface, no durability. Never used for reported results."""

    def __init__(self) -> None:
        self._entries: list[tuple[str, SafetyEvent]] = []
        self._cursor: dict[str, int] = {}
        self._unacked: dict[str, dict[str, SafetyEvent]] = {}
        self._n = 0

    def ensure_group(self, group: str) -> None:
        self._cursor.setdefault(group, 0)
        self._unacked.setdefault(group, {})

    def publish(self, event: SafetyEvent) -> str:
        self._n += 1
        did = f"{self._n}-0"
        self._entries.append((did, event))
        return did

    def consume(self, group, consumer, count=10, block_ms=100) -> list[Delivery]:
        self.ensure_group(group)
        i = self._cursor[group]
        chunk = self._entries[i : i + count]
        self._cursor[group] = i + len(chunk)
        for did, ev in chunk:
            self._unacked[group][did] = ev
        return [Delivery(d, e) for d, e in chunk]

    def reclaim(self, group, consumer, min_idle_ms=0) -> list[Delivery]:
        self.ensure_group(group)
        return [Delivery(d, e, redelivered=True) for d, e in self._unacked[group].items()]

    def ack(self, group: str, delivery_id: str) -> None:
        self.ensure_group(group)
        self._unacked[group].pop(delivery_id, None)

    def pending(self, group: str) -> int:
        self.ensure_group(group)
        return len(self._unacked[group])

    def stream_length(self) -> int:
        return len(self._entries)

    def reset(self) -> None:
        self._entries.clear()
        self._cursor.clear()
        self._unacked.clear()
        self._n = 0
