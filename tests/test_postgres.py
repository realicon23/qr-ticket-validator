import os
import threading
import unittest
import psycopg
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from unittest.mock import patch

from qr_ticket_validator.models import Attendee, ValidationStatus
from qr_ticket_validator.postgres import PostgresTicketService
from qr_ticket_validator.signing import decode_token

SECRET = "test-secret-value-32-bytes-long!!"


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "TEST_DATABASE_URL is not set")
class PostgresServiceTest(unittest.TestCase):
    def setUp(self):
        self.service = PostgresTicketService(os.environ["TEST_DATABASE_URL"], SECRET)
        self.service.initialise()
        self.event = self.service.create_event(str(uuid4()), datetime.now(timezone.utc), 1)
        self.attendee = Attendee("Test attendee", "person@example.com")

    def test_persistence_duplicate_and_private_payload(self):
        ticket, token = self.service.issue_ticket(self.event.event_id, self.attendee)
        self.assertEqual(set(decode_token(token)["payload"]), {"version", "ticket_id", "event_id"})
        fresh = PostgresTicketService(os.environ["TEST_DATABASE_URL"], SECRET)
        self.assertTrue(fresh.validate(token, self.event.event_id).accepted)
        self.assertEqual(fresh.validate(token, self.event.event_id).status, ValidationStatus.REJECTED_DUPLICATE)
        with fresh.connect() as conn:
            self.assertEqual(conn.execute("SELECT count(*) AS n FROM checkin_audit WHERE ticket_id = %s", (ticket.ticket_id,)).fetchone()["n"], 2)

    def test_simultaneous_checkins(self):
        _, token = self.service.issue_ticket(self.event.event_id, self.attendee)
        barrier = threading.Barrier(2)
        def scan():
            barrier.wait(timeout=10)
            return self.service.validate(token, self.event.event_id).status
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(lambda _: scan(), range(2)))
        self.assertCountEqual(outcomes, [ValidationStatus.ACCEPTED, ValidationStatus.REJECTED_DUPLICATE])

    def test_simultaneous_issuance_respects_capacity(self):
        barrier = threading.Barrier(2)
        def issue():
            barrier.wait(timeout=10)
            try:
                self.service.issue_ticket(self.event.event_id, self.attendee)
                return "issued"
            except ValueError:
                return "full"
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(lambda _: issue(), range(2)))
        self.assertCountEqual(outcomes, ["issued", "full"])

    def test_wrong_event_expired_and_invalid(self):
        _, token = self.service.issue_ticket(self.event.event_id, self.attendee, timedelta(seconds=1))
        self.assertEqual(self.service.validate(token, "another-event").status, ValidationStatus.REJECTED_INVALID_EVENT)
        self.assertEqual(self.service.validate(token, self.event.event_id, datetime.now(timezone.utc) + timedelta(minutes=1)).status, ValidationStatus.REJECTED_EXPIRED)
        self.assertEqual(self.service.validate("invalid", self.event.event_id).status, ValidationStatus.REJECTED_INVALID_SIGNATURE)

    def test_existing_format_is_not_accepted_without_migration(self):
        from qr_ticket_validator.signing import encode_token
        ticket, _ = self.service.issue_ticket(self.event.event_id, self.attendee)
        old_token = encode_token(ticket.to_payload(), SECRET)
        self.assertEqual(self.service.validate(old_token, self.event.event_id).status, ValidationStatus.REJECTED_INVALID_SIGNATURE)

    def test_expiry_uses_time_after_lock(self):
        ticket, token = self.service.issue_ticket(self.event.event_id, self.attendee)
        before_expiry = ticket.expires_at - timedelta(seconds=1)
        with patch("qr_ticket_validator.postgres.utc_now", side_effect=[before_expiry, ticket.expires_at]):
            result = self.service.validate(token, self.event.event_id)
        self.assertEqual(result.status, ValidationStatus.REJECTED_EXPIRED)

    def test_rejects_short_secret(self):
        with self.assertRaises(ValueError):
            PostgresTicketService(os.environ["TEST_DATABASE_URL"], "short")

    def test_failure_to_write_audit_rolls_back_checkin(self):
        ticket, token = self.service.issue_ticket(self.event.event_id, self.attendee)
        with self.service.connect() as conn:
            conn.execute("ALTER TABLE checkin_audit ADD CONSTRAINT reject_test_audit CHECK (status <> 'accepted') NOT VALID")
        try:
            with self.assertRaises(psycopg.errors.CheckViolation):
                self.service.validate(token, self.event.event_id)
            with self.service.connect() as conn:
                row = conn.execute("SELECT checked_in_at FROM tickets WHERE id = %s", (ticket.ticket_id,)).fetchone()
                self.assertIsNone(row["checked_in_at"])
        finally:
            with self.service.connect() as conn:
                conn.execute("ALTER TABLE checkin_audit DROP CONSTRAINT reject_test_audit")
