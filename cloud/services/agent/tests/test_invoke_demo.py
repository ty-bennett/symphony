import io
import unittest

import invoke_demo


class RenderEventsTest(unittest.TestCase):
    def test_prints_text_tool_calls_and_stop_reason(self):
        events = [
            {
                "contentBlockStart": {
                    "start": {
                        "toolUse": {
                            "name": "customer-documents___list_documents",
                            "toolUseId": "t1",
                        }
                    }
                }
            },
            {"contentBlockDelta": {"delta": {"text": "The PTO policy "}}},
            {"contentBlockDelta": {"delta": {"text": "allows 1.5 days."}}},
            {"messageStop": {"stopReason": "end_turn"}},
            {"metadata": {"usage": {"inputTokens": 900, "outputTokens": 40}}},
        ]
        out = io.StringIO()
        summary = invoke_demo.render_events(events, out)
        text = out.getvalue()
        self.assertIn("[tool] customer-documents___list_documents", text)
        self.assertIn("The PTO policy allows 1.5 days.", text)
        self.assertEqual(summary["stop_reason"], "end_turn")
        self.assertEqual(summary["tools"], ["customer-documents___list_documents"])
        self.assertEqual(summary["usage"], {"inputTokens": 900, "outputTokens": 40})

    def test_surfaces_runtime_errors(self):
        out = io.StringIO()
        summary = invoke_demo.render_events(
            [{"runtimeClientError": {"message": "boom"}}], out
        )
        self.assertIn("[error] boom", out.getvalue())
        self.assertEqual(summary["errors"], ["boom"])

    def test_request_scopes_memory_and_overrides_model_only_when_asked(self):
        plain = invoke_demo.invoke_request("arn:h", "s" * 40, "hi")
        self.assertNotIn("actorId", plain)
        self.assertNotIn("model", plain)
        full = invoke_demo.invoke_request(
            "arn:h",
            "s" * 40,
            "hi",
            user="ty",
            model_id="us.anthropic.claude-opus-4-5-20251101-v1:0",
        )
        self.assertEqual(full["actorId"], "ty")
        self.assertEqual(
            full["model"],
            {
                "bedrockModelConfig": {
                    "modelId": "us.anthropic.claude-opus-4-5-20251101-v1:0"
                }
            },
        )

    def test_session_ids_meet_the_minimum_length(self):
        self.assertGreaterEqual(len(invoke_demo.new_session_id()), 33)


if __name__ == "__main__":
    unittest.main()
