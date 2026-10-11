"""Unit tests for static_checks.py. Run: python3 -m unittest discover -s cloud/terraform/scripts -p 'test_*.py'"""

import shutil
import tempfile
import unittest
from pathlib import Path

import static_checks

SOURCE = Path(__file__).resolve().parent.parent


class StaticChecksTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / "terraform"
        shutil.copytree(SOURCE, self.root, ignore=shutil.ignore_patterns(".terraform", "__pycache__"))

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp)

    def edit(self, rel: str, old: str, new: str) -> None:
        path = self.root / rel
        text = path.read_text()
        self.assertIn(old, text)
        path.write_text(text.replace(old, new))

    def test_unmodified_tree_passes(self) -> None:
        self.assertEqual(static_checks.main(self.root), 0)

    def test_commented_out_prevent_destroy_fails(self) -> None:
        self.edit("modules/ct-account/main.tf", "prevent_destroy = true", "# prevent_destroy = true")
        self.assertEqual(static_checks.main(self.root), 1)

    def test_block_commented_prevent_destroy_fails(self) -> None:
        self.edit("modules/ct-account/main.tf", "prevent_destroy = true", "/* prevent_destroy = true */")
        self.assertEqual(static_checks.main(self.root), 1)

    def test_commented_out_ignore_changes_fails(self) -> None:
        self.edit("bootstrap/organization.tf", "    ignore_changes = [aws_service_access_principals", "    # ignore_changes = [aws_service_access_principals")
        self.assertEqual(static_checks.main(self.root), 1)

    def test_account_id_in_a_comment_still_fails(self) -> None:
        self.edit("modules/secure-bucket/outputs.tf", 'output "name" {', '# 123456789012\noutput "name" {')
        self.assertEqual(static_checks.main(self.root), 1)


if __name__ == "__main__":
    unittest.main()
