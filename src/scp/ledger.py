"""Append-only, tamper-evident ledger. REUSED CAPABILITY -- no new claim is made here.

Sealing is provided by ``omega_seal``, an existing installed package in this environment.
It is imported, never reimplemented: there is a standing rule against writing a second
sealer, and a copied sealer would also make cross-artifact hash comparison meaningless.

If ``omega_seal`` is absent the module falls back to a clearly-labelled minimal chain so the
repository still runs standalone for a reader who does not have the package. The fallback is
NOT the canonical sealer and every run records which one was used, so no result can silently
be attributed to the wrong implementation.

Two separate chains are maintained -- one per writer -- and this separation is the whole
point of the design (C3). See ``divergence.py``.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any

try:  # pragma: no cover - environment dependent
    from omega_seal import base_record as _base
    from omega_seal import seal as _seal
    from omega_seal import verify_chain as _verify_chain

    SEALER = "omega_seal"
except Exception:  # noqa: BLE001 - ANY import failure must fall back  # pragma: no cover
    SEALER = "fallback (NOT the canonical omega_seal)"

    def _canon(v: Any) -> str:
        return json.dumps(v, sort_keys=True, separators=(",", ":"), ensure_ascii=True)

    def _base(record_type, *, record_id, created_at, previous_hash=None, **fields):
        return {
            "record_type": record_type,
            "record_id": record_id,
            "created_at": created_at,
            "previous_hash": previous_hash,
            "schema_version": "fallback/1.0",
            **fields,
        }

    def _seal(record):
        body = {k: v for k, v in record.items() if k != "content_hash" and v is not None}
        return {**record, "content_hash": hashlib.sha256(_canon(body).encode()).hexdigest()}

    def _verify_chain(records):
        prev = None
        for i, r in enumerate(records):
            body = {k: v for k, v in r.items() if k != "content_hash" and v is not None}
            if hashlib.sha256(_canon(body).encode()).hexdigest() != r.get("content_hash"):
                return {"ok": False, "index": i, "reason": "seal mismatch"}
            if r.get("previous_hash") != prev:
                return {"ok": False, "index": i, "reason": "chain break"}
            prev = r["content_hash"]
        return {"ok": True, "count": len(records)}


_MISSING = object()


@dataclass
class Chain:
    """One append-only sealed chain, owned by exactly one writer."""

    name: str
    records: list[dict] = field(default_factory=list)

    def append(self, record_type: str, record_id: str, **fields) -> dict:
        prev = self.records[-1]["content_hash"] if self.records else None
        rec = _base(
            record_type,
            record_id=record_id,
            created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            previous_hash=prev,
            **fields,
        )
        sealed = _seal(rec)
        self.records.append(sealed)
        return sealed

    def verify(self) -> dict:
        """Normalised verification result with a guaranteed boolean ``ok``.

        The canonical sealer reports ``chain_intact``; the fallback reports ``ok``. Reading
        the wrong key returns ``None``, which is FALSY -- so a check written as
        ``assert not result.get("ok")`` would have passed vacuously on a tampered chain AND
        on an intact one. That is a label-trust bug and it is exactly what NC7 caught during
        development. Normalising here means no caller can read a key that is not there.
        """
        raw = _verify_chain(self.records)
        if "ok" in raw:
            ok = bool(raw["ok"])
        elif "chain_intact" in raw:
            ok = bool(raw["chain_intact"])
        else:  # unknown sealer contract -- refuse to guess
            raise RuntimeError(f"cannot interpret verify_chain result: {sorted(raw)}")
        return {"ok": ok, "raw": raw, "sealer": SEALER}

    def tamper(self, index: int, field_name: str, value: Any) -> None:
        """Deliberately corrupt a record. Negative control for experiment H -- used to
        demonstrate the integrity check has power, never in a measured run.

        Refuses a no-op. Writing a field's existing value back changes no bytes, so the seal
        correctly still verifies and the control silently tests nothing. NC7 failed exactly
        this way during development: it "tampered" ``decision`` on a record whose decision was
        already ``ALLOW`` and then reported that tamper-evidence was broken. The guard makes
        that failure impossible to repeat.
        """
        old = self.records[index].get(field_name, _MISSING)
        if old == value:
            raise ValueError(
                f"no-op tamper: record[{index}].{field_name} is already {value!r}. "
                "A tamper that changes no bytes tests nothing."
            )
        self.records[index][field_name] = value

    def __len__(self) -> int:
        return len(self.records)

    def dump(self, path: str) -> None:
        with open(path, "w") as fh:
            fh.writelines(json.dumps(r) + "\n" for r in self.records)

    @staticmethod
    def load(name: str, path: str) -> Chain:
        c = Chain(name)
        with open(path) as fh:
            for line in fh:
                if line.strip():
                    c.records.append(json.loads(line))
        return c
