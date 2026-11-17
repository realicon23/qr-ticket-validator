# Architecture Notes

The prototype has an in-memory service for the demo and a separate PostgreSQL-backed service.

## Core Components

| Component | Responsibility |
|---|---|
| `TicketService` | Creates events, issues tickets, validates check-ins, tracks capacity |
| `PostgresTicketService` | Stores events, tickets, and check-in decisions in PostgreSQL |
| `signing.py` | Encodes, signs, decodes, and verifies QR-compatible tokens |
| `models.py` | Defines event, attendee, ticket, and validation result models |
| `AuditLog` | Stores validation decisions in memory for the demo |
| CLI/demo | Shows the in-memory workflow |

## Validation Decisions

The validator can return:

- `accepted`
- `rejected_duplicate_check_in`
- `rejected_expired`
- `rejected_invalid_event`
- `rejected_invalid_signature`
- `rejected_unknown_ticket`

## Implemented Behaviours

| Feature | Implementation |
|---|---|
| HMAC-signed payload | `signing.py` signs and verifies compact tokens |
| Event-scoped validation | Both services check the expected event ID |
| Duplicate check-in prevention | A ticket with `checked_in_at` is rejected on reuse |
| Audit log | The demo records decisions in memory; the PostgreSQL service writes them to `checkin_audit` |
| Capacity tracking | Each service checks issued tickets against event capacity |

## PostgreSQL Transactions

Ticket issuance uses a Read Committed transaction and locks the event row before counting issued tickets. Check-in locks the ticket row, checks expiry after acquiring the lock, and writes the decision in the same transaction as the check-in update. A failed audit write rolls back that update.

These protections depend on all writers following the same locking rules. Direct database writes can bypass the capacity check. The optional check-in time argument is for trusted tests.

## Ticket Formats

The in-memory demo signs a payload containing attendee details. The PostgreSQL service issues a versioned token containing the event and ticket identifiers, and stores attendee details in the database. It rejects tokens issued by the in-memory demo.

`PostgresTicketService` relies on a trusted caller and database access. Authentication, key management, rate limiting, audit retention, and deployment configuration require separate controls.

## Organiser Isolation Experiment

The separate `sql/001_organiser_isolation.sql` creates a schema for organiser memberships, event assignments, tickets, and audit records. Its row policies limit reads to the current organiser and membership; staff can see only assigned events. The tests grant the synthetic reader role access to attendee names, but not email addresses or writes.

The schema is separate from `PostgresTicketService`. The test role has no login and no write privileges. The policies rely on transaction-local actor and organiser settings supplied by trusted code. A client able to execute arbitrary SQL as the database role could change those settings, so this mechanism does not authenticate a user or protect against SQL injection. A future API must authenticate requests, verify current membership, control all SQL access, and establish the context within each transaction.

Deleting a membership also deletes its event assignments in the same database transaction.

The schema owner and superusers can bypass row security. Runtime roles must not own the tables or have superuser or `BYPASSRLS` privileges. Do not use this experiment with real attendees.

## Organiser-Scoped Operations

The separate `sql/002_organiser_operations.sql` and `OrganiserService` experiment with organiser-scoped event creation, staff membership and assignment, ticket issuance, and check-in. Owners can manage manager and staff membership. Managers can manage staff membership and event assignments. Staff can check in tickets for assigned events. Membership removal deletes assignments, and the operation writes an audit entry in the same transaction.

Ticket issuance and check-in use database functions that lock the relevant rows and write audit entries within the transaction. Invalid tokens are rejected before the check-in function is called, without an audit record. Events have a start and end time; tickets expire at the event's end, and issuance is rejected after that time. A database trigger prevents an accepted check-in from being reset. Version 3 ticket tokens contain organiser, event, ticket, and key identifiers. Earlier token formats are not imported or accepted by this service.

`OrganiserService` trusts the actor and organiser passed by its caller; it does not authenticate them. A caller with arbitrary SQL access under the runtime role can alter the session context and impersonate another actor. Do not expose the database role or use this service with real attendee data. The privileged database functions must be owned by a dedicated non-login, non-superuser role, with function execution and runtime permissions restricted. Deployment would also require rate limiting and audit retention controls.

Membership records refer to internal user records with issuer and subject fields. The local fixture creates synthetic users directly; the presence of a user record does not verify an identity. Tests deny direct table writes for the runtime role, but that role can call the check-in function without presenting a ticket signature. The service verifies signatures before calling it. This is an application trust boundary, not protection against an attacker with arbitrary SQL access.

`TicketKeys` issues with one configured active key and verifies with an explicit set of keys. Removing a key from the verification set rejects its tokens; deployment key management remains outside the prototype. `AccessTokenVerifier` supports RS256 JWT access tokens following RFC 9068 using preconfigured trusted public keys. `AuthenticatedOrganiserService` checks the configured client and scope, maps the verified issuer and subject to an existing user, and calls the organiser service, which checks membership in each operation.

Tests use an in-process synthetic token issuer with a generated key. The verifier uses configured keys and does not check token revocation. A verified access token alone does not establish that its subject is an authorised human user. A real provider's token and subject semantics must be assessed before deployment. The lower-level organiser service remains callable by trusted code, so a future API must route every user operation through the authenticated boundary.
