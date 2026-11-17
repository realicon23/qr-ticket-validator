-- Local experiment: apply as a dedicated schema owner in a disposable database.
CREATE SCHEMA organiser_ops;
CREATE TABLE organiser_ops.organisations (id uuid PRIMARY KEY, name text NOT NULL);
CREATE TABLE organiser_ops.users (
    id uuid PRIMARY KEY, issuer text NOT NULL, subject text NOT NULL,
    UNIQUE (issuer, subject)
);
CREATE TABLE organiser_ops.memberships (
    organisation_id uuid NOT NULL REFERENCES organiser_ops.organisations(id),
    user_id uuid NOT NULL REFERENCES organiser_ops.users(id), role text NOT NULL CHECK (role IN ('owner','manager','staff')),
    PRIMARY KEY (organisation_id, user_id)
);
CREATE TABLE organiser_ops.events (
    id uuid PRIMARY KEY, organisation_id uuid NOT NULL REFERENCES organiser_ops.organisations(id),
    name text NOT NULL, starts_at timestamptz NOT NULL, ends_at timestamptz NOT NULL,
    capacity integer NOT NULL CHECK (capacity > 0), CHECK (ends_at > starts_at),
    UNIQUE (organisation_id, id)
);
CREATE TABLE organiser_ops.assignments (
    organisation_id uuid NOT NULL, event_id uuid NOT NULL, user_id uuid NOT NULL,
    PRIMARY KEY (organisation_id, event_id, user_id),
    FOREIGN KEY (organisation_id, event_id) REFERENCES organiser_ops.events(organisation_id, id),
    FOREIGN KEY (organisation_id, user_id) REFERENCES organiser_ops.memberships(organisation_id, user_id) ON DELETE CASCADE
);
CREATE TABLE organiser_ops.tickets (
    id uuid PRIMARY KEY, organisation_id uuid NOT NULL, event_id uuid NOT NULL,
    attendee_name text NOT NULL, attendee_email text NOT NULL,
    issued_at timestamptz NOT NULL, expires_at timestamptz NOT NULL, checked_in_at timestamptz,
    FOREIGN KEY (organisation_id, event_id) REFERENCES organiser_ops.events(organisation_id, id)
);
CREATE TABLE organiser_ops.audit (
    id uuid PRIMARY KEY, organisation_id uuid NOT NULL REFERENCES organiser_ops.organisations(id),
    actor_id uuid NOT NULL, subject_user_id uuid, event_id uuid, ticket_id uuid, action text NOT NULL, recorded_at timestamptz NOT NULL
);

CREATE FUNCTION organiser_ops.prevent_checkin_reset() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
BEGIN
    IF OLD.checked_in_at IS NOT NULL OR NEW.checked_in_at IS NULL THEN
        RAISE EXCEPTION 'check-in state cannot be reset';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER checkin_once BEFORE UPDATE OF checked_in_at ON organiser_ops.tickets
FOR EACH ROW EXECUTE FUNCTION organiser_ops.prevent_checkin_reset();

-- The owner must be a dedicated non-login, non-superuser role. See architecture notes.
CREATE FUNCTION organiser_ops.actor_role(org uuid) RETURNS text
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, organiser_ops, pg_temp AS $$
    SELECT m.role FROM organiser_ops.memberships m
    WHERE m.organisation_id = org AND org::text = current_setting('app.organisation_id', true)
      AND m.user_id::text = current_setting('app.actor_id', true)
$$;
REVOKE ALL ON FUNCTION organiser_ops.actor_role(uuid) FROM PUBLIC;

-- Trusted callers verify the access token's issuer and subject before calling this function.
CREATE FUNCTION organiser_ops.resolve_user(p_issuer text, p_subject text) RETURNS uuid
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, organiser_ops, pg_temp AS $$
    SELECT u.id FROM organiser_ops.users u WHERE u.issuer = p_issuer AND u.subject = p_subject
$$;
REVOKE ALL ON FUNCTION organiser_ops.resolve_user(text, text) FROM PUBLIC;

CREATE FUNCTION organiser_ops.issue_ticket(p_org uuid, p_event uuid, p_ticket uuid, p_name text, p_email text)
RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, organiser_ops, pg_temp AS $$
DECLARE
    event_capacity integer;
    event_end timestamptz;
    issued timestamptz;
    member_role text;
BEGIN
    SELECT role INTO member_role FROM organiser_ops.memberships
      WHERE organisation_id = p_org AND p_org::text = current_setting('app.organisation_id', true)
      AND user_id::text = current_setting('app.actor_id', true) FOR SHARE;
    IF member_role IS NULL OR member_role NOT IN ('owner', 'manager') THEN
        RAISE EXCEPTION 'operation unavailable';
    END IF;
    SELECT capacity, ends_at INTO event_capacity, event_end FROM organiser_ops.events
      WHERE organisation_id = p_org AND id = p_event FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'operation unavailable'; END IF;
    IF (SELECT count(*) FROM organiser_ops.tickets WHERE organisation_id = p_org AND event_id = p_event) >= event_capacity THEN
        RAISE EXCEPTION 'event capacity reached' USING ERRCODE = 'P0002';
    END IF;
    issued := clock_timestamp();
    IF issued >= event_end THEN RAISE EXCEPTION 'event has ended' USING ERRCODE = 'P0003'; END IF;
    INSERT INTO organiser_ops.tickets VALUES (p_ticket, p_org, p_event, p_name, p_email, issued, event_end, NULL);
    INSERT INTO organiser_ops.audit VALUES (gen_random_uuid(), p_org, current_setting('app.actor_id')::uuid, NULL,
        p_event, p_ticket, 'ticket_issued', issued);
    RETURN p_ticket;
END
$$;
REVOKE ALL ON FUNCTION organiser_ops.issue_ticket(uuid, uuid, uuid, text, text) FROM PUBLIC;

CREATE FUNCTION organiser_ops.claim_ticket(p_org uuid, p_event uuid, p_ticket uuid)
RETURNS TABLE (outcome text, attendee_name text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, organiser_ops, pg_temp AS $$
DECLARE
    member_role text;
    ticket_record organiser_ops.tickets%ROWTYPE;
    decided timestamptz;
    result text;
BEGIN
    SELECT role INTO member_role FROM organiser_ops.memberships
      WHERE organisation_id = p_org AND p_org::text = current_setting('app.organisation_id', true)
      AND user_id::text = current_setting('app.actor_id', true) FOR SHARE;
    IF member_role IS NULL THEN
        RAISE EXCEPTION 'operation unavailable';
    END IF;
    IF member_role = 'staff' THEN
        PERFORM 1 FROM organiser_ops.assignments a WHERE a.organisation_id = p_org AND a.event_id = p_event
          AND a.user_id::text = current_setting('app.actor_id', true) FOR SHARE;
        IF NOT FOUND THEN RAISE EXCEPTION 'operation unavailable'; END IF;
    END IF;
    SELECT * INTO ticket_record FROM organiser_ops.tickets t
      WHERE t.organisation_id = p_org AND t.event_id = p_event AND t.id = p_ticket FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'operation unavailable'; END IF;
    decided := clock_timestamp();
    IF ticket_record.checked_in_at IS NOT NULL THEN
        result := 'duplicate';
    ELSIF decided >= ticket_record.expires_at THEN
        result := 'expired';
    ELSE
        UPDATE organiser_ops.tickets SET checked_in_at = decided WHERE id = p_ticket AND organisation_id = p_org;
        result := 'accepted';
    END IF;
    INSERT INTO organiser_ops.audit VALUES (gen_random_uuid(), p_org, current_setting('app.actor_id')::uuid,
        NULL, p_event, p_ticket, 'checkin_' || result, decided);
    RETURN QUERY SELECT result, ticket_record.attendee_name;
END
$$;
REVOKE ALL ON FUNCTION organiser_ops.claim_ticket(uuid, uuid, uuid) FROM PUBLIC;

CREATE FUNCTION organiser_ops.manage_member(p_org uuid, p_user uuid, p_role text, p_add boolean)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, organiser_ops, pg_temp AS $$
DECLARE actor_role text; target_role text; actor uuid;
BEGIN
    actor := current_setting('app.actor_id', true)::uuid;
    SELECT role INTO actor_role FROM organiser_ops.memberships
      WHERE organisation_id = p_org AND p_org::text = current_setting('app.organisation_id', true)
        AND user_id = actor FOR SHARE;
    IF actor_role NOT IN ('owner','manager') OR actor_role IS NULL THEN RAISE EXCEPTION 'operation unavailable'; END IF;
    IF p_add THEN
        target_role := p_role;
        IF target_role NOT IN ('manager','staff') OR target_role IS NULL OR (actor_role = 'manager' AND target_role <> 'staff') THEN
            RAISE EXCEPTION 'operation unavailable';
        END IF;
        INSERT INTO organiser_ops.memberships VALUES (p_org, p_user, target_role);
    ELSE
        DELETE FROM organiser_ops.memberships WHERE organisation_id = p_org AND user_id = p_user
          AND (role = 'staff' OR (actor_role = 'owner' AND role = 'manager')) RETURNING role INTO target_role;
        IF NOT FOUND THEN RAISE EXCEPTION 'operation unavailable'; END IF;
    END IF;
    INSERT INTO organiser_ops.audit VALUES (gen_random_uuid(), p_org, actor, p_user, NULL, NULL,
        CASE WHEN p_add THEN 'membership_added' ELSE 'membership_removed' END, clock_timestamp());
END
$$;
REVOKE ALL ON FUNCTION organiser_ops.manage_member(uuid, uuid, text, boolean) FROM PUBLIC;

CREATE FUNCTION organiser_ops.manage_assignment(p_org uuid, p_event uuid, p_user uuid, p_add boolean)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, organiser_ops, pg_temp AS $$
DECLARE actor_role text; actor uuid;
BEGIN
    actor := current_setting('app.actor_id', true)::uuid;
    SELECT role INTO actor_role FROM organiser_ops.memberships
      WHERE organisation_id = p_org AND p_org::text = current_setting('app.organisation_id', true)
        AND user_id = actor FOR SHARE;
    IF actor_role NOT IN ('owner','manager') OR actor_role IS NULL THEN RAISE EXCEPTION 'operation unavailable'; END IF;
    IF p_add THEN
        PERFORM 1 FROM organiser_ops.memberships WHERE organisation_id = p_org AND user_id = p_user AND role = 'staff' FOR SHARE;
        IF NOT FOUND THEN RAISE EXCEPTION 'operation unavailable'; END IF;
        INSERT INTO organiser_ops.assignments VALUES (p_org, p_event, p_user);
    ELSE
        DELETE FROM organiser_ops.assignments WHERE organisation_id = p_org AND event_id = p_event AND user_id = p_user;
        IF NOT FOUND THEN RAISE EXCEPTION 'operation unavailable'; END IF;
    END IF;
    INSERT INTO organiser_ops.audit VALUES (gen_random_uuid(), p_org, actor, p_user, p_event, NULL,
        CASE WHEN p_add THEN 'staff_assigned' ELSE 'staff_unassigned' END, clock_timestamp());
END
$$;
REVOKE ALL ON FUNCTION organiser_ops.manage_assignment(uuid, uuid, uuid, boolean) FROM PUBLIC;

CREATE FUNCTION organiser_ops.create_event(p_org uuid, p_event uuid, p_name text, p_start timestamptz, p_end timestamptz, p_capacity integer)
RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, organiser_ops, pg_temp AS $$
DECLARE actor_role text; actor uuid;
BEGIN
    actor := current_setting('app.actor_id', true)::uuid;
    SELECT role INTO actor_role FROM organiser_ops.memberships
      WHERE organisation_id = p_org AND p_org::text = current_setting('app.organisation_id', true)
        AND user_id = actor FOR SHARE;
    IF actor_role NOT IN ('owner','manager') OR actor_role IS NULL THEN RAISE EXCEPTION 'operation unavailable'; END IF;
    INSERT INTO organiser_ops.events VALUES (p_event, p_org, p_name, p_start, p_end, p_capacity);
    INSERT INTO organiser_ops.audit VALUES (gen_random_uuid(), p_org, actor, NULL, p_event, NULL, 'event_created', clock_timestamp());
    RETURN p_event;
END
$$;
REVOKE ALL ON FUNCTION organiser_ops.create_event(uuid, uuid, text, timestamptz, timestamptz, integer) FROM PUBLIC;

ALTER TABLE organiser_ops.memberships ENABLE ROW LEVEL SECURITY;
ALTER TABLE organiser_ops.events ENABLE ROW LEVEL SECURITY;
ALTER TABLE organiser_ops.assignments ENABLE ROW LEVEL SECURITY;
ALTER TABLE organiser_ops.tickets ENABLE ROW LEVEL SECURITY;
ALTER TABLE organiser_ops.audit ENABLE ROW LEVEL SECURITY;

CREATE POLICY memberships_select ON organiser_ops.memberships FOR SELECT USING (
    organiser_ops.actor_role(organisation_id) = 'owner'
    OR (organiser_ops.actor_role(organisation_id) = 'manager' AND role = 'staff')
    OR (organiser_ops.actor_role(organisation_id) IS NOT NULL AND user_id::text = current_setting('app.actor_id', true))
);
CREATE POLICY memberships_insert ON organiser_ops.memberships FOR INSERT WITH CHECK (
    (organiser_ops.actor_role(organisation_id) = 'owner' AND role IN ('manager','staff'))
    OR (organiser_ops.actor_role(organisation_id) = 'manager' AND role = 'staff')
);
CREATE POLICY memberships_delete ON organiser_ops.memberships FOR DELETE USING (
    (organiser_ops.actor_role(organisation_id) = 'owner' AND role IN ('manager','staff'))
    OR (organiser_ops.actor_role(organisation_id) = 'manager' AND role = 'staff')
);
CREATE POLICY events_select ON organiser_ops.events FOR SELECT USING (
    organiser_ops.actor_role(organisation_id) IN ('owner','manager')
    OR (organiser_ops.actor_role(organisation_id) = 'staff' AND EXISTS (
        SELECT 1 FROM organiser_ops.assignments a WHERE a.organisation_id = events.organisation_id
        AND a.event_id = events.id AND a.user_id::text = current_setting('app.actor_id', true)))
);
CREATE POLICY events_insert ON organiser_ops.events FOR INSERT WITH CHECK (organiser_ops.actor_role(organisation_id) IN ('owner','manager'));
CREATE POLICY events_update ON organiser_ops.events FOR UPDATE USING (organiser_ops.actor_role(organisation_id) IN ('owner','manager')) WITH CHECK (organiser_ops.actor_role(organisation_id) IN ('owner','manager'));
CREATE POLICY assignments_select ON organiser_ops.assignments FOR SELECT USING (
    organiser_ops.actor_role(organisation_id) IN ('owner','manager')
    OR (organiser_ops.actor_role(organisation_id) = 'staff' AND user_id::text = current_setting('app.actor_id', true))
);
CREATE POLICY assignments_insert ON organiser_ops.assignments FOR INSERT WITH CHECK (
    organiser_ops.actor_role(organisation_id) IN ('owner','manager') AND EXISTS (
        SELECT 1 FROM organiser_ops.memberships m WHERE m.organisation_id = assignments.organisation_id
        AND m.user_id = assignments.user_id AND m.role = 'staff')
);
CREATE POLICY assignments_delete ON organiser_ops.assignments FOR DELETE USING (organiser_ops.actor_role(organisation_id) IN ('owner','manager'));
CREATE POLICY tickets_select ON organiser_ops.tickets FOR SELECT USING (
    EXISTS (SELECT 1 FROM organiser_ops.events e WHERE e.organisation_id = tickets.organisation_id AND e.id = tickets.event_id)
);
CREATE POLICY tickets_insert ON organiser_ops.tickets FOR INSERT WITH CHECK (organiser_ops.actor_role(organisation_id) IN ('owner','manager'));
CREATE POLICY tickets_update ON organiser_ops.tickets FOR UPDATE USING (
    EXISTS (SELECT 1 FROM organiser_ops.events e WHERE e.organisation_id = tickets.organisation_id AND e.id = tickets.event_id)
) WITH CHECK (EXISTS (SELECT 1 FROM organiser_ops.events e WHERE e.organisation_id = tickets.organisation_id AND e.id = tickets.event_id));
CREATE POLICY audit_select ON organiser_ops.audit FOR SELECT USING (organiser_ops.actor_role(organisation_id) IN ('owner','manager'));
CREATE POLICY audit_insert ON organiser_ops.audit FOR INSERT WITH CHECK (
    organiser_ops.actor_role(organisation_id) IS NOT NULL AND actor_id::text = current_setting('app.actor_id', true)
);
