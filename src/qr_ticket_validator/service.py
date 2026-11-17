"""Ticket issuing and validation service."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import secrets
from typing import Any

from .audit import AuditLog
from .models import Attendee, Event, Ticket, ValidationResult, ValidationStatus, utc_now
from .signing import encode_token, verify_token


class TicketService:
    """Issue and validate signed digital event tickets."""

    def __init__(self, secret: str) -> None:
        if len(secret) < 16:
            raise ValueError("secret must be at least 16 characters")
        self._secret = secret
        self._events: dict[str, Event] = {}
        self._tickets: dict[str, Ticket] = {}
        self.audit_log = AuditLog()

    def create_event(self, name: str, starts_at: datetime, capacity: int) -> Event:
        event = Event(name=name, starts_at=starts_at, capacity=capacity)
        self._events[event.event_id] = event
        return event

    def issue_ticket(
        self,
        event_id: str,
        attendee: Attendee,
        valid_for: timedelta = timedelta(days=30),
    ) -> tuple[Ticket, str]:
        if event_id not in self._events:
            raise KeyError(f"unknown event_id: {event_id}")
        if self.issued_count(event_id) >= self._events[event_id].capacity:
            raise ValueError("event capacity reached")

        issued_at = utc_now()
        ticket = Ticket(
            event_id=event_id,
            attendee=attendee,
            issued_at=issued_at,
            expires_at=issued_at + valid_for,
        )
        self._tickets[ticket.ticket_id] = ticket
        token = encode_token(ticket.to_payload(), self._secret)
        return ticket, token

    def validate(self, token: str, expected_event_id: str, now: datetime | None = None) -> ValidationResult:
        now = now or utc_now()
        if now.tzinfo is None:
            raise ValueError("now must be timezone-aware")

        signature_valid, payload = verify_token(token, self._secret)
        if not signature_valid or payload is None:
            return self._record(
                ValidationStatus.REJECTED_INVALID_SIGNATURE,
                payload,
                "token signature could not be verified",
            )

        ticket_id = _string(payload, "ticket_id")
        event_id = _string(payload, "event_id")

        if event_id != expected_event_id:
            return self._record(
                ValidationStatus.REJECTED_INVALID_EVENT,
                payload,
                "ticket belongs to a different event",
            )

        ticket = self._tickets.get(ticket_id)
        if ticket is None:
            return self._record(
                ValidationStatus.REJECTED_UNKNOWN_TICKET,
                payload,
                "ticket is well-signed but not known to this validator",
            )

        if now > ticket.expires_at:
            return self._record(
                ValidationStatus.REJECTED_EXPIRED,
                payload,
                "ticket has expired",
            )

        if ticket.is_checked_in:
            return self._record(
                ValidationStatus.REJECTED_DUPLICATE,
                payload,
                "ticket has already been checked in",
            )

        ticket.checked_in_at = now
        return self._record(ValidationStatus.ACCEPTED, payload, "ticket accepted")

    def issued_count(self, event_id: str) -> int:
        return sum(1 for ticket in self._tickets.values() if ticket.event_id == event_id)

    def checked_in_count(self, event_id: str) -> int:
        return sum(
            1
            for ticket in self._tickets.values()
            if ticket.event_id == event_id and ticket.is_checked_in
        )

    def event_capacity_remaining(self, event_id: str) -> int:
        if event_id not in self._events:
            raise KeyError(f"unknown event_id: {event_id}")
        return self._events[event_id].capacity - self.issued_count(event_id)

    def _record(
        self,
        status: ValidationStatus,
        payload: dict[str, Any] | None,
        reason: str,
    ) -> ValidationResult:
        ticket_id = _string(payload, "ticket_id") if payload else None
        event_id = _string(payload, "event_id") if payload else None
        self.audit_log.add(status, ticket_id, event_id, reason)
        return ValidationResult(status=status, ticket_id=ticket_id, event_id=event_id, reason=reason)


def _string(payload: dict[str, Any] | None, key: str) -> str | None:
    if not payload:
        return None
    value = payload.get(key)
    return value if isinstance(value, str) else None


def demo_service() -> TicketService:
    """Create a service with one sample event and ticket for quick demos."""

    service = TicketService(secret=secrets.token_urlsafe(32))
    event = service.create_event(
        "Digital Literacy Workshop",
        starts_at=datetime(2026, 5, 2, 10, 0, tzinfo=timezone.utc),
        capacity=100,
    )
    service.issue_ticket(
        event.event_id,
        Attendee(name="Ada Lovelace", email="ada@example.com"),
    )
    return service
