import base64
import json
import unittest

from qr_ticket_validator.signing import encode_token, verify_token


class SigningTest(unittest.TestCase):
    def test_malformed_tokens_are_rejected(self):
        def envelope(payload, signature):
            return base64.urlsafe_b64encode(json.dumps({"payload": payload, "signature": signature}).encode()).decode().rstrip("=")
        malformed = ["", "!", "x" * 4097, envelope({}, []), envelope([], "a" * 64), envelope({}, "é" * 64)]
        nested = b'{"payload":' + b'[' * 1100 + b'0' + b']' * 1100 + b',"signature":"x"}'
        malformed.append(base64.urlsafe_b64encode(nested).decode().rstrip("="))
        for token in malformed:
            with self.subTest(token=token[:20]):
                self.assertEqual(verify_token(token, "test-secret-value"), (False, None))

    def test_legacy_signature_remains_verifiable(self):
        payload = {"ticket_id": "one", "attendee_email": "person@example.com"}
        self.assertEqual(verify_token(encode_token(payload, "test-secret-value"), "test-secret-value"), (True, payload))
