import json
import unittest

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


if __name__ == "__main__":
    unittest.main()
