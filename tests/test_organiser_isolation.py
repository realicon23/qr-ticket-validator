import os
import unittest
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql


@unittest.skipUnless(os.getenv("ORGANISER_TEST_DATABASE_URL"), "ORGANISER_TEST_DATABASE_URL is not set")
class OrganiserIsolationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dsn = os.environ["ORGANISER_TEST_DATABASE_URL"]
        cls.role = "stage_reader_" + uuid4().hex[:12]
        cls.org_a, cls.org_b = uuid4(), uuid4()
        cls.owner, cls.staff, cls.other_owner, cls.removed = uuid4(), uuid4(), uuid4(), uuid4()
        cls.event_a, cls.event_b, cls.event_unassigned = uuid4(), uuid4(), uuid4()
        cls.ticket_a, cls.ticket_b = uuid4(), uuid4()
        migration = Path(__file__).resolve().parents[1] / "sql" / "001_organiser_isolation.sql"
        with psycopg.connect(cls.dsn) as conn:
            database = conn.execute("SELECT current_database()").fetchone()[0]
            if not database.startswith("ridwan_test_"):
                raise RuntimeError("organiser tests require a disposable database named ridwan_test_*")
            conn.execute(migration.read_text())
            conn.execute(sql.SQL("CREATE ROLE {} NOLOGIN NOSUPERUSER NOBYPASSRLS").format(sql.Identifier(cls.role)))
            conn.execute(sql.SQL("GRANT USAGE ON SCHEMA organiser_stage TO {}").format(sql.Identifier(cls.role)))
            for table in ("organisers", "memberships", "events", "assignments", "audit"):
                conn.execute(sql.SQL("GRANT SELECT ON organiser_stage.{} TO {}").format(sql.Identifier(table), sql.Identifier(cls.role)))
            conn.execute(sql.SQL("GRANT SELECT (id, organiser_id, event_id, attendee_name) ON organiser_stage.tickets TO {}").format(sql.Identifier(cls.role)))
            conn.cursor().executemany("INSERT INTO organiser_stage.organisers VALUES (%s, %s)", [(cls.org_a, "Synthetic A"), (cls.org_b, "Synthetic B")])
            conn.cursor().executemany("INSERT INTO organiser_stage.memberships VALUES (%s, %s, %s)", [(cls.org_a, cls.owner, "owner"), (cls.org_a, cls.staff, "staff"), (cls.org_b, cls.other_owner, "owner")])
            conn.cursor().executemany("INSERT INTO organiser_stage.events VALUES (%s, %s, %s)", [(cls.event_a, cls.org_a, "Event A"), (cls.event_unassigned, cls.org_a, "Unassigned"), (cls.event_b, cls.org_b, "Event B")])
            conn.execute("INSERT INTO organiser_stage.assignments VALUES (%s, %s, %s)", (cls.org_a, cls.event_a, cls.staff))
            conn.cursor().executemany("INSERT INTO organiser_stage.tickets VALUES (%s, %s, %s, %s, %s)", [(cls.ticket_a, cls.org_a, cls.event_a, "Test A", "a@example.com"), (cls.ticket_b, cls.org_b, cls.event_b, "Test B", "b@example.com")])
            conn.cursor().executemany("INSERT INTO organiser_stage.audit VALUES (%s, %s, %s)", [(uuid4(), cls.org_a, "test"), (uuid4(), cls.org_b, "test")])
        cls.addClassCleanup(cls.cleanup_test_resources)

    @classmethod
    def cleanup_test_resources(cls):
        with psycopg.connect(cls.dsn) as conn:
            if not conn.execute("SELECT current_database()").fetchone()[0].startswith("ridwan_test_"):
                raise RuntimeError("refusing to clean resources outside a ridwan_test_* database")
            conn.execute("DROP SCHEMA organiser_stage CASCADE")
            conn.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(cls.role)))

    def scoped(self, conn, user, organisation):
        conn.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(self.role)))
        conn.execute("SELECT set_config('app.actor_id', %s, true), set_config('app.organiser_id', %s, true)", (str(user), str(organisation)))

    def test_owner_cannot_read_other_organiser(self):
        with psycopg.connect(self.dsn) as conn:
            self.scoped(conn, self.owner, self.org_a)
            self.assertEqual({row[0] for row in conn.execute("SELECT id FROM organiser_stage.events")}, {self.event_a, self.event_unassigned})
            self.assertEqual(conn.execute("SELECT id FROM organiser_stage.tickets WHERE id = %s", (self.ticket_b,)).fetchall(), [])
            self.assertEqual(len(conn.execute("SELECT id FROM organiser_stage.audit").fetchall()), 1)

    def test_staff_sees_assigned_events_and_name_only(self):
        with psycopg.connect(self.dsn) as conn:
            self.scoped(conn, self.staff, self.org_a)
            self.assertEqual(conn.execute("SELECT id FROM organiser_stage.events").fetchall(), [(self.event_a,)])
            self.assertEqual(conn.execute("SELECT attendee_name FROM organiser_stage.tickets").fetchall(), [("Test A",)])
            self.assertEqual(conn.execute("SELECT id FROM organiser_stage.audit").fetchall(), [])
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                conn.execute("SELECT attendee_email FROM organiser_stage.tickets")

    def test_missing_or_removed_membership_denied(self):
        with psycopg.connect(self.dsn) as conn:
            conn.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(self.role)))
            self.assertEqual(conn.execute("SELECT id FROM organiser_stage.events").fetchall(), [])
        with psycopg.connect(self.dsn) as conn:
            self.scoped(conn, self.removed, self.org_a)
            self.assertEqual(conn.execute("SELECT id FROM organiser_stage.events").fetchall(), [])
        with psycopg.connect(self.dsn) as conn:
            self.scoped(conn, self.owner, self.org_b)
            self.assertEqual(conn.execute("SELECT id FROM organiser_stage.events").fetchall(), [])

    def test_reader_cannot_write(self):
        with psycopg.connect(self.dsn) as conn:
            self.scoped(conn, self.owner, self.org_a)
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                conn.execute("INSERT INTO organiser_stage.events VALUES (%s, %s, %s)", (uuid4(), self.org_a, "Denied"))

    def test_removing_membership_takes_effect(self):
        user = uuid4()
        with psycopg.connect(self.dsn) as conn:
            conn.execute("INSERT INTO organiser_stage.memberships VALUES (%s, %s, %s)", (self.org_a, user, "manager"))
        with psycopg.connect(self.dsn) as conn:
            self.scoped(conn, user, self.org_a)
            self.assertTrue(conn.execute("SELECT id FROM organiser_stage.events").fetchall())
        with psycopg.connect(self.dsn) as conn:
            conn.execute("DELETE FROM organiser_stage.memberships WHERE organiser_id = %s AND user_id = %s", (self.org_a, user))
        with psycopg.connect(self.dsn) as conn:
            self.scoped(conn, user, self.org_a)
            self.assertEqual(conn.execute("SELECT id FROM organiser_stage.events").fetchall(), [])

    def test_removing_assigned_staff_removes_assignment(self):
        user = uuid4()
        with psycopg.connect(self.dsn) as conn:
            conn.execute("INSERT INTO organiser_stage.memberships VALUES (%s, %s, %s)", (self.org_a, user, "staff"))
            conn.execute("INSERT INTO organiser_stage.assignments VALUES (%s, %s, %s)", (self.org_a, self.event_a, user))
        with psycopg.connect(self.dsn) as conn:
            self.scoped(conn, user, self.org_a)
            self.assertEqual(conn.execute("SELECT id FROM organiser_stage.events").fetchall(), [(self.event_a,)])
        with psycopg.connect(self.dsn) as conn:
            conn.execute("DELETE FROM organiser_stage.memberships WHERE organiser_id = %s AND user_id = %s", (self.org_a, user))
            self.assertEqual(conn.execute("SELECT count(*) FROM organiser_stage.assignments WHERE user_id = %s", (user,)).fetchone()[0], 0)
        with psycopg.connect(self.dsn) as conn:
            self.scoped(conn, user, self.org_a)
            self.assertEqual(conn.execute("SELECT id FROM organiser_stage.events").fetchall(), [])

    def test_runtime_role_cannot_bypass_rls(self):
        with psycopg.connect(self.dsn) as conn:
            row = conn.execute("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = %s", (self.role,)).fetchone()
            self.assertEqual(row, (False, False))
            owner = conn.execute("SELECT pg_get_userbyid(c.relowner) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'organiser_stage' AND c.relname = 'events'").fetchone()[0]
            self.assertNotEqual(owner, self.role)

    def test_transaction_context_is_cleared(self):
        with psycopg.connect(self.dsn) as conn:
            self.scoped(conn, self.owner, self.org_a)
            self.assertTrue(conn.execute("SELECT id FROM organiser_stage.events").fetchall())
            conn.commit()
            conn.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(self.role)))
            self.assertEqual(conn.execute("SELECT id FROM organiser_stage.events").fetchall(), [])

    def test_direct_sql_can_impersonate_another_actor(self):
        # Demonstrates why the test role must never be exposed to untrusted clients.
        with psycopg.connect(self.dsn) as conn:
            self.scoped(conn, self.owner, self.org_a)
            conn.execute("SELECT set_config('app.actor_id', %s, true), set_config('app.organiser_id', %s, true)", (str(self.other_owner), str(self.org_b)))
            self.assertEqual(conn.execute("SELECT id FROM organiser_stage.events").fetchall(), [(self.event_b,)])
