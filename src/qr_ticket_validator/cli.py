"""Command-line interface for the ticket validator demo."""

from __future__ import annotations

import argparse
import secrets
from datetime import datetime, timezone

from .models import Attendee
from .service import TicketService
from .signing import decode_token, encode_token


def run_demo() -> None:
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
    first = service.validate(token, expected_event_id=event.event_id)
    print(f"First validation: {first.status.value}")
    second = service.validate(token, expected_event_id=event.event_id)
    print(f"Second validation: {second.status.value}")

    envelope = decode_token(token)
    envelope["payload"]["attendee_email"] = "tampered@example.com"
    tampered = encode_token(envelope["payload"], "wrong-secret-value")
    third = service.validate(tampered, expected_event_id=event.event_id)
    print(f"Tampered validation: {third.status.value}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Signed QR ticket validation demo")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("demo", help="run the in-memory demo")
    args = parser.parse_args()

    if args.command == "demo":
        run_demo()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
