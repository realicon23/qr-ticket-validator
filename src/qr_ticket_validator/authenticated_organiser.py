"""Authenticated entry points for the local organiser-service experiment."""

import psycopg

from .identity import InvalidAccessToken
from .organiser_service import AccessDenied


class AuthenticatedOrganiserService:
    """Map verified access-token subjects to existing local users."""

    def __init__(self, verifier, service, allowed_clients, required_scope):
        if (not isinstance(allowed_clients, (set, frozenset, list, tuple)) or not allowed_clients
                or not isinstance(required_scope, str) or not required_scope or required_scope != required_scope.strip() or len(required_scope.split()) != 1
                or any(not isinstance(c, str) or not c for c in allowed_clients)):
            raise ValueError("allowed clients and a required scope are required")
        self.verifier = verifier
        self.service = service
        self.allowed_clients = frozenset(allowed_clients)
        self.required_scope = required_scope

    def _actor(self, token):
        try:
            principal = self.verifier.verify(token)
        except InvalidAccessToken:
            raise AccessDenied("operation unavailable") from None
        if principal.client_id not in self.allowed_clients or self.required_scope not in principal.scopes:
            raise AccessDenied("operation unavailable")
        with psycopg.connect(self.service.dsn) as conn:
            row = conn.execute("SELECT organiser_ops.resolve_user(%s, %s)", (principal.issuer, principal.subject)).fetchone()
        if row is None or row[0] is None:
            raise AccessDenied("operation unavailable")
        return row[0]

    def create_event(self, token, organisation, name, starts_at, ends_at, capacity):
        return self.service.create_event(self._actor(token), organisation, name, starts_at, ends_at, capacity)

    def add_member(self, token, organisation, user, new_role):
        return self.service.add_member(self._actor(token), organisation, user, new_role)

    def remove_member(self, token, organisation, user):
        return self.service.remove_member(self._actor(token), organisation, user)

    def assign_staff(self, token, organisation, event, user):
        return self.service.assign_staff(self._actor(token), organisation, event, user)

    def unassign_staff(self, token, organisation, event, user):
        return self.service.unassign_staff(self._actor(token), organisation, event, user)

    def issue_ticket(self, token, organisation, event, attendee_name, attendee_email):
        return self.service.issue_ticket(self._actor(token), organisation, event, attendee_name, attendee_email)

    def check_in(self, token, organisation, event, ticket_token):
        return self.service.check_in(self._actor(token), organisation, event, ticket_token)
