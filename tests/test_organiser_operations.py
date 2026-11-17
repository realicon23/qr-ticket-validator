import os
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
from unittest.mock import patch

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from qr_ticket_validator.organiser_service import AccessDenied, OrganiserService
from qr_ticket_validator.ticket_keys import TicketKeys

try:
    from qr_ticket_validator.authenticated_organiser import AuthenticatedOrganiserService
    from qr_ticket_validator.identity import AccessTokenVerifier
    from synthetic_identity_provider import SyntheticIdentityProvider
except ImportError:
    AuthenticatedOrganiserService = None


@unittest.skipUnless(os.getenv("OPERATIONS_TEST_DATABASE_URL"), "OPERATIONS_TEST_DATABASE_URL is not set")
class OrganiserOperationsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.admin_dsn = os.environ["OPERATIONS_TEST_DATABASE_URL"]
        suffix = uuid4().hex[:12]
        cls.owner_role, cls.runtime_role = "ops_owner_" + suffix, "ops_runtime_" + suffix
        cls.org_a, cls.org_b = uuid4(), uuid4()
        cls.owner, cls.manager, cls.staff, cls.other, cls.unassigned = [uuid4() for _ in range(5)]
        script = Path(__file__).resolve().parents[1] / "sql" / "002_organiser_operations.sql"
        with psycopg.connect(cls.admin_dsn) as conn:
            database = conn.execute("SELECT current_database()").fetchone()[0]
            if not database.startswith("ridwan_test_"):
                raise RuntimeError("operations tests require a disposable ridwan_test_* database")
            conn.execute(sql.SQL("CREATE ROLE {} NOLOGIN NOSUPERUSER NOBYPASSRLS").format(sql.Identifier(cls.owner_role)))
            conn.execute(sql.SQL("CREATE ROLE {} LOGIN NOSUPERUSER NOBYPASSRLS").format(sql.Identifier(cls.runtime_role)))
            conn.execute(sql.SQL("GRANT CREATE ON DATABASE {} TO {}").format(sql.Identifier(database), sql.Identifier(cls.owner_role)))
            conn.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(cls.owner_role)))
            conn.execute(script.read_text())
            conn.execute(sql.SQL("GRANT USAGE ON SCHEMA organiser_ops TO {}").format(sql.Identifier(cls.runtime_role)))
            conn.execute(sql.SQL("GRANT EXECUTE ON FUNCTION organiser_ops.actor_role(uuid) TO {}").format(sql.Identifier(cls.runtime_role)))
            conn.execute(sql.SQL("GRANT EXECUTE ON FUNCTION organiser_ops.resolve_user(text,text) TO {}").format(sql.Identifier(cls.runtime_role)))
            conn.execute(sql.SQL("GRANT EXECUTE ON FUNCTION organiser_ops.issue_ticket(uuid,uuid,uuid,text,text), organiser_ops.claim_ticket(uuid,uuid,uuid) TO {}").format(sql.Identifier(cls.runtime_role)))
            conn.execute(sql.SQL("GRANT EXECUTE ON FUNCTION organiser_ops.manage_member(uuid,uuid,text,boolean), organiser_ops.manage_assignment(uuid,uuid,uuid,boolean), organiser_ops.create_event(uuid,uuid,text,timestamptz,timestamptz,integer) TO {}").format(sql.Identifier(cls.runtime_role)))
            conn.execute(sql.SQL("GRANT SELECT ON organiser_ops.memberships, organiser_ops.assignments, organiser_ops.events TO {}").format(sql.Identifier(cls.runtime_role)))
            conn.execute(sql.SQL("GRANT SELECT (id, organisation_id, event_id, attendee_name, expires_at, checked_in_at) ON organiser_ops.tickets TO {}").format(sql.Identifier(cls.runtime_role)))
            conn.execute(sql.SQL("GRANT SELECT ON organiser_ops.audit TO {}").format(sql.Identifier(cls.runtime_role)))
            conn.cursor().executemany("INSERT INTO organiser_ops.organisations VALUES (%s,%s)", [(cls.org_a,"A"),(cls.org_b,"B")])
            conn.cursor().executemany("INSERT INTO organiser_ops.users VALUES (%s,%s,%s)", [(u, "https://synthetic.example", str(u)) for u in (cls.owner, cls.manager, cls.staff, cls.other, cls.unassigned)])
            conn.cursor().executemany("INSERT INTO organiser_ops.memberships VALUES (%s,%s,%s)", [(cls.org_a,cls.owner,"owner"),(cls.org_a,cls.manager,"manager"),(cls.org_a,cls.staff,"staff"),(cls.org_a,cls.unassigned,"staff"),(cls.org_b,cls.other,"owner")])
        cls.addClassCleanup(cls.cleanup)
        cls.dsn = make_conninfo(cls.admin_dsn, user=cls.runtime_role)
        cls.service = OrganiserService(cls.dsn, TicketKeys("test", {"test": "test-secret-value-32-bytes-long!!"}))

    @classmethod
    def cleanup(cls):
        with psycopg.connect(cls.admin_dsn) as conn:
            database = conn.execute("SELECT current_database()").fetchone()[0]
            if not database.startswith("ridwan_test_"):
                raise RuntimeError("refusing cleanup outside a disposable database")
            conn.execute("DROP SCHEMA organiser_ops CASCADE")
            conn.execute(sql.SQL("REVOKE CREATE ON DATABASE {} FROM {}").format(sql.Identifier(database), sql.Identifier(cls.owner_role)))
            conn.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(cls.runtime_role)))
            conn.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(cls.owner_role)))

    def event(self, owner=None, organisation=None, capacity=2):
        now = datetime.now(timezone.utc)
        return self.service.create_event(owner or self.owner, organisation or self.org_a, "Synthetic", now, now + timedelta(hours=2), capacity)

    def synthetic_user(self):
        user = uuid4()
        with psycopg.connect(self.admin_dsn) as conn:
            conn.execute("INSERT INTO organiser_ops.users VALUES (%s,%s,%s)", (user, "https://synthetic.example", str(user)))
        return user

    def authenticated_service(self):
        provider = SyntheticIdentityProvider()
        verifier = AccessTokenVerifier(provider.issuer, provider.audience, provider.public_keys())
        return provider, AuthenticatedOrganiserService(verifier, self.service, {"synthetic-client"}, "ticketing")

    @unittest.skipUnless(AuthenticatedOrganiserService is not None, "identity dependencies are not installed")
    def test_verified_subject_maps_to_member_and_cannot_choose_another_actor(self):
        provider, gateway = self.authenticated_service()
        token = provider.issue(self.owner, actor_id=str(self.other), organisation_id=str(self.org_b))
        now = datetime.now(timezone.utc)
        event = gateway.create_event(token, self.org_a, "Synthetic", now, now + timedelta(hours=2), 2)
        with psycopg.connect(self.admin_dsn) as conn:
            row = conn.execute("SELECT actor_id, organisation_id FROM organiser_ops.audit WHERE event_id = %s", (event,)).fetchone()
        self.assertEqual(row, (self.owner, self.org_a))
        with self.assertRaises(AccessDenied):
            gateway.create_event(token, self.org_b, "Denied", now, now + timedelta(hours=2), 2)

    @unittest.skipUnless(AuthenticatedOrganiserService is not None, "identity dependencies are not installed")
    def test_access_token_scope_client_and_mapping_are_checked(self):
        provider, gateway = self.authenticated_service()
        now = datetime.now(timezone.utc)
        invalid_tokens = [provider.issue(uuid4()), provider.issue(self.owner, iss="https://other.example"),
                          provider.issue(self.owner, aud="another-api"), provider.issue(self.owner, scope="other"),
                          provider.issue(self.owner, client_id="other-client"),
                          provider.issue(self.owner, exp=now - timedelta(seconds=1)), "invalid"]
        for token in invalid_tokens:
            with self.subTest(token=token[:25]), self.assertRaises(AccessDenied):
                gateway.create_event(token, self.org_a, "Denied", now, now + timedelta(hours=2), 1)

    @unittest.skipUnless(AuthenticatedOrganiserService is not None, "identity dependencies are not installed")
    def test_membership_removal_denies_existing_valid_token(self):
        provider, gateway = self.authenticated_service()
        user = self.synthetic_user()
        self.service.add_member(self.manager, self.org_a, user, "staff")
        token = provider.issue(user)
        event = self.event()
        _, ticket_token = self.service.issue_ticket(self.owner, self.org_a, event, "Test", "test@example.com")
        self.service.assign_staff(self.manager, self.org_a, event, user)
        self.service.remove_member(self.manager, self.org_a, user)
        with self.assertRaises(AccessDenied):
            gateway.check_in(token, self.org_a, event, ticket_token)

    @unittest.skipUnless(AuthenticatedOrganiserService is not None, "identity dependencies are not installed")
    def test_verified_staff_scans_only_assigned_event(self):
        provider, gateway = self.authenticated_service()
        token = provider.issue(self.staff)
        event, other_event = self.event(), self.event()
        _, ticket_token = self.service.issue_ticket(self.owner, self.org_a, event, "Synthetic Attendee", "test@example.com")
        _, other_ticket = self.service.issue_ticket(self.owner, self.org_a, other_event, "Other", "test@example.com")
        self.service.assign_staff(self.manager, self.org_a, event, self.staff)
        with self.assertRaises(AccessDenied):
            gateway.check_in(token, self.org_a, other_event, other_ticket)
        self.assertEqual(gateway.check_in(token, self.org_a, event, ticket_token), {"status": "accepted", "attendee_name": "Synthetic Attendee"})
        self.assertEqual(gateway.check_in(token, self.org_a, event, ticket_token)["status"], "duplicate")

    @unittest.skipUnless(AuthenticatedOrganiserService is not None, "identity dependencies are not installed")
    def test_unknown_provider_key_is_denied(self):
        provider, gateway = self.authenticated_service()
        unrelated_provider = SyntheticIdentityProvider()
        now = datetime.now(timezone.utc)
        with self.assertRaises(AccessDenied):
            gateway.create_event(unrelated_provider.issue(self.owner), self.org_a, "Denied", now, now + timedelta(hours=1), 1)

    @unittest.skipUnless(AuthenticatedOrganiserService is not None, "identity dependencies are not installed")
    def test_trusted_key_change_requires_explicit_reconfiguration(self):
        old_provider, old_gateway = self.authenticated_service()
        new_provider = SyntheticIdentityProvider()
        now = datetime.now(timezone.utc)
        with self.assertRaises(AccessDenied):
            old_gateway.create_event(new_provider.issue(self.owner), self.org_a, "Denied", now, now + timedelta(hours=1), 1)
        new_verifier = AccessTokenVerifier(new_provider.issuer, new_provider.audience, new_provider.public_keys())
        new_gateway = AuthenticatedOrganiserService(new_verifier, self.service, {"synthetic-client"}, "ticketing")
        self.assertIsNotNone(new_gateway.create_event(new_provider.issue(self.owner), self.org_a, "Allowed", now, now + timedelta(hours=1), 1))
        with self.assertRaises(AccessDenied):
            new_gateway.create_event(old_provider.issue(self.owner), self.org_a, "Denied", now, now + timedelta(hours=1), 1)

    def test_organiser_isolation_and_permissions(self):
        event = self.event()
        other_event = self.event(self.other, self.org_b)
        ticket, token = self.service.issue_ticket(self.manager, self.org_a, event, "Test", "test@example.com")
        with self.assertRaises(AccessDenied):
            self.service.issue_ticket(self.owner, self.org_a, other_event, "Test", "test@example.com")
        with self.assertRaises(AccessDenied):
            self.service.check_in(self.other, self.org_b, event, token)
        with self.assertRaises(AccessDenied):
            self.service.check_in(self.unassigned, self.org_a, event, token)
        with self.assertRaises(AccessDenied):
            self.event(self.staff, self.org_a)
        self.service.assign_staff(self.manager, self.org_a, event, self.staff)
        self.assertEqual(self.service.check_in(self.staff, self.org_a, event, token), {"status":"accepted", "attendee_name":"Test"})
        self.assertEqual(self.service.check_in(self.staff, self.org_a, event, token)["status"], "duplicate")

    def test_manager_memberships_and_removal(self):
        user = self.synthetic_user()
        self.service.add_member(self.manager, self.org_a, user, "staff")
        event = self.event()
        _, token = self.service.issue_ticket(self.owner, self.org_a, event, "Test", "test@example.com")
        self.service.assign_staff(self.manager, self.org_a, event, user)
        self.service.remove_member(self.manager, self.org_a, user)
        with psycopg.connect(self.admin_dsn) as conn:
            self.assertEqual(conn.execute("SELECT action FROM organiser_ops.audit WHERE subject_user_id = %s ORDER BY recorded_at", (user,)).fetchall(), [("membership_added",), ("staff_assigned",), ("membership_removed",)])
        with self.assertRaises(AccessDenied):
            self.service.check_in(user, self.org_a, event, token)
        with self.assertRaises(AccessDenied):
            self.service.add_member(self.manager, self.org_a, uuid4(), "manager")
        with self.assertRaises(AccessDenied):
            self.service.remove_member(self.manager, self.org_a, self.owner)

    def test_competing_issuance_and_scans(self):
        event = self.event(capacity=1)
        barrier = threading.Barrier(2)
        def issue(_):
            barrier.wait(timeout=10)
            try:
                return self.service.issue_ticket(self.manager, self.org_a, event, "Test", "test@example.com")
            except ValueError:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            issued = list(pool.map(issue, range(2)))
        self.assertEqual(sum(x is not None for x in issued), 1)
        token = next(x[1] for x in issued if x)
        barrier = threading.Barrier(2)
        def scan(_):
            barrier.wait(timeout=10)
            return self.service.check_in(self.owner, self.org_a, event, token)["status"]
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertCountEqual(list(pool.map(scan, range(2))), ["accepted", "duplicate"])

    def test_runtime_role_is_restricted(self):
        with psycopg.connect(self.admin_dsn) as conn:
            self.assertEqual(conn.execute("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = %s", (self.runtime_role,)).fetchone(), (False, False))
            self.assertEqual(conn.execute("SELECT rolsuper, rolcanlogin, rolbypassrls FROM pg_roles WHERE rolname = %s", (self.owner_role,)).fetchone(), (False, False, False))
            self.assertFalse(conn.execute("SELECT pg_has_role(%s, %s, 'MEMBER')", (self.runtime_role, self.owner_role)).fetchone()[0])
            public = conn.execute("""SELECT p.proname FROM pg_proc p
                JOIN pg_namespace n ON n.oid = p.pronamespace
                CROSS JOIN LATERAL aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) a
                WHERE n.nspname = 'organiser_ops' AND p.prosecdef AND a.grantee = 0 AND a.privilege_type = 'EXECUTE'""").fetchall()
            self.assertEqual(public, [])

    def test_direct_writes_to_ticket_and_audit_are_denied(self):
        event = self.event()
        now = datetime.now(timezone.utc)
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            with self.service.transaction(self.owner, self.org_a) as (conn, _):
                conn.execute("INSERT INTO organiser_ops.tickets VALUES (%s,%s,%s,%s,%s,%s,%s,NULL)", (uuid4(), self.org_a, event, "Test", "test@example.com", now, now + timedelta(hours=1)))
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            with self.service.transaction(self.owner, self.org_a) as (conn, _):
                conn.execute("INSERT INTO organiser_ops.audit VALUES (%s,%s,%s,%s,%s,%s,%s,%s)", (uuid4(), self.org_a, self.owner, None, event, None, "test", now))
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            with self.service.transaction(self.owner, self.org_a) as (conn, _):
                conn.execute("DELETE FROM organiser_ops.memberships WHERE organisation_id = %s AND user_id = %s", (self.org_a, self.staff))
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            with self.service.transaction(self.owner, self.org_a) as (conn, _):
                conn.execute("DELETE FROM organiser_ops.assignments WHERE organisation_id = %s", (self.org_a,))
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            with self.service.transaction(self.owner, self.org_a) as (conn, _):
                conn.execute("INSERT INTO organiser_ops.events VALUES (%s,%s,%s,%s,%s,%s)", (uuid4(), self.org_a, "Test", now, now + timedelta(hours=1), 1))

    def test_database_issue_function_enforces_capacity(self):
        event = self.event(capacity=1)
        self.service.issue_ticket(self.owner, self.org_a, event, "Test", "test@example.com")
        with self.assertRaises(psycopg.errors.NoDataFound):
            with self.service.transaction(self.owner, self.org_a) as (conn, _):
                conn.execute("SELECT organiser_ops.issue_ticket(%s,%s,%s,%s,%s)", (self.org_a, event, uuid4(), "Test", "test@example.com"))

    def test_direct_claim_function_does_not_verify_signature(self):
        event = self.event()
        ticket, _ = self.service.issue_ticket(self.owner, self.org_a, event, "Test", "test@example.com")
        with self.service.transaction(self.owner, self.org_a) as (conn, _):
            row = conn.execute("SELECT * FROM organiser_ops.claim_ticket(%s,%s,%s)", (self.org_a, event, ticket)).fetchone()
            self.assertEqual(row["outcome"], "accepted")

    def test_service_accepts_retained_key_and_rejects_revoked_key(self):
        old = "synthetic-old-key-0000000000000000"
        new = "synthetic-new-key-1111111111111111"
        event = self.event(capacity=3)
        old_service = OrganiserService(self.dsn, TicketKeys("old", {"old": old}))
        _, old_token = old_service.issue_ticket(self.owner, self.org_a, event, "Test", "test@example.com")
        _, revoked_token = old_service.issue_ticket(self.owner, self.org_a, event, "Test", "test@example.com")
        rotated = OrganiserService(self.dsn, TicketKeys("new", {"old": old, "new": new}))
        self.assertEqual(rotated.check_in(self.owner, self.org_a, event, old_token)["status"], "accepted")
        revoked = OrganiserService(self.dsn, TicketKeys("new", {"new": new}))
        with self.assertRaises(AccessDenied):
            revoked.check_in(self.owner, self.org_a, event, revoked_token)

    def test_revocation_affects_next_statement(self):
        user = self.synthetic_user()
        self.service.add_member(self.manager, self.org_a, user, "staff")
        event = self.event()
        self.service.assign_staff(self.manager, self.org_a, event, user)
        with self.service.transaction(user, self.org_a) as (conn, _):
            self.assertIsNotNone(conn.execute("SELECT id FROM organiser_ops.events WHERE id = %s", (event,)).fetchone())
            self.service.remove_member(self.manager, self.org_a, user)
            self.assertIsNone(conn.execute("SELECT id FROM organiser_ops.events WHERE id = %s", (event,)).fetchone())

    def test_audit_failure_rolls_back_checkin(self):
        event = self.event()
        ticket, token = self.service.issue_ticket(self.owner, self.org_a, event, "Test", "test@example.com")
        with psycopg.connect(self.admin_dsn) as conn:
            conn.execute("ALTER TABLE organiser_ops.audit ADD CONSTRAINT reject_checkin_test CHECK (action <> 'checkin_accepted') NOT VALID")
        try:
            with self.assertRaises(psycopg.errors.CheckViolation):
                self.service.check_in(self.owner, self.org_a, event, token)
            with psycopg.connect(self.admin_dsn) as conn:
                self.assertIsNone(conn.execute("SELECT checked_in_at FROM organiser_ops.tickets WHERE id = %s", (ticket,)).fetchone()[0])
        finally:
            with psycopg.connect(self.admin_dsn) as conn:
                conn.execute("ALTER TABLE organiser_ops.audit DROP CONSTRAINT reject_checkin_test")

    def test_checked_in_ticket_cannot_be_reset(self):
        event = self.event()
        ticket, token = self.service.issue_ticket(self.owner, self.org_a, event, "Test", "test@example.com")
        self.service.check_in(self.owner, self.org_a, event, token)
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            with self.service.transaction(self.owner, self.org_a) as (conn, _):
                conn.execute("UPDATE organiser_ops.tickets SET checked_in_at = NULL WHERE id = %s", (ticket,))

    def test_staff_cannot_read_email_or_unassign_others(self):
        event = self.event()
        self.service.assign_staff(self.manager, self.org_a, event, self.staff)
        self.service.issue_ticket(self.owner, self.org_a, event, "Test", "test@example.com")
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            with self.service.transaction(self.staff, self.org_a) as (conn, _):
                conn.execute("SELECT attendee_email FROM organiser_ops.tickets")
        with self.assertRaises(AccessDenied):
            self.service.unassign_staff(self.staff, self.org_a, event, self.staff)
        self.service.unassign_staff(self.manager, self.org_a, event, self.staff)

    def test_cross_organiser_ticket_update_is_filtered(self):
        event = self.event()
        ticket, _ = self.service.issue_ticket(self.owner, self.org_a, event, "Test", "test@example.com")
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            with self.service.transaction(self.other, self.org_b) as (conn, _):
                conn.execute("UPDATE organiser_ops.tickets SET checked_in_at = %s WHERE id = %s RETURNING id", (datetime.now(timezone.utc), ticket))
        with psycopg.connect(self.admin_dsn) as conn:
            self.assertIsNone(conn.execute("SELECT checked_in_at FROM organiser_ops.tickets WHERE id = %s", (ticket,)).fetchone()[0])

    def test_revoked_assignment_during_checkin_never_reports_accepted(self):
        event = self.event()
        ticket, token = self.service.issue_ticket(self.owner, self.org_a, event, "Test", "test@example.com")
        self.service.assign_staff(self.manager, self.org_a, event, self.staff)
        claim = self.service._claim_ticket
        def revoke_then_claim(conn, organisation, event_id, ticket_id):
            self.service.unassign_staff(self.manager, self.org_a, event, self.staff)
            return claim(conn, organisation, event_id, ticket_id)
        with patch.object(self.service, "_claim_ticket", side_effect=revoke_then_claim):
            with self.assertRaises(AccessDenied):
                self.service.check_in(self.staff, self.org_a, event, token)
        with psycopg.connect(self.admin_dsn) as conn:
            self.assertIsNone(conn.execute("SELECT checked_in_at FROM organiser_ops.tickets WHERE id = %s", (ticket,)).fetchone()[0])
            self.assertEqual(conn.execute("SELECT count(*) FROM organiser_ops.audit WHERE ticket_id = %s AND action = 'checkin_accepted'", (ticket,)).fetchone()[0], 0)

    def test_inflight_checkin_finishes_before_assignment_removal(self):
        event = self.event()
        ticket, token = self.service.issue_ticket(self.owner, self.org_a, event, "Test", "test@example.com")
        self.service.assign_staff(self.manager, self.org_a, event, self.staff)
        limited = OrganiserService(make_conninfo(self.dsn, options="-c statement_timeout=5000"), self.service.keys)
        def wait_for_lock(fragment):
            deadline = time.monotonic() + 3
            with psycopg.connect(self.admin_dsn, autocommit=True) as observer:
                while time.monotonic() < deadline:
                    waiting = observer.execute("SELECT count(*) FROM pg_stat_activity WHERE usename = %s AND wait_event_type = 'Lock' AND query LIKE %s", (self.runtime_role, "%" + fragment + "%")).fetchone()[0]
                    if waiting:
                        return
                    time.sleep(0.02)
            self.fail("expected database lock wait was not observed")
        with psycopg.connect(self.admin_dsn) as locker:
            locker.execute("SELECT id FROM organiser_ops.tickets WHERE id = %s FOR UPDATE", (ticket,))
            with ThreadPoolExecutor(max_workers=2) as pool:
                scan = pool.submit(limited.check_in, self.staff, self.org_a, event, token)
                try:
                    wait_for_lock("claim_ticket")
                    removal = pool.submit(limited.unassign_staff, self.manager, self.org_a, event, self.staff)
                    wait_for_lock("manage_assignment")
                finally:
                    locker.commit()
                self.assertEqual(scan.result(timeout=6)["status"], "accepted")
                removal.result(timeout=6)
        with self.assertRaises(AccessDenied):
            self.service.check_in(self.staff, self.org_a, event, token)

    def test_ticket_expires_at_event_end_even_when_far_in_future(self):
        start = datetime.now(timezone.utc) + timedelta(days=40)
        end = start + timedelta(hours=2)
        event = self.service.create_event(self.owner, self.org_a, "Future", start, end, 2)
        ticket, token = self.service.issue_ticket(self.owner, self.org_a, event, "Test", "test@example.com")
        with psycopg.connect(self.admin_dsn) as conn:
            self.assertEqual(conn.execute("SELECT expires_at FROM organiser_ops.tickets WHERE id = %s", (ticket,)).fetchone()[0], end)
        with psycopg.connect(self.admin_dsn) as conn:
            conn.execute("UPDATE organiser_ops.tickets SET expires_at = clock_timestamp() - interval '1 second' WHERE id = %s", (ticket,))
        self.assertEqual(self.service.check_in(self.owner, self.org_a, event, token)["status"], "expired")

    def test_event_times_and_unknown_user_are_rejected(self):
        now = datetime.now(timezone.utc)
        with self.assertRaises(ValueError):
            self.service.create_event(self.owner, self.org_a, "Invalid", now, now, 1)
        event = self.service.create_event(self.owner, self.org_a, "Past", now - timedelta(hours=2), now - timedelta(hours=1), 1)
        with self.assertRaises(ValueError):
            self.service.issue_ticket(self.owner, self.org_a, event, "Test", "test@example.com")
        with self.assertRaises(psycopg.errors.ForeignKeyViolation):
            self.service.add_member(self.owner, self.org_a, uuid4(), "staff")
