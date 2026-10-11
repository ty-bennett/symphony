import unittest
from types import SimpleNamespace

from tools.knowledge_search import handler

SETTINGS = handler.Settings(
    knowledge_base_id="KB123",
    gateway_id="gateway-123",
    target_name="knowledge-search",
    source_prefix="approved-docs/",
)


def context(tool="search_documents", gateway_id="gateway-123"):
    return SimpleNamespace(
        client_context=SimpleNamespace(
            custom={
                "bedrockAgentCoreGatewayId": gateway_id,
                "bedrockAgentCoreToolName": f"knowledge-search___{tool}",
            }
        )
    )


class FakeRuntime:
    def __init__(self, results=None):
        self.calls = []
        self.results = results or []

    def retrieve(self, **kwargs):
        self.calls.append(kwargs)
        return {"retrievalResults": self.results}


class SearchTest(unittest.TestCase):
    def test_queries_only_the_configured_knowledge_base(self):
        client = FakeRuntime(
            [
                {
                    "content": {"text": "Requests need two weeks' notice."},
                    "location": {
                        "s3Location": {
                            "uri": "s3://b/approved-docs/handbook/pto-policy.md"
                        }
                    },
                    "score": 0.8,
                }
            ]
        )
        result = handler.search(
            {"query": "notice period", "max_results": 2},
            context(),
            settings=SETTINGS,
            client=client,
        )
        self.assertEqual(client.calls[0]["knowledgeBaseId"], "KB123")
        self.assertEqual(
            client.calls[0]["retrievalConfiguration"]["vectorSearchConfiguration"][
                "numberOfResults"
            ],
            2,
        )
        self.assertEqual(result["results"][0]["source"], "handbook/pto-policy.md")
        self.assertFalse(result["results"][0]["truncated"])

    def test_caps_passage_length(self):
        client = FakeRuntime(
            [
                {
                    "content": {"text": "x" * 5000},
                    "location": {"s3Location": {"uri": "s3://b/approved-docs/a.txt"}},
                }
            ]
        )
        result = handler.search(
            {"query": "x"}, context(), settings=SETTINGS, client=client
        )
        self.assertEqual(len(result["results"][0]["text"]), handler.MAX_PASSAGE_CHARS)
        self.assertTrue(result["results"][0]["truncated"])

    def test_rejects_model_supplied_knowledge_base_or_bad_bounds(self):
        client = FakeRuntime()
        for event in (
            {"query": "x", "knowledgeBaseId": "OTHER"},
            {"query": ""},
            {"query": "x" * 1001},
            {"query": "x", "max_results": 6},
            {"query": "x", "max_results": True},
            "query",
        ):
            with self.subTest(event=event), self.assertRaises(handler.ToolError):
                handler.search(event, context(), settings=SETTINGS, client=client)
        self.assertFalse(client.calls)

    def test_rejects_unapproved_gateway_and_tool(self):
        client = FakeRuntime()
        for bad in (context(gateway_id="attacker"), context(tool="delete_index")):
            with self.assertRaises(handler.ToolError):
                handler.search({"query": "x"}, bad, settings=SETTINGS, client=client)
        self.assertFalse(client.calls)

    def test_configuration_is_fail_closed(self):
        with self.assertRaises(handler.ToolError):
            handler.Settings.from_env({"KNOWLEDGE_BASE_ID": "KB123"})


if __name__ == "__main__":
    unittest.main()
