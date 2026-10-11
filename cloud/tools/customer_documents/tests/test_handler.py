import io
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tools.customer_documents import handler


SETTINGS = handler.Settings(
    bucket="approved-bucket",
    prefix="approved/",
    role_arn="arn:aws:iam::111122223333:role/SymphonyIntegrationRole",
    external_id="integration-123",
    gateway_id="gateway-123",
    target_id="target-123",
    target_name="customer_documents",
)


def context(tool="list_documents", gateway_id="gateway-123", target_id="target-123"):
    metadata = {
        "bedrockAgentCoreGatewayId": gateway_id,
        "bedrockAgentCoreTargetId": target_id,
        "bedrockAgentCoreToolName": f"customer_documents___{tool}",
    }
    return SimpleNamespace(
        client_context=SimpleNamespace(custom=metadata),
        aws_request_id="lambda-request-123",
    )


class Body(io.BytesIO):
    pass


class FakeS3:
    def __init__(self, objects=None):
        self.objects = objects or {}
        self.calls = []
        self.last_body = None

    def list_objects_v2(self, **kwargs):
        self.calls.append(("list", kwargs))
        keys = sorted(key for key in self.objects if key.startswith(kwargs["Prefix"]))
        limited = keys[: kwargs["MaxKeys"]]
        return {
            "Contents": [
                {"Key": key, "Size": len(self.objects[key])} for key in limited
            ],
            "IsTruncated": len(keys) > len(limited),
        }

    def get_object(self, **kwargs):
        self.calls.append(("get", kwargs))
        self.last_body = Body(self.objects[kwargs["Key"]])
        return {"Body": self.last_body}


class DocumentToolTests(unittest.TestCase):
    def test_schema_and_handler_have_same_tool_names(self):
        path = Path(__file__).parents[1] / "gateway-tools.json"
        definitions = json.loads(path.read_text())
        self.assertEqual({item["name"] for item in definitions}, handler.TOOL_NAMES)
        manifest = json.loads((path.parent / "manifest.json").read_text())
        self.assertEqual(
            {item["name"] for item in manifest["tools"]}, handler.TOOL_NAMES
        )
        self.assertFalse(manifest["frontier_egress_allowed"])
        self.assertEqual(manifest["execution_routes"], ["private-cloud"])

    def test_list_scopes_to_configured_folder(self):
        s3 = FakeS3(
            {
                "approved/policies/handbook.md": b"hello",
                "approved/policies/leave.txt": b"days",
                "approved/policies-other/secret.md": b"no",
                "other/policies/secret.md": b"no",
            }
        )
        result = handler.run_tool(
            {"relative_prefix": "policies", "limit": 2},
            context(),
            settings=SETTINGS,
            s3=s3,
        )
        self.assertEqual(
            [item["key"] for item in result["documents"]],
            ["policies/handbook.md", "policies/leave.txt"],
        )
        self.assertEqual(s3.calls[0][1]["Prefix"], "approved/policies/")

    def test_list_is_bounded(self):
        s3 = FakeS3({f"approved/file-{index}.txt": b"x" for index in range(25)})
        result = handler.run_tool({"limit": 20}, context(), settings=SETTINGS, s3=s3)
        self.assertEqual(len(result["documents"]), 20)
        self.assertTrue(result["truncated"])
        self.assertEqual(s3.calls[0][1]["MaxKeys"], 21)

    def test_read_caps_content_and_closes_stream(self):
        s3 = FakeS3({"approved/a.md": b"a" * (handler.MAX_DOCUMENT_BYTES + 10)})
        result = handler.run_tool(
            {"key": "a.md"}, context("read_document"), settings=SETTINGS, s3=s3
        )
        self.assertEqual(len(result["text"]), handler.MAX_DOCUMENT_BYTES)
        self.assertTrue(result["truncated"])
        self.assertTrue(s3.last_body.closed)
        self.assertEqual(s3.calls[0][1]["Key"], "approved/a.md")

    def test_utf8_split_at_limit_is_not_corrupted(self):
        s3 = FakeS3(
            {
                "approved/a.txt": b"a" * (handler.MAX_DOCUMENT_BYTES - 1)
                + "é".encode()
                + b"z"
            }
        )
        result = handler.run_tool(
            {"key": "a.txt"}, context("read_document"), settings=SETTINGS, s3=s3
        )
        self.assertEqual(result["text"], "a" * (handler.MAX_DOCUMENT_BYTES - 1))
        self.assertTrue(result["truncated"])

    def test_rejects_binary_and_non_text_extensions(self):
        s3 = FakeS3({"approved/a.txt": b"a\x00b"})
        with self.assertRaises(handler.ToolError) as caught:
            handler.run_tool(
                {"key": "a.txt"}, context("read_document"), settings=SETTINGS, s3=s3
            )
        self.assertEqual(caught.exception.code, "UNSUPPORTED_DOCUMENT")
        with self.assertRaises(handler.ToolError):
            handler.run_tool(
                {"key": "a.pdf"}, context("read_document"), settings=SETTINGS, s3=s3
            )
        self.assertEqual(len(s3.calls), 1)

    def test_empty_text_document_is_valid(self):
        s3 = FakeS3({"approved/empty.md": b""})
        result = handler.run_tool(
            {"key": "empty.md"}, context("read_document"), settings=SETTINGS, s3=s3
        )
        self.assertEqual(result["text"], "")
        self.assertFalse(result["truncated"])

    def test_invalid_utf8_inside_truncated_document_is_rejected(self):
        s3 = FakeS3({"approved/a.txt": b"bad\xff" + b"a" * handler.MAX_DOCUMENT_BYTES})
        with self.assertRaises(handler.ToolError) as caught:
            handler.run_tool(
                {"key": "a.txt"}, context("read_document"), settings=SETTINGS, s3=s3
            )
        self.assertEqual(caught.exception.code, "UNSUPPORTED_DOCUMENT")

    def test_rejects_paths_before_customer_call(self):
        s3 = FakeS3()
        for key in (
            "../other.txt",
            "/other.txt",
            "a/../other.txt",
            "a//b.txt",
            "a\\b.txt",
            "a\x00b.txt",
        ):
            with self.subTest(key=key), self.assertRaises(handler.ToolError):
                handler.run_tool(
                    {"key": key}, context("read_document"), settings=SETTINGS, s3=s3
                )
        self.assertFalse(s3.calls)

    def test_rejects_unapproved_gateway_and_tool(self):
        s3 = FakeS3()
        for bad_context in (
            context(gateway_id="wrong"),
            context(target_id="wrong"),
            context(tool="delete_document"),
        ):
            with self.assertRaises(handler.ToolError):
                handler.run_tool({}, bad_context, settings=SETTINGS, s3=s3)
        self.assertFalse(s3.calls)

    def test_rejects_extra_fields_and_invalid_limit(self):
        s3 = FakeS3()
        for event in (
            {"bucket": "other"},
            {"limit": True},
            {"limit": 21},
            {"limit": "2"},
        ):
            with self.subTest(event=event), self.assertRaises(handler.ToolError):
                handler.run_tool(event, context(), settings=SETTINGS, s3=s3)
        self.assertFalse(s3.calls)

    def test_configuration_is_fail_closed(self):
        with self.assertRaises(handler.ToolError) as caught:
            handler.Settings.from_env({})
        self.assertEqual(caught.exception.code, "NOT_CONFIGURED")

    def test_target_id_is_optional_but_gateway_and_tool_are_enforced(self):
        values = {
            "DOCS_BUCKET": "approved-bucket",
            "DOCS_PREFIX": "approved/",
            "EXTERNAL_ROLE_ARN": SETTINGS.role_arn,
            "EXTERNAL_ID": SETTINGS.external_id,
            "EXPECTED_GATEWAY_ID": SETTINGS.gateway_id,
            "GATEWAY_TARGET_NAME": SETTINGS.target_name,
        }
        settings = handler.Settings.from_env(values)
        self.assertEqual(settings.target_id, "")
        s3 = FakeS3({"approved/a.txt": b"hello"})
        result = handler.run_tool(
            {}, context(target_id="any-target"), settings=settings, s3=s3
        )
        self.assertTrue(result["ok"])
        for bad_context in (
            context(gateway_id="wrong"),
            context(tool="delete_document"),
        ):
            with self.assertRaises(handler.ToolError):
                handler.run_tool({}, bad_context, settings=settings, s3=s3)

    def test_configuration_normalizes_required_prefix(self):
        values = {
            "DOCS_BUCKET": "approved-bucket",
            "DOCS_PREFIX": "approved",
            "EXTERNAL_ROLE_ARN": SETTINGS.role_arn,
            "EXTERNAL_ID": SETTINGS.external_id,
            "EXPECTED_GATEWAY_ID": SETTINGS.gateway_id,
            "EXPECTED_TARGET_ID": SETTINGS.target_id,
            "GATEWAY_TARGET_NAME": SETTINGS.target_name,
        }
        self.assertEqual(handler.Settings.from_env(values).prefix, "approved/")
        values["DOCS_PREFIX"] = "/"
        with self.assertRaises(handler.ToolError):
            handler.Settings.from_env(values)

    def test_validation_happens_before_assume_role(self):
        calls = []

        def factory(settings, lambda_context, sts_client):
            calls.append((settings, lambda_context, sts_client))
            return FakeS3()

        with self.assertRaises(handler.ToolError):
            handler.run_tool(
                {"key": "../secret.md"},
                context("read_document"),
                settings=SETTINGS,
                sts_client=object(),
                s3_factory=factory,
            )
        self.assertFalse(calls)

    def test_assume_role_uses_pinned_role_and_external_id(self):
        sts = SimpleNamespace(
            assume_role=lambda **kwargs: {
                "Credentials": {
                    "AccessKeyId": "test",
                    "SecretAccessKey": "test",
                    "SessionToken": "test",
                }
            }
        )
        with (
            patch.object(sts, "assume_role", wraps=sts.assume_role) as assume,
            patch("boto3.Session") as session,
        ):
            handler._assumed_s3(SETTINGS, context(), sts)
        self.assertEqual(assume.call_args.kwargs["RoleArn"], SETTINGS.role_arn)
        self.assertEqual(assume.call_args.kwargs["ExternalId"], SETTINGS.external_id)
        self.assertEqual(assume.call_args.kwargs["DurationSeconds"], 900)
        session.assert_called_once()

    def test_session_name_is_bounded_and_traceable(self):
        name = handler._session_name(context())
        self.assertEqual(name, "symphony-lambda-request-123")
        self.assertLessEqual(len(name), 64)


if __name__ == "__main__":
    unittest.main()
