import unittest

import tenant_ops


class FakeBedrock:
    def __init__(self, mode, sticky=False):
        self.mode = mode
        self.sticky = sticky
        self.puts = []

    def get_account_data_retention(self):
        return {"mode": self.mode}

    def put_account_data_retention(self, mode):
        self.puts.append(mode)
        if not self.sticky:
            self.mode = mode


class ZeroRetentionTest(unittest.TestCase):
    def test_sets_none_only_where_needed(self):
        clients = {
            "us-east-1": FakeBedrock("none"),
            "us-east-2": FakeBedrock("default"),
            "us-west-2": FakeBedrock("inherit"),
        }
        result = tenant_ops.ensure_zero_retention(clients.__getitem__)
        self.assertEqual(
            result, {"us-east-1": "none", "us-east-2": "none", "us-west-2": "none"}
        )
        self.assertEqual(clients["us-east-1"].puts, [])
        self.assertEqual(clients["us-east-2"].puts, ["none"])

    def test_fails_when_a_region_does_not_verify(self):
        clients = {"us-east-1": FakeBedrock("default", sticky=True)}
        with self.assertRaises(RuntimeError):
            tenant_ops.ensure_zero_retention(
                clients.__getitem__, regions=("us-east-1",)
            )


class FakeAgent:
    def __init__(self, statuses):
        self.statuses = list(statuses)

    def start_ingestion_job(self, **kwargs):
        return {
            "ingestionJob": {"ingestionJobId": "job-1", "status": self.statuses.pop(0)}
        }

    def get_ingestion_job(self, **kwargs):
        status = self.statuses.pop(0)
        job = {
            "ingestionJobId": "job-1",
            "status": status,
            "statistics": {"numberOfNewDocumentsIndexed": 4},
        }
        if status == "FAILED":
            job["failureReasons"] = ["AccessDenied on s3:GetObject"]
        return {"ingestionJob": job}


class KnowledgeBaseSyncTest(unittest.TestCase):
    def test_waits_until_complete(self):
        stats = tenant_ops.sync_knowledge_base(
            FakeAgent(["STARTING", "IN_PROGRESS", "COMPLETE"]),
            "KB",
            "DS",
            sleep=lambda _: None,
        )
        self.assertEqual(stats["numberOfNewDocumentsIndexed"], 4)

    def test_surfaces_failure_reasons(self):
        with self.assertRaises(RuntimeError) as caught:
            tenant_ops.sync_knowledge_base(
                FakeAgent(["STARTING", "FAILED"]), "KB", "DS", sleep=lambda _: None
            )
        self.assertIn("AccessDenied", str(caught.exception))


class FakeLogs:
    def __init__(self, groups):
        self.groups = groups
        self.retention = []
        self.kms = []

    def get_paginator(self, name):
        groups = self.groups

        class Paginator:
            def paginate(self, **kwargs):
                return [
                    {
                        "logGroups": [
                            g
                            for g in groups
                            if g["logGroupName"].startswith(
                                kwargs["logGroupNamePrefix"]
                            )
                        ]
                    }
                ]

        return Paginator()

    def put_retention_policy(self, **kwargs):
        self.retention.append(kwargs["logGroupName"])

    def associate_kms_key(self, **kwargs):
        self.kms.append(kwargs["logGroupName"])


class LockdownLogsTest(unittest.TestCase):
    def test_applies_retention_and_key_to_runtime_groups_only(self):
        logs = FakeLogs(
            [
                {"logGroupName": "/aws/bedrock-agentcore/runtimes/abc-DEFAULT"},
                {
                    "logGroupName": "/aws/bedrock-agentcore/runtimes/ok-DEFAULT",
                    "retentionInDays": 7,
                    "kmsKeyId": "arn:key",
                },
                {"logGroupName": "/symphony/demo/documents-tool"},
            ]
        )
        changed = tenant_ops.lockdown_runtime_logs(logs, "arn:key")
        self.assertEqual(len(changed), 2)
        self.assertEqual(
            logs.retention, ["/aws/bedrock-agentcore/runtimes/abc-DEFAULT"]
        )
        self.assertEqual(logs.kms, ["/aws/bedrock-agentcore/runtimes/abc-DEFAULT"])


if __name__ == "__main__":
    unittest.main()
