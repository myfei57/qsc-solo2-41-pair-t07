"""Audit entries and the log that subscribes to the event bus."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from tankfarm.clock import LogicalClock
from tankfarm.event import Event, EventBus
from tankfarm.event.topics import AUDITED_TOPICS
from tankfarm.ids import IdFactory
from tankfarm.store.repository import Repository

# Payload keys that identify the device an audit entry belongs to, in order
# of preference. An event without any of them is a system-level action whose
# subject is the empty string.
_SUBJECT_KEYS = (
    "tank_id",
    "new_tank_id",
    "old_tank_id",
    "valve_id",
    "pump_id",
    "batch_id",
    "header_id",
    "gauge_id",
    "latch",
)


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
            "tank_id": self.subject,
            "message": self.message,
            "ts": self.ts,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "AuditEntry":
        subject = payload.get("subject")
        if not isinstance(subject, str) or not subject:
            # Entries written before the subject was captured kept it only in
            # the device id fields; recover it from whichever one is present.
            subject = next(
                (
                    str(payload[key])
                    for key in _SUBJECT_KEYS
                    if isinstance(payload.get(key), str) and payload[key]
                ),
                "",
            )
        return cls(
            entry_id=str(payload["entry_id"]),
            seq=int(payload["seq"]),
            kind=str(payload["kind"]),
            subject=subject,
            message=str(payload.get("message", "")),
            ts=int(payload["ts"]),
        )


def subject_of(payload: Mapping[str, Any]) -> str:
    """Extract the device identifier carried by an event payload."""

    for key in _SUBJECT_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


class AuditLog:
    """Turns every published event into one durable audit entry."""

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
        self._loaded = False

    def subscribe(self) -> None:
        for topic in AUDITED_TOPICS:
            self._bus.subscribe(topic, self._on_event)

    def entries(self) -> tuple[AuditEntry, ...]:
        return tuple(self._entries)

    def restore(self) -> int:
        """Load previously persisted entries so the trail survives restarts."""

        if self._loaded:
            return len(self._entries)
        loaded = [AuditEntry.from_payload(item) for item in self._repository.load_audit()]
        loaded.sort(key=lambda entry: entry.seq)
        self._entries = loaded
        for entry in loaded:
            self._ids.resume("audit", entry.entry_id)
        self._loaded = True
        return len(loaded)

    def record(self, kind: str, subject: str, message: str) -> AuditEntry:
        entry = AuditEntry(
            entry_id=self._ids.new("audit"),
            seq=len(self._entries) + 1,
            kind=kind,
            subject=subject,
            message=message,
            ts=self._clock.now(),
        )
        # The trail is append-only: every action leaves its own mark, even
        # when the same device moves several times in a row.
        self._entries.append(entry)
        self._repository.append_audit(entry.as_payload())
        return entry

    def _on_event(self, event: Event) -> None:
        payload = event.value if isinstance(event.value, Mapping) else {}
        self.record(event.topic, subject_of(payload), _describe(event.topic, payload))


def _describe(topic: str, payload: Mapping[str, Any]) -> str:
    parts = [f"{key}={payload[key]}" for key in sorted(payload) if _is_scalar(payload[key])]
    return f"{topic} " + " ".join(parts) if parts else topic


def _is_scalar(value: Any) -> bool:
    return isinstance(value, (str, int, float, bool))
