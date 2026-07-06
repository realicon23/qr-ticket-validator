# Architecture Notes

This prototype uses an in-memory service to keep the ticket lifecycle easy to inspect. The current implementation does not include a web API, database, payment layer, or mobile check-in client.

## Core Components

| Component | Responsibility |
|---|---|
| `TicketService` | Creates events, issues tickets, validates check-ins, tracks capacity |
| `signing.py` | Encodes, signs, decodes, and verifies QR-compatible tokens |
| `models.py` | Defines event, attendee, ticket, and validation result models |
| `AuditLog` | Stores validation decisions for reporting and traceability |
| CLI/demo | Shows the workflow end to end |

## Validation Decisions

The validator can return:

- `accepted`
- `rejected_duplicate_check_in`
- `rejected_expired`
- `rejected_invalid_event`
- `rejected_invalid_signature`
- `rejected_unknown_ticket`

## Implemented Behaviors

| Feature | Implementation |
|---|---|
| HMAC-signed payload | `signing.py` signs and verifies compact tokens |
| Event-scoped validation | `TicketService.validate` checks the expected event ID |
| Duplicate check-in prevention | A ticket with `checked_in_at` is rejected on reuse |
| Audit log | `AuditLog` records every validation decision |
| Capacity tracking | `TicketService.issue_ticket` enforces event capacity |

## Possible Extensions

Potential next steps:

- Persist events, orders, tickets, and check-ins in SQLite/Postgres.
- Add FastAPI endpoints for ticket issue and validation.
- Generate QR image files.
- Add organiser dashboard views.
- Add payment-confirmed ticket issue flow.
- Add CSV imports for attendee lists.
- Add authentication and role-based access control.
