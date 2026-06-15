"""Structured audit records for ticket validation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .models import ValidationStatus, utc_now


@dataclass(frozen=True)
class AuditRecord:
    """One check-in decision."""

    timestamp: datetime
    status: ValidationStatus
    ticket_id: str | None
    event_id: str | None
    reason: str


class AuditLog:
    """In-memory audit log suitable for demos and tests."""

    def __init__(self) -> None:
        self._records: list[AuditRecord] = []

    def add(
        self,
        status: ValidationStatus,
        ticket_id: str | None,
        event_id: str | None,
        reason: str,
    ) -> None:
        self._records.append(
            AuditRecord(
                timestamp=utc_now(),
                status=status,
                ticket_id=ticket_id,
                event_id=event_id,
                reason=reason,
            )
        )

    def records(self) -> list[AuditRecord]:
        return list(self._records)
