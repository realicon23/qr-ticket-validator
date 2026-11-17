"""Signing-key selection for versioned ticket tokens."""

import re
from types import MappingProxyType

from .signing import decode_token, encode_token, verify_token


class TicketKeys:
    """Use an active key for issue and an explicit set of verification keys."""

    def __init__(self, active_id: str, keys: dict[str, str]):
        if not keys or active_id not in keys:
            raise ValueError("active key must be configured")
        for key_id, secret in keys.items():
            if not isinstance(key_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", key_id):
                raise ValueError("invalid key identifier")
            if not isinstance(secret, str) or len(secret.encode("utf-8")) < 32:
                raise ValueError("keys must contain at least 32 bytes")
        self.active_id = active_id
        self._keys = MappingProxyType(dict(keys))

    def issue(self, organisation_id: str, event_id: str, ticket_id: str) -> str:
        payload = {"version": 3, "key_id": self.active_id, "organisation_id": organisation_id,
                   "event_id": event_id, "ticket_id": ticket_id}
        return encode_token(payload, self._keys[self.active_id])

    def verify(self, token: str):
        try:
            envelope = decode_token(token)
        except (ValueError, UnicodeDecodeError, RecursionError):
            return False, None
        payload = envelope.get("payload")
        if not isinstance(payload, dict):
            return False, None
        key_id = payload.get("key_id")
        if not isinstance(key_id, str) or key_id not in self._keys:
            return False, None
        return verify_token(token, self._keys[key_id])
