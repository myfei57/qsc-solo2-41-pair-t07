"""Query filters shared by the audit trail and the record stream."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

# Payload keys that name the piece of equipment an action happened to,
# in the order they should be tried.
SUBJECT_KEYS = (
    "valve_id",
    "pump_id",
    "tank_id",
    "batch_id",
    "gauge_id",
    "latch",
    "header_id",
    "subject",
)


def subject_from_payload(payload: Mapping[str, Any]) -> str:
    """Extracts the acted-upon device name from an event/record payload."""

    for key in SUBJECT_KEYS:
        value = payload.get(key)
        if value is not None and value != "":
            return str(value)
    return ""


@dataclass(frozen=True)
class RecordFilter:
    """Narrows a record set by kind, subject, sequence/time window and limit."""

    kinds: tuple[str, ...] = ()
    subject: str | None = None
    min_seq: int = 0
    max_seq: int = 0
    start_ts: int = 0
    end_ts: int = 0
    limit: int = 0

    def matches(self, record: Any) -> bool:
        if self.kinds and record.kind not in self.kinds:
            return False
        if self.min_seq and record.seq < self.min_seq:
            return False
        if self.max_seq and record.seq > self.max_seq:
            return False
        if self.start_ts and record.ts < self.start_ts:
            return False
        if self.end_ts and record.ts > self.end_ts:
            return False
        if self.subject is not None:
            subject = getattr(record, "subject", None)
            if subject is None:
                subject = subject_from_payload(record.payload)
            if subject != self.subject:
                return False
        return True

    def apply(self, records: Sequence[Any]) -> tuple[Any, ...]:
        selected = [record for record in records if self.matches(record)]
        if self.limit > 0:
            selected = selected[-self.limit :]
        return tuple(selected)

    def as_payload(self) -> dict[str, Any]:
        return {
            "kinds": list(self.kinds),
            "subject": self.subject,
            "min_seq": self.min_seq,
            "max_seq": self.max_seq,
            "start_ts": self.start_ts,
            "end_ts": self.end_ts,
            "limit": self.limit,
        }
