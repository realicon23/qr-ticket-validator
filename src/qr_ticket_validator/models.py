"""Domain models for ticket issuing and validation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp."""

    return datetime.now(timezone.utc)


class ValidationStatus(str, Enum):
    """Possible check-in validation outcomes."""

    ACCEPTED = "accepted"
    REJECTED_DUPLICATE = "rejected_duplicate_check_in"
    REJECTED_EXPIRED = "rejected_expired"
    REJECTED_INVALID_EVENT = "rejected_invalid_event"
    REJECTED_INVALID_SIGNATURE = "rejected_invalid_signature"
    REJECTED_UNKNOWN_TICKET = "rejected_unknown_ticket"


@dataclass(frozen=True)
class Attendee:
    """Ticket holder details."""

    name: str
    email: str


@dataclass
class Event:
    """Ticketed event configuration."""

    name: str
    starts_at: datetime
    capacity: int
    event_id: str = field(default_factory=lambda: str(uuid4()))

    def __post_init__(self) -> None:
        if self.capacity <= 0:
            raise ValueError("capacity must be greater than zero")
        if self.starts_at.tzinfo is None:
            raise ValueError("starts_at must be timezone-aware")


@dataclass
class Ticket:
    """Issued event ticket."""

    event_id: str
    attendee: Attendee
    issued_at: datetime
    expires_at: datetime
    ticket_id: str = field(default_factory=lambda: str(uuid4()))
    checked_in_at: datetime | None = None

    @property
    def is_checked_in(self) -> bool:
        return self.checked_in_at is not None

    def to_payload(self) -> dict[str, str]:
        """Serialize the ticket into a stable signing payload."""

        return {
            "ticket_id": self.ticket_id,
            "event_id": self.event_id,
            "attendee_name": self.attendee.name,
            "attendee_email": self.attendee.email,
            "issued_at": self.issued_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
        }


@dataclass(frozen=True)
class ValidationResult:
    """Result returned by the check-in validator."""

    status: ValidationStatus
    ticket_id: str | None
    event_id: str | None
    reason: str

    @property
    def accepted(self) -> bool:
        return self.status == ValidationStatus.ACCEPTED
