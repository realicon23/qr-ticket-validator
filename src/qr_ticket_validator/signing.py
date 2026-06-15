"""Signing helpers for QR-compatible ticket tokens."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def canonical_json(payload: dict[str, Any]) -> bytes:
    """Serialize payload deterministically for signing."""

    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sign_payload(payload: dict[str, Any], secret: str) -> str:
    """Return an HMAC-SHA256 signature for a payload."""

    return hmac.new(secret.encode("utf-8"), canonical_json(payload), hashlib.sha256).hexdigest()


def encode_token(payload: dict[str, Any], secret: str) -> str:
    """Create a compact QR-compatible token."""

    envelope = {
        "payload": payload,
        "signature": sign_payload(payload, secret),
    }
    return _b64encode(canonical_json(envelope))


def decode_token(token: str) -> dict[str, Any]:
    """Decode a QR-compatible token into its envelope."""

    decoded = _b64decode(token)
    data = json.loads(decoded.decode("utf-8"))
    if not isinstance(data, dict) or "payload" not in data or "signature" not in data:
        raise ValueError("invalid token envelope")
    return data


def verify_token(token: str, secret: str) -> tuple[bool, dict[str, Any] | None]:
    """Verify a token and return its payload when valid."""

    try:
        envelope = decode_token(token)
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return False, None

    payload = envelope["payload"]
    signature = envelope["signature"]
    expected = sign_payload(payload, secret)
    if not hmac.compare_digest(signature, expected):
        return False, payload if isinstance(payload, dict) else None
    return True, payload if isinstance(payload, dict) else None
