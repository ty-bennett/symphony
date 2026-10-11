import datetime
import stat
import tempfile
import unittest
from pathlib import Path

from cryptography import x509
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

import device_pki

NOW = datetime.datetime(2026, 10, 11, tzinfo=datetime.timezone.utc)


class FakeTable:
    def __init__(self):
        self.items = {}
        self.calls = []

    def put_item(self, Item, ConditionExpression):
        self.calls.append(("put", ConditionExpression))
        if Item["cert_serial"] in self.items:
            raise RuntimeError("ConditionalCheckFailedException")
        self.items[Item["cert_serial"]] = dict(Item)

    def update_item(
        self,
        Key,
        UpdateExpression,
        ConditionExpression,
        ExpressionAttributeValues,
        ExpressionAttributeNames=None,
    ):
        self.calls.append(("update", ConditionExpression))
        item = self.items.get(Key["cert_serial"])
        if item is None:
            raise RuntimeError("ConditionalCheckFailedException")
        item["status"] = ExpressionAttributeValues[":revoked"]
        item["revoked_at"] = ExpressionAttributeValues[":at"]


class PilotCaTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.key_path = self.tmp / "secret" / "ca.key"
        self.cert_path = self.tmp / "pki" / "ca.pem"

    def test_creates_a_ca_with_a_private_key_only_the_operator_can_read(self):
        device_pki.create_ca("demo", self.key_path, self.cert_path, now=NOW)
        ca = x509.load_pem_x509_certificate(self.cert_path.read_bytes())
        self.assertTrue(
            ca.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
        )
        self.assertTrue(
            ca.extensions.get_extension_for_class(x509.KeyUsage).value.key_cert_sign
        )
        self.assertEqual(
            ca.subject.get_attributes_for_oid(NameOID.ORGANIZATION_NAME)[0].value,
            "demo",
        )
        self.assertEqual(stat.S_IMODE(self.key_path.stat().st_mode), 0o600)
        self.assertNotIn(b"PRIVATE KEY", self.cert_path.read_bytes())

    def test_refuses_to_overwrite_an_existing_ca_key(self):
        device_pki.create_ca("demo", self.key_path, self.cert_path, now=NOW)
        with self.assertRaises(FileExistsError):
            device_pki.create_ca("demo", self.key_path, self.cert_path, now=NOW)


class EnrollmentTest(unittest.TestCase):
    def setUp(self):
        tmp = Path(tempfile.mkdtemp())
        self.key_path, self.cert_path = tmp / "ca.key", tmp / "ca.pem"
        device_pki.create_ca("demo", self.key_path, self.cert_path, now=NOW)
        self.device_key, self.csr_pem = device_pki.make_device_csr("jetson-01")
        self.table = FakeTable()

    def enroll(self, csr_pem=None, device_id="jetson-01"):
        return device_pki.enroll_device(
            self.table,
            self.key_path,
            self.cert_path,
            csr_pem or self.csr_pem,
            device_id,
            "demo",
            now=NOW,
        )

    def test_signs_a_one_year_client_auth_certificate_for_the_device(self):
        cert_pem, item = self.enroll()
        cert = x509.load_pem_x509_certificate(cert_pem)
        self.assertEqual(
            cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value,
            "jetson-01",
        )
        self.assertEqual(
            cert.not_valid_after_utc - cert.not_valid_before_utc,
            datetime.timedelta(days=365),
        )
        eku = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
        self.assertEqual(list(eku), [ExtendedKeyUsageOID.CLIENT_AUTH])
        self.assertFalse(
            cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
        )
        ca = x509.load_pem_x509_certificate(self.cert_path.read_bytes())
        cert.verify_directly_issued_by(ca)

    def test_records_an_active_registry_item_keyed_by_plain_hex_serial(self):
        cert_pem, item = self.enroll()
        cert = x509.load_pem_x509_certificate(cert_pem)
        self.assertEqual(item["cert_serial"], format(cert.serial_number, "x"))
        self.assertEqual(item["status"], "active")
        self.assertEqual(item["tenant_id"], "demo")
        self.assertEqual(item["device_id"], "jetson-01")
        self.assertEqual(item["not_after"], "2027-10-11T00:00:00Z")
        self.assertEqual(len(item["fingerprint_sha256"]), 64)
        self.assertEqual(
            self.table.calls, [("put", "attribute_not_exists(cert_serial)")]
        )

    def test_rejects_a_csr_whose_signature_does_not_verify(self):
        lines = self.csr_pem.splitlines()
        lines[5] = b"A" * len(lines[5])
        with self.assertRaises(ValueError):
            self.enroll(csr_pem=b"\n".join(lines) + b"\n")
        self.assertEqual(self.table.items, {})

    def test_rejects_a_device_id_that_is_not_a_simple_name(self):
        with self.assertRaises(ValueError):
            self.enroll(device_id="jetson-01,O=other")

    def test_revocation_marks_the_serial_revoked(self):
        _, item = self.enroll()
        device_pki.revoke_device(self.table, item["cert_serial"], now=NOW)
        self.assertEqual(self.table.items[item["cert_serial"]]["status"], "revoked")
        self.assertEqual(
            self.table.calls[-1], ("update", "attribute_exists(cert_serial)")
        )

    def test_revoking_an_unknown_serial_fails_loudly(self):
        with self.assertRaises(RuntimeError):
            device_pki.revoke_device(self.table, "abc123", now=NOW)


if __name__ == "__main__":
    unittest.main()
