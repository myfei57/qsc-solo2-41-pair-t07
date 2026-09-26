"""Audit entries and the log that observes the durable record stream."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from tankfarm.clock import LogicalClock
from tankfarm.event import EventBus
from tankfarm.event.topics import AUDITED_TOPICS
from tankfarm.ids import IdFactory
from tankfarm.journal.record import Record
from tankfarm.judgement.filters import subject_from_payload
from tankfarm.store.repository import Repository

@dataclass(frozen=True)
class AuditEntry:
    entry_id: str
    seq: int
    kind: str
    subject: str
    message: str
    ts: int

    @property
    def payload(self) -> dict[str, Any]:
        return self.as_payload()

    def as_payload(self) -> dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "seq": self.seq,
            "kind": self.kind,
            "subject": self.subject,
            "message": self.message,
            "ts": self.ts,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "AuditEntry":
        return cls(
            entry_id=str(payload["entry_id"]),
            seq=int(payload["seq"]),
            kind=str(payload["kind"]),
            subject=str(payload.get("subject", "")),
            message=str(payload.get("message", "")),
            ts=int(payload["ts"]),
        )

class AuditLog:
    """Turns every durable action record into one append-only audit entry.

    Repeated actions on the same device each leave their own trace, and the
    on-disk log is reloaded on restart.
    """

    def __init__(
        self,
        repository: Repository,
        bus: EventBus,
        ids: IdFactory,
        clock: LogicalClock,
    ) -> None:
        self._repository = repository
        self._bus = bus
        self._ids = ids
        self._clock = clock
        self._entries: list[AuditEntry] = []

    def observe_journal(self, journal: Any) -> None:
        """Feeds each durable action record into the audit trail."""

        journal.add_observer(self._on_record)

    def entries(self) -> tuple[AuditEntry, ...]:
        return tuple(self._entries)

    def restore(self) -> int:
        """Reloads audit entries written by earlier process runs."""

        entries = [AuditEntry.from_payload(item) for item in self._repository.load_audit()]
        entries.sort(key=lambda item: item.seq)
        self._entries = entries
        for entry in entries:
            # Keep the id factory counters consistent so new entries never
            # reuse identifiers owned by restored entries.
            self._ids.seed("audit", _numeric_suffix(entry.entry_id))
        return len(entries)

    def record(self, kind: str, subject: str, message: str, ts: int | None = None) -> AuditEntry:
        entry = AuditEntry(
            entry_id=self._ids.new("audit"),
            seq=len(self._entries) + 1,
            kind=kind,
            subject=subject,
            message=message,
            ts=self._clock.now() if ts is None else int(ts),
        )
        self._entries.append(entry)
        self._repository.append_audit(entry.as_payload())
        return entry

    def _on_record(self, record: Record) -> None:
        if record.kind not in AUDITED_TOPICS:
            return
        payload = dict(record.payload)
        subject = subject_from_payload(payload)
        self.record(record.kind, subject, _describe(record.kind, payload), record.ts)


def _numeric_suffix(entry_id: str) -> int:
    head, _, raw = entry_id.rpartition("-")
    return int(raw) if head and raw.isdigit() else 0


def _describe(topic: str, payload: Mapping[str, Any]) -> str:
    parts = [f"{key}={payload[key]}" for key in sorted(payload) if _is_scalar(payload[key])]
    return f"{topic} " + " ".join(parts) if parts else topic


def _is_scalar(value: Any) -> bool:
    return isinstance(value, (str, int, float, bool))
