"""Pilot device PKI for the Jetson mTLS ingress.

The CA private key lives outside the repository (default ~/.symphony/pki/<tenant>/ca.key)
and never enters Git or Terraform state; only the public CA certificate is published
to the tenant truststore. Each Jetson creates its own key and CSR; the operator signs
it and records the serial in the tenant device registry, where the authorizer checks it.

    uv run --no-project --with 'boto3>=1.43.111' --with 'cryptography>=46' \\
        python cloud/services/agent/device_pki.py <command> [...]

Commands:
  create-ca TENANT                        Create the pilot CA (refuses to overwrite).
  make-csr DEVICE_ID OUT_DIR              Laptop stand-in for a Jetson: new key + CSR.
  enroll TENANT DEVICE_ID CSR OUT_CERT    Sign a CSR (1 year) and register the device.
  revoke TENANT SERIAL                    Mark a certificate serial revoked.
"""

from __future__ import annotations

import argparse
import datetime
import os
import re
import sys
from pathlib import Path
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

CA_DAYS = 5 * 365
DEVICE_DAYS = 365
DEVICE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")
REPO_PKI = Path(__file__).resolve().parents[2] / "pki"


def _utc(now: datetime.datetime | None) -> datetime.datetime:
    return (now or datetime.datetime.now(datetime.timezone.utc)).replace(microsecond=0)


def _iso(moment: datetime.datetime) -> str:
    return moment.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _key_usage(*, signing: bool) -> x509.KeyUsage:
    return x509.KeyUsage(
        digital_signature=not signing,
        content_commitment=False,
        key_encipherment=not signing,
        data_encipherment=False,
        key_agreement=False,
        key_cert_sign=signing,
        crl_sign=signing,
        encipher_only=False,
        decipher_only=False,
    )


def _write_private(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)


def _private_pem(key: rsa.RSAPrivateKey) -> bytes:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def default_key_path(tenant_id: str) -> Path:
    return Path.home() / ".symphony" / "pki" / tenant_id / "ca.key"


def default_cert_path(tenant_id: str) -> Path:
    return REPO_PKI / tenant_id / "ca.pem"


def create_ca(
    tenant_id: str, key_path: Path, cert_path: Path, *, now=None
) -> x509.Certificate:
    if key_path.exists():
        raise FileExistsError(f"{key_path} already exists; rotate deliberately instead")
    now = _utc(now)
    key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    name = x509.Name(
        [
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, tenant_id),
            x509.NameAttribute(
                NameOID.COMMON_NAME, f"Symphony {tenant_id} pilot device CA"
            ),
        ]
    )
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=CA_DAYS))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(_key_usage(signing=True), critical=True)
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False
        )
        .sign(key, hashes.SHA256())
    )
    _write_private(key_path, _private_pem(key))
    cert_path.parent.mkdir(parents=True, exist_ok=True)
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return cert


def make_device_csr(device_id: str) -> tuple[bytes, bytes]:
    """What the Jetson does on first boot: its key never leaves the device."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, device_id)]))
        .sign(key, hashes.SHA256())
    )
    return _private_pem(key), csr.public_bytes(serialization.Encoding.PEM)


def sign_device_csr(
    ca_key_path: Path,
    ca_cert_path: Path,
    csr_pem: bytes,
    device_id: str,
    tenant_id: str,
    *,
    now=None,
) -> x509.Certificate:
    if not DEVICE_ID.match(device_id):
        raise ValueError("device_id must be lowercase letters, digits, and dashes")
    try:
        csr = x509.load_pem_x509_csr(csr_pem)
        valid = csr.is_signature_valid
    except ValueError as error:
        raise ValueError(f"CSR is not valid: {error}") from error
    if not valid:
        raise ValueError("CSR signature does not verify")
    ca_key = serialization.load_pem_private_key(ca_key_path.read_bytes(), password=None)
    ca = x509.load_pem_x509_certificate(ca_cert_path.read_bytes())
    now = _utc(now)
    return (
        x509.CertificateBuilder()
        .subject_name(
            x509.Name(
                [
                    x509.NameAttribute(NameOID.ORGANIZATION_NAME, tenant_id),
                    x509.NameAttribute(NameOID.COMMON_NAME, device_id),
                ]
            )
        )
        .issuer_name(ca.subject)
        .public_key(csr.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=DEVICE_DAYS))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(_key_usage(signing=False), critical=True)
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]), critical=False
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca.public_key()),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )


def normalize_serial(serial: str) -> str:
    """Registry key: lowercase hex, no colons or leading zeros (matches the authorizer)."""
    return serial.replace(":", "").lower().lstrip("0") or "0"


def registry_item(
    cert: x509.Certificate, device_id: str, tenant_id: str, *, now=None
) -> dict[str, str]:
    return {
        "cert_serial": format(cert.serial_number, "x"),
        "device_id": device_id,
        "tenant_id": tenant_id,
        "fingerprint_sha256": cert.fingerprint(hashes.SHA256()).hex(),
        "status": "active",
        "not_after": _iso(cert.not_valid_after_utc),
        "enrolled_at": _iso(_utc(now)),
    }


def enroll_device(
    table: Any,
    ca_key_path: Path,
    ca_cert_path: Path,
    csr_pem: bytes,
    device_id: str,
    tenant_id: str,
    *,
    now=None,
) -> tuple[bytes, dict[str, str]]:
    cert = sign_device_csr(
        ca_key_path, ca_cert_path, csr_pem, device_id, tenant_id, now=now
    )
    item = registry_item(cert, device_id, tenant_id, now=now)
    table.put_item(Item=item, ConditionExpression="attribute_not_exists(cert_serial)")
    return cert.public_bytes(serialization.Encoding.PEM), item


def revoke_device(table: Any, serial: str, *, now=None) -> None:
    table.update_item(
        Key={"cert_serial": normalize_serial(serial)},
        UpdateExpression="SET #s = :revoked, revoked_at = :at",
        ConditionExpression="attribute_exists(cert_serial)",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":revoked": "revoked", ":at": _iso(_utc(now))},
    )


def _registry(tenant_id: str, region: str, assume_role_arn: str | None) -> Any:
    import boto3

    session = boto3.Session()
    if assume_role_arn:
        creds = session.client("sts").assume_role(
            RoleArn=assume_role_arn, RoleSessionName="symphony-device-pki"
        )["Credentials"]
        session = boto3.Session(
            aws_access_key_id=creds["AccessKeyId"],
            aws_secret_access_key=creds["SecretAccessKey"],
            aws_session_token=creds["SessionToken"],
        )
    return session.resource("dynamodb", region_name=region).Table(
        f"symphony-{tenant_id}-device-registry"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--assume-role-arn")
    parser.add_argument(
        "--ca-key", type=Path, help="Default: ~/.symphony/pki/<tenant>/ca.key"
    )
    parser.add_argument(
        "--ca-cert", type=Path, help="Default: cloud/pki/<tenant>/ca.pem"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("create-ca").add_argument("tenant")
    csr = sub.add_parser("make-csr")
    csr.add_argument("device_id")
    csr.add_argument("out_dir", type=Path)
    enroll = sub.add_parser("enroll")
    enroll.add_argument("tenant")
    enroll.add_argument("device_id")
    enroll.add_argument("csr", type=Path)
    enroll.add_argument("out_cert", type=Path)
    revoke = sub.add_parser("revoke")
    revoke.add_argument("tenant")
    revoke.add_argument("serial")
    args = parser.parse_args(argv)

    if args.command == "make-csr":
        key_pem, csr_pem = make_device_csr(args.device_id)
        _write_private(args.out_dir / "device.key", key_pem)
        (args.out_dir / "device.csr").write_bytes(csr_pem)
        print(args.out_dir / "device.csr")
        return 0

    tenant = args.tenant
    key_path = args.ca_key or default_key_path(tenant)
    cert_path = args.ca_cert or default_cert_path(tenant)
    if args.command == "create-ca":
        cert = create_ca(tenant, key_path, cert_path)
        print(f"CA certificate: {cert_path}\nCA key (keep offline): {key_path}")
        print(f"serial: {format(cert.serial_number, 'x')}")
    elif args.command == "enroll":
        table = _registry(tenant, args.region, args.assume_role_arn)
        cert_pem, item = enroll_device(
            table, key_path, cert_path, args.csr.read_bytes(), args.device_id, tenant
        )
        args.out_cert.write_bytes(cert_pem)
        print(
            f"enrolled {item['device_id']} serial={item['cert_serial']} not_after={item['not_after']}"
        )
    elif args.command == "revoke":
        revoke_device(_registry(tenant, args.region, args.assume_role_arn), args.serial)
        print(f"revoked {normalize_serial(args.serial)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
