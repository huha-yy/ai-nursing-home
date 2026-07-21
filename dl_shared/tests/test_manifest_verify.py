"""Tests for dl_shared/manifest_verify.py — host-side minisign verifier."""

from __future__ import annotations

import json
from base64 import b64decode, b64encode
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from dl_shared.manifest_verify import (
    ManifestVerifyError,
    VerifiedManifest,
    canonicalize,
    parse_pubkey,
    parse_signature,
    verify_manifest,
    verify_manifest_file,
)

_MINISIGN_ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789+/"
_STANDARD_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
_TRANSLATE_TO_MINISIGN = str.maketrans(_STANDARD_ALPHABET, _MINISIGN_ALPHABET)
_TRANSLATE_FROM_MINISIGN = str.maketrans(_MINISIGN_ALPHABET, _STANDARD_ALPHABET)


def _minsig_encode(data: bytes) -> str:
    return b64encode(data).decode("ascii").translate(_TRANSLATE_TO_MINISIGN).rstrip("=")


def _minsig_decode(data: str) -> bytes:
    std_b64 = data.translate(_TRANSLATE_FROM_MINISIGN)
    padding = 4 - len(std_b64) % 4
    if padding != 4:
        std_b64 += "=" * padding
    return b64decode(std_b64)


def _make_keypair() -> tuple[Ed25519PrivateKey, bytes, bytes]:
    """Generate a minisign-compatible keypair. Returns (private_key, pubkey_bytes, key_id)."""
    private_key = Ed25519PrivateKey.generate()
    pubkey_bytes = private_key.public_key().public_bytes_raw()
    import os

    key_id = os.urandom(8)
    return private_key, pubkey_bytes, key_id


def _make_pubkey_b64(pubkey_bytes: bytes, key_id: bytes) -> str:
    """Encode a minisign public key to base64."""
    raw = b"Ed" + key_id + pubkey_bytes
    return _minsig_encode(raw)


def _make_pubkey_file_text(pubkey_b64: str) -> str:
    """Full minisign .pub file content."""
    return f"untrusted comment: test keypair\n{pubkey_b64}\n"


def _sign_envelope(envelope: dict, private_key: Ed25519PrivateKey, key_id: bytes) -> str:
    """Sign the envelope and return the full minisign signature blob."""
    canonical_bytes = canonicalize(envelope)
    ed_sig = private_key.sign(canonical_bytes)
    raw = b"Ed" + key_id + ed_sig
    sig_b64 = _minsig_encode(raw)
    return f"untrusted comment: test signature\n{sig_b64}\n"


@pytest.fixture(scope="module")
def keypair() -> tuple[Ed25519PrivateKey, bytes, bytes]:
    return _make_keypair()


@pytest.fixture(scope="module")
def pubkey_b64(keypair) -> str:
    private_key, pubkey_bytes, key_id = keypair
    return _make_pubkey_b64(pubkey_bytes, key_id)


@pytest.fixture(scope="module")
def pubkey_file_text(keypair) -> str:
    private_key, pubkey_bytes, key_id = keypair
    return _make_pubkey_file_text(_make_pubkey_b64(pubkey_bytes, key_id))


@pytest.fixture
def signed_envelope(keypair) -> dict:
    private_key, pubkey_bytes, key_id = keypair
    envelope = {
        "payload": {
            "manifest_format": 1,
            "bundle_format": 1,
            "version": "2026.0.1",
            "source_commit": "a" * 40,
            "released_at": "2026-05-27T00:00:00Z",
            "placeholder": False,
            "min_appliance_version": None,
            "tarball_sha256": "sha256:deadbeef",
            "target_data_schema": 23,
            "services": {
                "openclaw": {
                    "image": "dato-openclaw:2026.0.1",
                    "compose_ref": "dato-openclaw:latest",
                    "image_id": "sha256:abc123",
                    "digest": "sha256:xyz789",
                }
            },
            "third_party": {},
        },
        "signature": "",
    }
    sig = _sign_envelope(envelope, private_key, key_id)
    envelope["signature"] = sig
    return envelope


# ---- canonicalize --------------------------------------------------------


def test_canonicalize_is_deterministic():
    obj = {"z": 1, "a": 2, "nested": {"b": 3, "a": 4}}
    c1 = canonicalize(obj)
    c2 = canonicalize(obj)
    assert c1 == c2


def test_canonicalize_sorts_keys():
    obj = {"z": 1, "a": 2}
    result = canonicalize(obj)
    assert result == b'{"a":2,"signature":"","z":1}'


def test_canonicalize_no_insignificant_whitespace():
    result = canonicalize({"a": 1})
    assert b" " not in result


def test_canonicalize_utf8():
    result = canonicalize({"key": "cafe"})
    assert isinstance(result, bytes)


def test_canonicalize_strips_signature():
    """Matching P7: signature is set to "" before canonicalization."""
    envelope = {"payload": {}, "signature": "some-sig"}
    result = canonicalize(envelope)
    decoded = json.loads(result)
    assert decoded["signature"] == ""


# ---- parse_pubkey --------------------------------------------------------


def test_parse_pubkey_bare_b64(pubkey_b64):
    pk = parse_pubkey(pubkey_b64)
    assert pk is not None


def test_parse_pubkey_full_file_format(pubkey_file_text):
    pk = parse_pubkey(pubkey_file_text)
    assert pk is not None


def test_parse_pubkey_empty():
    with pytest.raises(ManifestVerifyError):
        parse_pubkey("")


def test_parse_pubkey_wrong_algorithm():
    raw = b"XX" + b"\x00" * 40
    bad = _minsig_encode(raw)
    with pytest.raises(ManifestVerifyError):
        parse_pubkey(bad)


def test_parse_pubkey_truncated():
    raw = b"Ed" + b"\x00" * 5
    bad = _minsig_encode(raw)
    with pytest.raises(ManifestVerifyError):
        parse_pubkey(bad)


# ---- parse_signature ----------------------------------------------------


def test_parse_signature_bare_b64(keypair):
    private_key, _, key_id = keypair
    envelope = {"payload": {}, "signature": ""}
    canonical_bytes = canonicalize(envelope)
    ed_sig = private_key.sign(canonical_bytes)
    raw = b"Ed" + key_id + ed_sig
    sig_b64 = _minsig_encode(raw)
    result = parse_signature(sig_b64)
    assert len(result) == 64


def test_parse_signature_full_format(keypair):
    private_key, _, key_id = keypair
    envelope = {"payload": {}, "signature": ""}
    canonical_bytes = canonicalize(envelope)
    ed_sig = private_key.sign(canonical_bytes)
    raw = b"Ed" + key_id + ed_sig
    sig_b64 = _minsig_encode(raw)
    full = f"untrusted comment: test\n{sig_b64}\n"
    result = parse_signature(full)
    assert len(result) == 64


def test_parse_signature_skips_trusted_comment(keypair):
    private_key, _, key_id = keypair
    envelope = {"payload": {}, "signature": ""}
    canonical_bytes = canonicalize(envelope)
    ed_sig = private_key.sign(canonical_bytes)
    raw = b"Ed" + key_id + ed_sig
    sig_b64 = _minsig_encode(raw)
    full = f"untrusted comment: test\n{sig_b64}\ntrusted comment: test\n"
    result = parse_signature(full)
    assert len(result) == 64


def test_parse_signature_empty():
    with pytest.raises(ManifestVerifyError):
        parse_signature("")


def test_parse_signature_truncated():
    raw = b"Ed" + b"\x00" * 5
    bad = _minsig_encode(raw)
    with pytest.raises(ManifestVerifyError):
        parse_signature(bad)


# ---- verify_manifest -----------------------------------------------------


def test_verify_manifest_valid(signed_envelope, pubkey_b64):
    result = verify_manifest(signed_envelope, pubkey_b64)
    assert isinstance(result, VerifiedManifest)
    assert result.payload == signed_envelope["payload"]


def test_verify_manifest_tampered_payload(signed_envelope, pubkey_b64):
    tampered = json.loads(json.dumps(signed_envelope))
    tampered["payload"]["version"] = "9999.0.0"
    with pytest.raises(ManifestVerifyError):
        verify_manifest(tampered, pubkey_b64)


def test_verify_manifest_wrong_key(signed_envelope):
    _, wrong_pubkey_bytes, wrong_key_id = _make_keypair()
    wrong_pubkey_b64 = _make_pubkey_b64(wrong_pubkey_bytes, wrong_key_id)
    with pytest.raises(ManifestVerifyError):
        verify_manifest(signed_envelope, wrong_pubkey_b64)


def test_verify_manifest_missing_payload():
    with pytest.raises(ManifestVerifyError):
        verify_manifest({"signature": "x"}, "RWQ...")


def test_verify_manifest_missing_signature():
    with pytest.raises(ManifestVerifyError):
        verify_manifest({"payload": {}}, "RWQ...")


def test_verify_manifest_payload_not_dict():
    with pytest.raises(ManifestVerifyError):
        verify_manifest({"payload": "not-a-dict", "signature": "x"}, "RWQ...")


def test_verify_manifest_with_pubkey_file_format(signed_envelope, pubkey_file_text):
    result = verify_manifest(signed_envelope, pubkey_file_text)
    assert isinstance(result, VerifiedManifest)


# ---- verify_manifest_file --------------------------------------------------


def test_verify_manifest_file_from_disk(signed_envelope, pubkey_file_text):
    with TemporaryDirectory() as tmp:
        envelope_path = Path(tmp) / "manifest.json"
        pubkey_path = Path(tmp) / "minisign.pub"
        envelope_path.write_text(json.dumps(signed_envelope))
        pubkey_path.write_text(pubkey_file_text)
        result = verify_manifest_file(envelope_path, pubkey_path)
        assert isinstance(result, VerifiedManifest)


def test_verify_manifest_file_bad_json():
    with TemporaryDirectory() as tmp:
        envelope_path = Path(tmp) / "manifest.json"
        pubkey_path = Path(tmp) / "minisign.pub"
        envelope_path.write_text("not json")
        pubkey_path.write_text("untrusted comment: test\nRWQtest\n")
        with pytest.raises((json.JSONDecodeError, ManifestVerifyError)):
            verify_manifest_file(envelope_path, pubkey_path)
