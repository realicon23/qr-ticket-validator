"""In-process token issuer for local tests; not an OpenID Connect server."""

from datetime import datetime, timedelta, timezone

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa


class SyntheticIdentityProvider:
    issuer = "https://synthetic.example"
    audience = "synthetic-ticket-api"

    def __init__(self):
        self.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def public_keys(self):
        return {"local-test-key": self.private_key.public_key()}

    def issue(self, subject, **overrides):
        now = datetime.now(timezone.utc)
        claims = {"iss": self.issuer, "sub": str(subject), "aud": self.audience,
                  "iat": now, "exp": now + timedelta(minutes=5), "jti": "synthetic-token",
                  "client_id": "synthetic-client", "scope": "ticketing"}
        claims.update(overrides)
        return jwt.encode(claims, self.private_key, algorithm="RS256",
                          headers={"kid": "local-test-key", "typ": "at+jwt"})
