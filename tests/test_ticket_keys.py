import unittest

from qr_ticket_validator.signing import encode_token
from qr_ticket_validator.ticket_keys import TicketKeys


OLD = "synthetic-old-key-0000000000000000"
NEW = "synthetic-new-key-1111111111111111"


class TicketKeysTest(unittest.TestCase):
    def test_rotation_and_revocation(self):
        original = TicketKeys("old", {"old": OLD})
        old_token = original.issue("org", "event", "old-ticket")
        rotated = TicketKeys("new", {"old": OLD, "new": NEW})
        new_token = rotated.issue("org", "event", "new-ticket")
        self.assertTrue(rotated.verify(old_token)[0])
        self.assertTrue(rotated.verify(new_token)[0])
        revoked = TicketKeys("new", {"new": NEW})
        self.assertFalse(revoked.verify(old_token)[0])
        self.assertTrue(revoked.verify(new_token)[0])

    def test_key_identifier_is_covered_by_signature(self):
        keys = TicketKeys("new", {"old": OLD, "new": NEW})
        forged = encode_token({"version": 3, "key_id": "new", "organisation_id": "org", "event_id": "event", "ticket_id": "id"}, OLD)
        self.assertFalse(keys.verify(forged)[0])
        self.assertFalse(keys.verify(encode_token({"key_id": "unknown"}, OLD))[0])
        self.assertFalse(keys.verify(encode_token({"version": 2}, OLD))[0])

    def test_invalid_configuration(self):
        for active, keys in [("missing", {"old": OLD}), ("bad/key", {"bad/key": OLD}), ("short", {"short": "short"})]:
            with self.subTest(active=active), self.assertRaises(ValueError):
                TicketKeys(active, keys)
