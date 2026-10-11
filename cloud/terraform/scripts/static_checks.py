#!/usr/bin/env python3
"""Offline static checks for cloud/terraform that terraform test cannot express.

Exits non-zero and prints every violation. Makes no network or AWS calls.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ACCOUNT_ID = re.compile(r"(?<![0-9])[0-9]{12}(?![0-9])")
PLACEHOLDER_ACCOUNT = re.compile(r"^([0-9])\1{11}$")
SCANNED_SUFFIXES = (".tf", ".tftest.hcl", ".tfvars.example", ".hcl.example")

FORBIDDEN = {
    "aws_iam_policy_document": "Build IAM policies with jsonencode so tests can inspect them.",
    'resource "aws_iam_user"': "Use IAM Identity Center; do not create IAM users.",
    'resource "aws_iam_access_key"': "Do not create long-lived access keys.",
}

PREVENT_DESTROY = r"prevent_destroy\s*=\s*true"

# (file relative to cloud/terraform, required regex, minimum matches, reason)
REQUIRED: list[tuple[str, str, int, str]] = [
    ("modules/secure-bucket/main.tf", PREVENT_DESTROY, 1, "Buckets must not be destroyed by Terraform."),
    ("bootstrap/state.tf", PREVENT_DESTROY, 1, "The state KMS key must not be destroyed."),
    ("bootstrap/organization.tf", PREVENT_DESTROY, 4, "Organization, Security OU, and shared accounts must not be destroyed."),
    (
        "bootstrap/organization.tf",
        r"ignore_changes\s*=\s*\[aws_service_access_principals,\s*enabled_policy_types\]",
        1,
        "Terraform must not remove trusted access or policy types that Control Tower enables.",
    ),
    ("landing-zone/kms.tf", PREVENT_DESTROY, 1, "The Control Tower log key must not be destroyed."),
    ("landing-zone/landing_zone.tf", PREVENT_DESTROY, 1, "The landing zone must not be destroyed."),
    ("organization/ous.tf", PREVENT_DESTROY, 1, "Workload OUs must not be destroyed."),
    ("modules/ct-account/main.tf", PREVENT_DESTROY, 1, "Vended accounts must not be terminated by Terraform."),
    ("shared-services/main.tf", PREVENT_DESTROY, 1, "The artifact key must not be destroyed."),
]

# Every root must refuse to plan against the wrong account or Region.
ROOTS: list[str] = [
    "bootstrap",
    "landing-zone",
    "organization",
    "shared-services",
]


def scanned_files() -> list[Path]:
    return sorted(
        path
        for path in ROOT.rglob("*")
        if path.is_file()
        and ".terraform" not in path.parts
        and (
            path.name.endswith(SCANNED_SUFFIXES)
            or (path.name == "terraform.tfvars" and path.parent.name == "tests")
        )
    )


def main() -> int:
    errors: list[str] = []
    for path in scanned_files():
        rel = path.relative_to(ROOT)
        text = path.read_text(encoding="utf-8")
        for match in ACCOUNT_ID.finditer(text):
            if not PLACEHOLDER_ACCOUNT.match(match.group(0)):
                line = text.count("\n", 0, match.start()) + 1
                errors.append(f"{rel}:{line}: real-looking AWS account ID; use a variable or remote state")
        for needle, reason in FORBIDDEN.items():
            if needle in text:
                errors.append(f"{rel}: contains {needle!r}. {reason}")
    for rel, pattern, minimum, reason in REQUIRED:
        path = ROOT / rel
        found = len(re.findall(pattern, path.read_text(encoding="utf-8"))) if path.is_file() else 0
        if found < minimum:
            errors.append(f"{rel}: found {found} of /{pattern}/, need {minimum}. {reason}")
    for root in ROOTS:
        guard = ROOT / root / "guard.tf"
        if not guard.is_file() or 'resource "terraform_data" "account_guard"' not in guard.read_text(encoding="utf-8"):
            errors.append(f"{root}/guard.tf: missing terraform_data.account_guard.")
    for error in errors:
        print(error, file=sys.stderr)
    print(f"static checks: {len(errors)} violation(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
