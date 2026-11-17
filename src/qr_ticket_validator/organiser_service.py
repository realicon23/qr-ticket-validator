"""Organiser-scoped operations for a trusted local caller."""

from contextlib import contextmanager
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row

from .ticket_keys import TicketKeys


class AccessDenied(Exception):
    """The requested resource or operation is unavailable."""


class OrganiserService:
    """Local experiment; caller identity is not authenticated by this class."""

    def __init__(self, dsn: str, keys: TicketKeys):
        self.dsn, self.keys = dsn, keys

    @contextmanager
    def transaction(self, actor: UUID, organisation: UUID):
        with psycopg.connect(self.dsn, row_factory=dict_row) as conn:
            conn.execute("SELECT set_config('app.actor_id', %s, true), set_config('app.organisation_id', %s, true)", (str(actor), str(organisation)))
            role = conn.execute("SELECT organiser_ops.actor_role(%s) AS role", (organisation,)).fetchone()["role"]
            if role is None:
                raise AccessDenied("operation unavailable")
            yield conn, role

    @staticmethod
    def _invoke(conn, statement, values):
        try:
            return conn.execute(statement, values)
        except psycopg.errors.RaiseException:
            raise AccessDenied("operation unavailable") from None

    def create_event(self, actor, organisation, name, starts_at, ends_at, capacity):
        if not name or capacity <= 0 or starts_at.tzinfo is None or starts_at.utcoffset() is None or ends_at.tzinfo is None or ends_at.utcoffset() is None or ends_at <= starts_at:
            raise ValueError("invalid event")
        with self.transaction(actor, organisation) as (conn, role):
            if role not in ("owner", "manager"):
                raise AccessDenied("operation unavailable")
            event = uuid4()
            self._invoke(conn, "SELECT organiser_ops.create_event(%s,%s,%s,%s,%s,%s)", (organisation, event, name, starts_at, ends_at, capacity))
        return event

    def add_member(self, actor, organisation, user, new_role):
        with self.transaction(actor, organisation) as (conn, role):
            if new_role not in ("manager", "staff") or not (role == "owner" or (role == "manager" and new_role == "staff")):
                raise AccessDenied("operation unavailable")
            self._invoke(conn, "SELECT organiser_ops.manage_member(%s,%s,%s,%s)", (organisation, user, new_role, True))

    def remove_member(self, actor, organisation, user):
        with self.transaction(actor, organisation) as (conn, role):
            if role not in ("owner", "manager"):
                raise AccessDenied("operation unavailable")
            self._invoke(conn, "SELECT organiser_ops.manage_member(%s,%s,%s,%s)", (organisation, user, None, False))

    def assign_staff(self, actor, organisation, event, user):
        with self.transaction(actor, organisation) as (conn, role):
            if role not in ("owner", "manager"):
                raise AccessDenied("operation unavailable")
            self._invoke(conn, "SELECT organiser_ops.manage_assignment(%s,%s,%s,%s)", (organisation, event, user, True))

    def unassign_staff(self, actor, organisation, event, user):
        with self.transaction(actor, organisation) as (conn, role):
            if role not in ("owner", "manager"):
                raise AccessDenied("operation unavailable")
            self._invoke(conn, "SELECT organiser_ops.manage_assignment(%s,%s,%s,%s)", (organisation, event, user, False))

    def issue_ticket(self, actor, organisation, event, attendee_name, attendee_email):
        with self.transaction(actor, organisation) as (conn, role):
            if role not in ("owner", "manager"):
                raise AccessDenied("operation unavailable")
            ticket = uuid4()
            try:
                conn.execute("SELECT organiser_ops.issue_ticket(%s,%s,%s,%s,%s)", (organisation, event, ticket, attendee_name, attendee_email))
            except psycopg.Error as exc:
                if exc.sqlstate in ("P0002", "P0003"):
                    raise ValueError("ticket unavailable") from None
                if exc.sqlstate == "P0001":
                    raise AccessDenied("operation unavailable") from None
                raise
            token = self.keys.issue(str(organisation), str(event), str(ticket))
        return ticket, token

    def check_in(self, actor, organisation, event, token):
        valid, payload = self.keys.verify(token)
        if not valid or not isinstance(payload, dict) or set(payload) != {"version", "key_id", "organisation_id", "event_id", "ticket_id"} or type(payload["version"]) is not int or payload["version"] != 3 or payload["organisation_id"] != str(organisation) or payload["event_id"] != str(event):
            raise AccessDenied("operation unavailable")
        try:
            ticket = UUID(payload["ticket_id"])
        except (ValueError, TypeError, AttributeError):
            raise AccessDenied("operation unavailable") from None
        with self.transaction(actor, organisation) as (conn, role):
            try:
                row = self._claim_ticket(conn, organisation, event, ticket)
            except psycopg.errors.RaiseException:
                raise AccessDenied("operation unavailable")
            return {"status": row["outcome"], "attendee_name": row["attendee_name"]}

    @staticmethod
    def _claim_ticket(conn, organisation, event, ticket):
        return conn.execute("SELECT * FROM organiser_ops.claim_ticket(%s,%s,%s)", (organisation, event, ticket)).fetchone()
