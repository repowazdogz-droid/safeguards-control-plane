"""Metrics that cannot be reported without a denominator.

``Rate`` carries its numerator and denominator and formats as ``n/d = p%``. There is no way
to get a bare percentage out of it, which is the point: the standing rule on this machine is
that a number without its denominator is not a measurement, and the cheapest way to enforce
a rule is to make violating it require extra effort.

``Rate`` with ``d == 0`` reports ``undefined``, never ``0%`` and never ``100%``. An empty
denominator is missing evidence, not a clean result.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass


@dataclass(frozen=True)
class Rate:
    name: str
    numerator: int
    denominator: int
    excluded: str = ""

    @property
    def value(self) -> float | None:
        if self.denominator == 0:
            return None
        return self.numerator / self.denominator

    def __str__(self) -> str:
        if self.denominator == 0:
            return f"{self.name}: 0/0 = undefined (no denominator)"
        pct = 100.0 * self.numerator / self.denominator
        tail = f"  [excludes: {self.excluded}]" if self.excluded else ""
        return f"{self.name}: {self.numerator}/{self.denominator} = {pct:.2f}%{tail}"

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "numerator": self.numerator,
            "denominator": self.denominator,
            "value": self.value,
            "excluded": self.excluded or None,
        }


@dataclass(frozen=True)
class Distribution:
    """Latency and similar. N is always carried; a percentile without N is not reportable."""

    name: str
    samples: tuple[float, ...]
    unit: str = "ms"

    def _q(self, p: float) -> float:
        if not self.samples:
            return float("nan")
        s = sorted(self.samples)
        k = min(round(p * (len(s) - 1)), len(s) - 1)
        return s[k]

    def __str__(self) -> str:
        if not self.samples:
            return f"{self.name}: N=0 (undefined)"
        return (
            f"{self.name}: N={len(self.samples)}  "
            f"p50={self._q(0.50):.3f}{self.unit}  p95={self._q(0.95):.3f}{self.unit}  "
            f"p99={self._q(0.99):.3f}{self.unit}  max={max(self.samples):.3f}{self.unit}  "
            f"mean={statistics.fmean(self.samples):.3f}{self.unit}"
        )

    def as_dict(self) -> dict:
        if not self.samples:
            return {"name": self.name, "n": 0}
        return {
            "name": self.name,
            "n": len(self.samples),
            "unit": self.unit,
            "p50": self._q(0.50),
            "p95": self._q(0.95),
            "p99": self._q(0.99),
            "max": max(self.samples),
            "mean": statistics.fmean(self.samples),
        }


@dataclass
class MetricSet:
    rates: list[Rate]
    distributions: list[Distribution]
    context: dict

    def render(self) -> str:
        lines = []
        for r in self.rates:
            lines.append("  " + str(r))
        for d in self.distributions:
            lines.append("  " + str(d))
        return "\n".join(lines)

    def as_dict(self) -> dict:
        return {
            "context": self.context,
            "rates": [r.as_dict() for r in self.rates],
            "distributions": [d.as_dict() for d in self.distributions],
        }
