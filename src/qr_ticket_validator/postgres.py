"""PostgreSQL storage for ticket issuance and check-in."""

from datetime import datetime, timedelta
import psycopg
from psycopg.rows import dict_row

from .models import Attendee, Event, Ticket, ValidationResult, ValidationStatus, utc_now
from .signing import encode_token, verify_token


class PostgresTicketService:
    """Store events, tickets, and check-in decisions in PostgreSQL."""

    def __init__(self, dsn: str, secret: str) -> None:
        if len(secret.encode("utf-8")) < 32:
            raise ValueError("secret must be at least 32 bytes")
        self.dsn = dsn
        self.secret = secret

    def connect(self):
        return psycopg.connect(self.dsn, row_factory=dict_row)

    def initialise(self) -> None:
        with self.connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS events (
                id text PRIMARY KEY, name text NOT NULL, starts_at timestamptz NOT NULL,
                capacity integer NOT NULL CHECK (capacity > 0))""")
            conn.execute("""CREATE TABLE IF NOT EXISTS tickets (
                id text PRIMARY KEY, event_id text NOT NULL REFERENCES events(id),
                attendee_name text NOT NULL, attendee_email text NOT NULL,
                issued_at timestamptz NOT NULL, expires_at timestamptz NOT NULL,
                checked_in_at timestamptz)""")
            conn.execute("""CREATE TABLE IF NOT EXISTS checkin_audit (
                id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                recorded_at timestamptz NOT NULL, status text NOT NULL,
                ticket_id text, event_id text, reason text NOT NULL)""")

    def create_event(self, name: str, starts_at: datetime, capacity: int) -> Event:
        event = Event(name=name, starts_at=starts_at, capacity=capacity)
        with self.connect() as conn:
            conn.execute("INSERT INTO events VALUES (%s, %s, %s, %s)",
                         (event.event_id, event.name, event.starts_at, event.capacity))
        return event

    def issue_ticket(self, event_id: str, attendee: Attendee,
                     valid_for: timedelta = timedelta(days=30)) -> tuple[Ticket, str]:
        if valid_for <= timedelta(0):
            raise ValueError("valid_for must be positive")
        with self.connect() as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
            event = conn.execute("SELECT capacity FROM events WHERE id = %s FOR UPDATE", (event_id,)).fetchone()
            if event is None:
                raise KeyError(f"unknown event_id: {event_id}")
            count = conn.execute("SELECT count(*) AS count FROM tickets WHERE event_id = %s", (event_id,)).fetchone()["count"]
            if count >= event["capacity"]:
                raise ValueError("event capacity reached")
            issued_at = utc_now()
            ticket = Ticket(event_id=event_id, attendee=attendee, issued_at=issued_at,
                            expires_at=issued_at + valid_for)
            conn.execute("INSERT INTO tickets VALUES (%s, %s, %s, %s, %s, %s, NULL)",
                         (ticket.ticket_id, event_id, attendee.name, attendee.email, issued_at, ticket.expires_at))
            token = encode_token({"version": 1, "ticket_id": ticket.ticket_id, "event_id": event_id}, self.secret)
        return ticket, token

    def validate(self, token: str, expected_event_id: str, now: datetime | None = None) -> ValidationResult:
        if now is not None and (now.tzinfo is None or now.utcoffset() is None):
            raise ValueError("now must be timezone-aware")
        try:
            valid, payload = verify_token(token, self.secret)
        except (TypeError, ValueError, UnicodeError):
            valid, payload = False, None
        payload = payload if isinstance(payload, dict) else {}
        valid = valid and set(payload) == {"version", "ticket_id", "event_id"} and type(payload.get("version")) is int and payload["version"] == 1
        ticket_id = payload.get("ticket_id") if valid and isinstance(payload.get("ticket_id"), str) else None
        event_id = payload.get("event_id") if valid and isinstance(payload.get("event_id"), str) else None
        with self.connect() as conn:
            decision_time = now or utc_now()
            if not valid or not ticket_id or not event_id:
                status, reason = ValidationStatus.REJECTED_INVALID_SIGNATURE, "invalid ticket token"
            elif event_id != expected_event_id:
                status, reason = ValidationStatus.REJECTED_INVALID_EVENT, "ticket belongs to a different event"
            else:
                row = conn.execute("SELECT event_id, expires_at, checked_in_at FROM tickets WHERE id = %s FOR UPDATE", (ticket_id,)).fetchone()
                decision_time = now or utc_now()
                if row is None or row["event_id"] != event_id:
                    status, reason = ValidationStatus.REJECTED_UNKNOWN_TICKET, "ticket is not known for this event"
                elif decision_time >= row["expires_at"]:
                    status, reason = ValidationStatus.REJECTED_EXPIRED, "ticket has expired"
                elif row["checked_in_at"] is not None:
                    status, reason = ValidationStatus.REJECTED_DUPLICATE, "ticket has already been checked in"
                else:
                    conn.execute("UPDATE tickets SET checked_in_at = %s WHERE id = %s", (decision_time, ticket_id))
                    status, reason = ValidationStatus.ACCEPTED, "ticket accepted"
            conn.execute("INSERT INTO checkin_audit (recorded_at, status, ticket_id, event_id, reason) VALUES (%s, %s, %s, %s, %s)",
                         (decision_time, status.value, ticket_id, event_id, reason))
        return ValidationResult(status=status, ticket_id=ticket_id, event_id=event_id, reason=reason)
