-- Apply once as the schema owner in a new, dedicated database.
CREATE SCHEMA organiser_stage;

CREATE TABLE organiser_stage.organisers (
    id uuid PRIMARY KEY,
    name text NOT NULL
);

CREATE TABLE organiser_stage.memberships (
    organiser_id uuid NOT NULL REFERENCES organiser_stage.organisers(id),
    user_id uuid NOT NULL,
    role text NOT NULL CHECK (role IN ('owner', 'manager', 'staff')),
    PRIMARY KEY (organiser_id, user_id)
);

CREATE TABLE organiser_stage.events (
    id uuid PRIMARY KEY,
    organiser_id uuid NOT NULL REFERENCES organiser_stage.organisers(id),
    name text NOT NULL,
    UNIQUE (organiser_id, id)
);

CREATE TABLE organiser_stage.assignments (
    organiser_id uuid NOT NULL,
    event_id uuid NOT NULL,
    user_id uuid NOT NULL,
    PRIMARY KEY (organiser_id, event_id, user_id),
    FOREIGN KEY (organiser_id, event_id) REFERENCES organiser_stage.events (organiser_id, id),
    FOREIGN KEY (organiser_id, user_id) REFERENCES organiser_stage.memberships (organiser_id, user_id) ON DELETE CASCADE
);

CREATE TABLE organiser_stage.tickets (
    id uuid PRIMARY KEY,
    organiser_id uuid NOT NULL,
    event_id uuid NOT NULL,
    attendee_name text NOT NULL,
    attendee_email text NOT NULL,
    FOREIGN KEY (organiser_id, event_id) REFERENCES organiser_stage.events (organiser_id, id)
);

CREATE TABLE organiser_stage.audit (
    id uuid PRIMARY KEY,
    organiser_id uuid NOT NULL REFERENCES organiser_stage.organisers(id),
    action text NOT NULL
);

ALTER TABLE organiser_stage.organisers ENABLE ROW LEVEL SECURITY;
ALTER TABLE organiser_stage.memberships ENABLE ROW LEVEL SECURITY;
ALTER TABLE organiser_stage.events ENABLE ROW LEVEL SECURITY;
ALTER TABLE organiser_stage.assignments ENABLE ROW LEVEL SECURITY;
ALTER TABLE organiser_stage.tickets ENABLE ROW LEVEL SECURITY;
ALTER TABLE organiser_stage.audit ENABLE ROW LEVEL SECURITY;

-- Identity and organiser context must be set by trusted application code per transaction.
CREATE POLICY own_membership ON organiser_stage.memberships FOR SELECT USING (
    user_id::text = current_setting('app.actor_id', true)
    AND organiser_id::text = current_setting('app.organiser_id', true)
);
CREATE POLICY own_assignments ON organiser_stage.assignments FOR SELECT USING (
    user_id::text = current_setting('app.actor_id', true)
    AND organiser_id::text = current_setting('app.organiser_id', true)
);
CREATE POLICY current_organiser ON organiser_stage.organisers FOR SELECT USING (
    EXISTS (SELECT 1 FROM organiser_stage.memberships m WHERE m.organiser_id = organisers.id)
);
CREATE POLICY visible_events ON organiser_stage.events FOR SELECT USING (
    EXISTS (SELECT 1 FROM organiser_stage.memberships m WHERE m.organiser_id = events.organiser_id
      AND (m.role IN ('owner', 'manager') OR EXISTS (
        SELECT 1 FROM organiser_stage.assignments a WHERE a.organiser_id = events.organiser_id AND a.event_id = events.id
      )))
);
CREATE POLICY visible_tickets ON organiser_stage.tickets FOR SELECT USING (
    EXISTS (SELECT 1 FROM organiser_stage.events e WHERE e.organiser_id = tickets.organiser_id AND e.id = tickets.event_id)
);
CREATE POLICY manager_audit ON organiser_stage.audit FOR SELECT USING (
    EXISTS (SELECT 1 FROM organiser_stage.memberships m WHERE m.organiser_id = audit.organiser_id AND m.role IN ('owner', 'manager'))
);
