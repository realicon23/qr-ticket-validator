# Architecture Notes

This prototype uses an in-memory service to make the ticket lifecycle easy to inspect. A production platform would split these responsibilities into API, database, payment, and mobile/web check-in layers.

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

## Product Platform Mapping

| Prototype feature | Platform equivalent |
|---|---|
| HMAC-signed payload | Tamper-resistant digital ticket |
| Event-scoped validation | Prevent tickets being reused across events |
| Duplicate check-in prevention | Gate/venue access control |
| Audit log | Operational reporting and dispute review |
| Capacity tracking | Ticket inventory management |

## Production Extensions

Potential next steps:

- Persist events, orders, tickets, and check-ins in SQLite/Postgres.
- Add FastAPI endpoints for ticket issue and validation.
- Generate QR image files.
- Add organiser dashboard views.
- Add payment-confirmed ticket issue flow.
- Add CSV imports for attendee lists.
- Add authentication and role-based access control.
