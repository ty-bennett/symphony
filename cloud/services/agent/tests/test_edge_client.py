import base64
import json
import tempfile
import unittest
from pathlib import Path

import edge_client


class ParseSseTest(unittest.TestCase):
    def test_yields_named_events_with_json_data(self):
        lines = [
            b"event: meta\n",
            b'data: {"route":"private-cloud"}\n',
            b"\n",
            b"event: token\n",
            b'data: {"text":"Hi"}\n',
            b"\n",
        ]
        self.assertEqual(
            list(edge_client.parse_sse(lines)),
            [("meta", {"route": "private-cloud"}), ("token", {"text": "Hi"})],
        )

    def test_ignores_comments_and_flushes_a_final_event_without_blank_line(self):
        lines = [
            b": keepalive\n",
            b"event: done\n",
            b'data: {"stop_reason":"end_turn"}\n',
        ]
        self.assertEqual(
            list(edge_client.parse_sse(lines)), [("done", {"stop_reason": "end_turn"})]
        )


class EnvelopeTest(unittest.TestCase):
    def test_builds_a_v1_request_with_a_fresh_request_id(self):
        first = json.loads(
            edge_client.envelope("Hello", "conv-00000001", "private-cloud", "default")
        )
        second = json.loads(
            edge_client.envelope("Hello", "conv-00000001", "private-cloud", "default")
        )
        self.assertEqual(first["version"], 1)
        self.assertEqual(first["messages"], [{"role": "user", "text": "Hello"}])
        self.assertNotEqual(first["request_id"], second["request_id"])
        self.assertNotIn("tenant_id", first)


class SummaryTest(unittest.TestCase):
    def test_collects_answer_tools_and_time_to_first_token(self):
        clock = iter([1.2, 2.0]).__next__
        events = [
            ("meta", {"model": "kimi"}),
            ("tool", {"name": "docs___search_documents"}),
            ("token", {"text": "1.5 days"}),
            ("done", {"stop_reason": "end_turn"}),
        ]
        summary = edge_client.summarize(events, clock=clock, started=0.0)
        self.assertEqual(summary["answer"], "1.5 days")
        self.assertEqual(summary["tools"], ["docs___search_documents"])
        self.assertEqual(summary["ttft_s"], 1.2)
        self.assertEqual(summary["end"], "done")
        self.assertEqual(summary["total_s"], 2.0)


class TotpTest(unittest.TestCase):
    def test_matches_the_rfc_6238_sha1_vector(self):
        secret = base64.b32encode(b"12345678901234567890").decode()
        self.assertEqual(edge_client.totp(secret, at=59), "287082")
        self.assertEqual(edge_client.totp(secret, at=1111111109), "081804")


class FakeCognito:
    def __init__(self, first_challenge):
        self.first_challenge = first_challenge
        self.calls = []

    def admin_initiate_auth(self, **kwargs):
        self.calls.append("initiate")
        return {"ChallengeName": self.first_challenge, "Session": "s1"}

    def associate_software_token(self, Session):
        self.calls.append("associate")
        return {
            "SecretCode": base64.b32encode(b"12345678901234567890").decode(),
            "Session": "s2",
        }

    def verify_software_token(self, Session, UserCode):
        self.calls.append(f"verify:{len(UserCode)}")
        return {"Status": "SUCCESS", "Session": "s3"}

    def admin_respond_to_auth_challenge(self, **kwargs):
        self.calls.append(f"respond:{kwargs['ChallengeName']}")
        return {"AuthenticationResult": {"AccessToken": "token-123", "ExpiresIn": 3600}}


class SignInTest(unittest.TestCase):
    def setUp(self):
        self.users = Path(tempfile.mkdtemp())
        (self.users / "a@example.com.json").write_text(
            json.dumps({"username": "a@example.com", "password": "pw"})
        )

    def test_enrolls_totp_on_first_sign_in_and_keeps_the_secret(self):
        cognito = FakeCognito("MFA_SETUP")
        token = edge_client.access_token(
            cognito, "pool", "client", "a@example.com", users_dir=self.users
        )
        self.assertEqual(token, "token-123")
        self.assertEqual(
            cognito.calls, ["initiate", "associate", "verify:6", "respond:MFA_SETUP"]
        )
        self.assertIn(
            "totp_secret", json.loads((self.users / "a@example.com.json").read_text())
        )

    def test_answers_the_totp_challenge_on_later_sign_ins(self):
        cognito = FakeCognito("SOFTWARE_TOKEN_MFA")
        record = {
            "username": "a@example.com",
            "password": "pw",
            "totp_secret": "GEZDGNBVGY3TQOJQ",
        }
        (self.users / "a@example.com.json").write_text(json.dumps(record))
        token = edge_client.access_token(
            cognito, "pool", "client", "a@example.com", users_dir=self.users
        )
        self.assertEqual(token, "token-123")
        self.assertEqual(cognito.calls, ["initiate", "respond:SOFTWARE_TOKEN_MFA"])

    def test_reuses_a_cached_token_until_shortly_before_it_expires(self):
        record = {
            "username": "a@example.com",
            "password": "pw",
            "totp_secret": "GEZDGNBVGY3TQOJQ",
            "access_token": "cached",
            "expires_at": 10_000,
        }
        (self.users / "a@example.com.json").write_text(json.dumps(record))
        cognito = FakeCognito("SOFTWARE_TOKEN_MFA")
        self.assertEqual(
            edge_client.access_token(
                cognito,
                "pool",
                "client",
                "a@example.com",
                users_dir=self.users,
                now=9_000,
            ),
            "cached",
        )
        self.assertEqual(cognito.calls, [])
        self.assertEqual(
            edge_client.access_token(
                cognito,
                "pool",
                "client",
                "a@example.com",
                users_dir=self.users,
                now=9_950,
            ),
            "token-123",
        )


if __name__ == "__main__":
    unittest.main()
