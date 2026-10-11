import json
import unittest
from pathlib import Path

import eval_harness

CASES = Path(__file__).resolve().parents[1] / "evals" / "harness_v1.json"


class GradeTest(unittest.TestCase):
    def test_passes_when_tools_and_facts_match(self):
        case = {
            "id": "a",
            "prompt": "p",
            "tools_any": ["read_document"],
            "include_any": ["1.5"],
        }
        result = eval_harness.grade(
            case, "Staff accrue 1.5 days.", ["docs___read_document"], []
        )
        self.assertEqual(result["status"], "PASS")

    def test_fails_with_reasons_for_each_missing_check(self):
        case = {
            "id": "a",
            "prompt": "p",
            "tools_any": ["code"],
            "include_all": ["South"],
            "exclude": ["CANARY"],
        }
        result = eval_harness.grade(
            case, "West had CANARY", ["docs___read_document"], []
        )
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(
            result["reasons"],
            [
                "expected a tool matching ['code']",
                "missing 'South'",
                "must not contain 'CANARY'",
            ],
        )

    def test_forbidden_tools_and_errors_fail(self):
        case = {"id": "a", "prompt": "p", "forbid_tools": ["shell"]}
        result = eval_harness.grade(case, "ok", ["shell_exec"], ["boom"])
        self.assertEqual(
            result["reasons"], ["used forbidden tool shell_exec", "stream error: boom"]
        )

    def test_matching_ignores_case(self):
        case = {"id": "a", "prompt": "p", "include_any": ["two weeks"]}
        self.assertEqual(
            eval_harness.grade(case, "Two Weeks notice", [], [])["status"], "PASS"
        )

    def test_suite_file_has_fifteen_cases_and_a_threshold(self):
        suite = json.loads(CASES.read_text())
        self.assertEqual(len(suite["cases"]), 15)
        self.assertEqual(len({c["id"] for c in suite["cases"]}), 15)
        self.assertEqual(suite["pass_threshold"], 13)

    def test_table_reports_the_score(self):
        rows = [
            {"id": "a", "status": "PASS", "reasons": [], "tools": ["x"], "seconds": 1.0}
        ]
        self.assertIn("1/1 passed", eval_harness.table(rows))


if __name__ == "__main__":
    unittest.main()
