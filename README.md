# QR Ticket Validator

A small, dependency-free Python prototype for event ticket generation and check-in validation.

The project models a core workflow used by digital ticketing platforms:

1. create an event
2. issue a signed ticket payload
3. encode that payload into a QR-compatible string
4. validate the ticket at check-in
5. prevent duplicate check-ins
6. keep an audit trail for ticket decisions

It is intentionally small: the core workflow can be read, run, and tested without external services.

## What It Demonstrates

This prototype covers:

- HMAC-signed ticket payloads
- event-scoped validation
- duplicate check-in prevention
- ticket expiry handling
- capacity tracking
- structured audit logs
- a simple command-line demo

## Architecture

```mermaid
flowchart LR
    Organizer["Event organizer"] --> Event["Create event"]
    Event --> Ticket["Issue ticket"]
    Ticket --> Sign["Sign payload with HMAC"]
    Sign --> QR["QR-compatible token"]
    QR --> Gate["Check-in desk"]
    Gate --> Verify["Verify signature, event, expiry, status"]
    Verify -->|valid| Admit["Admit attendee"]
    Verify -->|invalid| Reject["Reject and audit"]
    Admit --> Audit["Audit log"]
    Reject --> Audit
```

## Repository Structure

```text
qr-ticket-validator/
  src/qr_ticket_validator/
    __init__.py
    audit.py
    cli.py
    models.py
    service.py
    signing.py
  tests/
    test_ticket_service.py
  docs/
    architecture.md
  examples/
    demo.py
  README.md
  pyproject.toml
```

## Quick Start

Run the demo:

```bash
python3 examples/demo.py
```

Run the tests:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
```

Run the CLI:

```bash
python3 -m qr_ticket_validator.cli demo
```

If running the CLI from a fresh checkout, set `PYTHONPATH`:

```bash
PYTHONPATH=src python3 -m qr_ticket_validator.cli demo
```

## Example Output

```text
Issued ticket for Ada Lovelace
First validation: accepted
Second validation: rejected_duplicate_check_in
Tampered validation: rejected_invalid_signature
```

## Core Concepts

### Signed Ticket Payload

Each ticket payload is serialized and signed with HMAC-SHA256. If the payload is changed after issue, signature verification fails.

### Event-Scoped Validation

Tickets are tied to a specific event ID. A valid token for one event cannot be checked into another event.

### Duplicate Check-In Prevention

The validator stores check-in state. Once a ticket has been accepted, a second attempt is rejected.

### Audit Trail

Every validation decision is logged with:

- timestamp
- ticket ID
- event ID
- decision
- reason

## Project Scope

Implemented:

- in-memory event and ticket storage
- HMAC token signing and verification
- capacity checks at ticket issue
- check-in validation decisions
- audit records for each validation attempt

Not implemented:

- payment processing
- persistent database storage
- QR image rendering
- web or mobile user interface
- user accounts or roles

## Possible Extensions

- Add FastAPI service wrapper
- Add SQLite persistence
- Add QR image generation
- Add CSV attendee import/export
- Add organiser dashboard mock
- Add payment-confirmed ticket issuance flow

## License

MIT
