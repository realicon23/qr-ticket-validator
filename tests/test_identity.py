import unittest
from datetime import datetime, timedelta, timezone

try:
    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa
    from qr_ticket_validator.identity import AccessTokenVerifier, InvalidAccessToken, VerifiedPrincipal
except ImportError:
    jwt = None


@unittest.skipUnless(jwt is not None, "identity dependencies are not installed")
class AccessTokenVerifierTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.verifier = AccessTokenVerifier("https://issuer.example", "ticket-api", {"key1": cls.key.public_key()})

    def token(self, overrides=None, headers=None, key=None):
        now = datetime.now(timezone.utc)
        claims = {"iss": "https://issuer.example", "sub": "synthetic-user", "aud": "ticket-api",
                  "iat": now, "exp": now + timedelta(minutes=5), "jti": "test-token", "client_id": "test-client"}
        claims.update(overrides or {})
        return jwt.encode(claims, key or self.key, algorithm="RS256", headers=headers or {"kid": "key1", "typ": "at+jwt"})

    def test_valid_access_token(self):
        self.assertEqual(self.verifier.verify(self.token()), VerifiedPrincipal("https://issuer.example", "synthetic-user", "test-client", frozenset()))

    def test_invalid_tokens(self):
        cases = [
            self.token({"iss": "https://other.example"}),
            self.token({"aud": "different-api"}),
            self.token({"exp": datetime.now(timezone.utc) - timedelta(seconds=1)}),
            self.token({"sub": ""}),
            self.token({"scope": ["ticketing"]}),
            self.token({"iat": datetime.now(timezone.utc) + timedelta(minutes=10)}),
            self.token({"exp": "9999999999"}),
            self.token({"nbf": datetime.now(timezone.utc) + timedelta(minutes=10)}),
            self.token(headers={"kid": "key1", "typ": "JWT"}),
            self.token(headers={"kid": "unknown", "typ": "at+jwt"}),
            self.token(headers={"kid": "key1", "typ": "at+jwt", "jku": "https://invalid.example"}),
            self.token(key=self.other_key),
            "x" * 16385,
            "invalid",
        ]
        for token in cases:
            with self.subTest(token=token[:20]), self.assertRaises(InvalidAccessToken):
                self.verifier.verify(token)

    def test_missing_required_claim(self):
        now = datetime.now(timezone.utc)
        claims = {"iss": "https://issuer.example", "sub": "synthetic-user", "aud": "ticket-api",
                  "iat": now, "exp": now + timedelta(minutes=5), "jti": "test-token"}
        token = jwt.encode(claims, self.key, algorithm="RS256", headers={"kid": "key1", "typ": "at+jwt"})
        with self.assertRaises(InvalidAccessToken):
            self.verifier.verify(token)

    def test_rejects_weak_or_invalid_configuration(self):
        weak = rsa.generate_private_key(public_exponent=65537, key_size=1024)
        with self.assertRaises(ValueError):
            AccessTokenVerifier("https://issuer.example", "ticket-api", {"key": weak.public_key()})
