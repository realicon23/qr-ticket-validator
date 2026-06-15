from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from qr_ticket_validator import Attendee, TicketService
from qr_ticket_validator.models import ValidationStatus
from qr_ticket_validator.signing import decode_token, encode_token


class TicketServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.service = TicketService(secret="test-secret-value")
        self.event = self.service.create_event(
            "Digital Literacy Workshop",
            starts_at=datetime(2026, 5, 2, 10, 0, tzinfo=timezone.utc),
            capacity=2,
        )

    def test_issue_and_validate_ticket(self) -> None:
        _, token = self.service.issue_ticket(
            self.event.event_id,
            Attendee(name="Ada Lovelace", email="ada@example.com"),
        )

        result = self.service.validate(token, expected_event_id=self.event.event_id)

        self.assertTrue(result.accepted)
        self.assertEqual(result.status, ValidationStatus.ACCEPTED)
        self.assertEqual(self.service.checked_in_count(self.event.event_id), 1)

    def test_duplicate_check_in_is_rejected(self) -> None:
        _, token = self.service.issue_ticket(
            self.event.event_id,
            Attendee(name="Grace Hopper", email="grace@example.com"),
        )

        first = self.service.validate(token, expected_event_id=self.event.event_id)
        second = self.service.validate(token, expected_event_id=self.event.event_id)

        self.assertTrue(first.accepted)
        self.assertFalse(second.accepted)
        self.assertEqual(second.status, ValidationStatus.REJECTED_DUPLICATE)

    def test_tampered_token_is_rejected(self) -> None:
        _, token = self.service.issue_ticket(
            self.event.event_id,
            Attendee(name="Katherine Johnson", email="katherine@example.com"),
        )
        envelope = decode_token(token)
        envelope["payload"]["attendee_email"] = "changed@example.com"
        tampered = encode_token(envelope["payload"], "wrong-secret-value")

        result = self.service.validate(tampered, expected_event_id=self.event.event_id)

        self.assertFalse(result.accepted)
        self.assertEqual(result.status, ValidationStatus.REJECTED_INVALID_SIGNATURE)

    def test_wrong_event_is_rejected(self) -> None:
        _, token = self.service.issue_ticket(
            self.event.event_id,
            Attendee(name="Alan Turing", email="alan@example.com"),
        )
        other_event = self.service.create_event(
            "Another Event",
            starts_at=datetime(2026, 6, 1, 10, 0, tzinfo=timezone.utc),
            capacity=10,
        )

        result = self.service.validate(token, expected_event_id=other_event.event_id)

        self.assertFalse(result.accepted)
        self.assertEqual(result.status, ValidationStatus.REJECTED_INVALID_EVENT)

    def test_expired_ticket_is_rejected(self) -> None:
        _, token = self.service.issue_ticket(
            self.event.event_id,
            Attendee(name="Mary Jackson", email="mary@example.com"),
            valid_for=timedelta(seconds=1),
        )
        later = datetime.now(timezone.utc) + timedelta(minutes=5)

        result = self.service.validate(token, expected_event_id=self.event.event_id, now=later)

        self.assertFalse(result.accepted)
        self.assertEqual(result.status, ValidationStatus.REJECTED_EXPIRED)

    def test_capacity_limit_is_enforced(self) -> None:
        self.service.issue_ticket(
            self.event.event_id,
            Attendee(name="Ada Lovelace", email="ada@example.com"),
        )
        self.service.issue_ticket(
            self.event.event_id,
            Attendee(name="Grace Hopper", email="grace@example.com"),
        )

        with self.assertRaises(ValueError):
            self.service.issue_ticket(
                self.event.event_id,
                Attendee(name="Third Person", email="third@example.com"),
            )

    def test_audit_log_records_decisions(self) -> None:
        _, token = self.service.issue_ticket(
            self.event.event_id,
            Attendee(name="Ada Lovelace", email="ada@example.com"),
        )

        self.service.validate(token, expected_event_id=self.event.event_id)
        self.service.validate(token, expected_event_id=self.event.event_id)

        records = self.service.audit_log.records()
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0].status, ValidationStatus.ACCEPTED)
        self.assertEqual(records[1].status, ValidationStatus.REJECTED_DUPLICATE)


if __name__ == "__main__":
    unittest.main()
