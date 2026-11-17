"""Run a quick QR ticket validation scenario."""

from datetime import datetime, timezone
from pathlib import Path
import secrets
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from qr_ticket_validator import Attendee, TicketService  # noqa: E402
from qr_ticket_validator.signing import decode_token, encode_token  # noqa: E402


def main() -> None:
    service = TicketService(secret=secrets.token_urlsafe(32))
    event = service.create_event(
        "Sample Event",
        starts_at=datetime(2026, 5, 2, 10, 0, tzinfo=timezone.utc),
        capacity=100,
    )
    ticket, token = service.issue_ticket(
        event.event_id,
        Attendee(name="Ada Lovelace", email="ada@example.com"),
    )

    print(f"Issued ticket for {ticket.attendee.name}")
    print(f"Token preview: {token[:48]}...")

    first = service.validate(token, expected_event_id=event.event_id)
    print(f"First validation: {first.status.value}")

    second = service.validate(token, expected_event_id=event.event_id)
    print(f"Second validation: {second.status.value}")

    envelope = decode_token(token)
    envelope["payload"]["attendee_email"] = "tampered@example.com"
    tampered = encode_token(envelope["payload"], "wrong-secret-value")
    third = service.validate(tampered, expected_event_id=event.event_id)
    print(f"Tampered validation: {third.status.value}")

    print("\nAudit trail:")
    for record in service.audit_log.records():
        print(f"- {record.status.value}: {record.reason}")


if __name__ == "__main__":
    main()
