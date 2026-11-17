"""Local verifier for RS256 JWT access tokens conforming to RFC 9068."""

from dataclasses import dataclass
import math
import re
from types import MappingProxyType

import jwt
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey


@dataclass(frozen=True)
class VerifiedPrincipal:
    issuer: str
    subject: str
    client_id: str
    scopes: frozenset[str]


class InvalidAccessToken(Exception):
    """An access token could not be verified."""


class AccessTokenVerifier:
    """Verify tokens using issuer keys supplied by trusted configuration."""

    def __init__(self, issuer: str, audience: str, keys: dict[str, RSAPublicKey]):
        if not issuer or not audience or not keys:
            raise ValueError("issuer, audience, and keys are required")
        for key_id, public_key in keys.items():
            if not isinstance(key_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", key_id):
                raise ValueError("invalid key identifier")
            if not isinstance(public_key, RSAPublicKey) or public_key.key_size < 2048:
                raise ValueError("RSA public key must be at least 2048 bits")
        self.issuer = issuer
        self.audience = audience
        self._keys = MappingProxyType(dict(keys))

    def verify(self, token: str) -> VerifiedPrincipal:
        if not isinstance(token, str) or len(token) > 16384:
            raise InvalidAccessToken("invalid access token")
        try:
            header = jwt.get_unverified_header(token)
            key_id = header.get("kid")
            token_type = header.get("typ")
            if (header.get("alg") != "RS256" or not isinstance(token_type, str)
                    or token_type.lower() not in ("at+jwt", "application/at+jwt")
                    or not isinstance(key_id, str) or key_id not in self._keys
                    or any(name in header for name in ("crit", "jku", "jwk", "x5u"))):
                raise InvalidAccessToken("invalid access token")
            claims = jwt.decode(token, self._keys[key_id], algorithms=["RS256"], issuer=self.issuer,
                                audience=self.audience,
                                options={"require": ["iss", "sub", "aud", "exp", "iat", "jti", "client_id"]})
            if not all(isinstance(claims.get(name), str) and claims[name] for name in ("iss", "sub", "jti", "client_id")):
                raise InvalidAccessToken("invalid access token")
            scope = claims.get("scope", "")
            if not isinstance(scope, str):
                raise InvalidAccessToken("invalid access token")
            for name in ("exp", "iat", "nbf"):
                if name in claims and (type(claims[name]) not in (int, float) or not math.isfinite(claims[name])):
                    raise InvalidAccessToken("invalid access token")
        except (jwt.InvalidTokenError, TypeError, ValueError, OverflowError):
            raise InvalidAccessToken("invalid access token") from None
        return VerifiedPrincipal(issuer=claims["iss"], subject=claims["sub"],
                                 client_id=claims["client_id"], scopes=frozenset(scope.split()))
