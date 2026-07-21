"""Host-side minisign verifier for dato install bundles.

Ports the canonicalize() + Ed25519 minisign verification logic from
dl-ota-watcher/dl_ota_watcher/signing.py so init scripts can run it
without a container. Uses the P7 signing.py minisign base64 alphabet;
the two verifiers MUST produce identical canonical forms.

MUST stay Python 3.10-compatible: scripts/init runs this on the HOST via
PYTHONPATH (the package is not installed), and the host preflight floor is
3.10 (spec §7.3/§5.3). Do NOT use 3.11+-only syntax/stdlib here.
"""

from __future__ import annotations

import json
from base64 import b64decode
from dataclasses import dataclass
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

_MINISIGN_ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789+/"
_STANDARD_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
_TRANSLATE_FROM_MINISIGN = str.maketrans(_MINISIGN_ALPHABET, _STANDARD_ALPHABET)

_ALG_ED = b"Ed"


def _minsig_decode(data: str) -> bytes:
    """Decode a minisign-base64 string to raw bytes."""
    std_b64 = data.translate(_TRANSLATE_FROM_MINISIGN)
    padding = 4 - len(std_b64) % 4
    if padding != 4:
        std_b64 += "=" * padding
    return b64decode(std_b64)


class ManifestVerifyError(Exception):
    """Raised when a manifest fails verification or parsing."""


@dataclass(frozen=True)
class VerifiedManifest:
    payload: dict
    signature_blob: str


def canonicalize(manifest_dict: dict) -> bytes:
    """Serialize a manifest envelope to canonical JSON for signing.

    Mirrors dl-ota-watcher/dl_ota_watcher/signing.py exactly:
      - signature field is set to "" before canonicalization
      - sorted keys, no insignificant whitespace, UTF-8, no BOM
    """
    working = dict(manifest_dict)
    working["signature"] = ""
    return json.dumps(working, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def parse_pubkey(pubkey_text: str) -> Ed25519PublicKey:
    """Parse a minisign-format pubkey. Accepts a bare minisign base64 line or
    the full .pub file format (untrusted-comment header + base64 line).

    Minisign pubkey blob layout (42 bytes):
        [2 bytes signature_algorithm = "Ed"]
        [8 bytes key_id]
        [32 bytes Ed25519 raw public key]
    """
    lines = [
        line.strip()
        for line in pubkey_text.strip().splitlines()
        if line.strip() and not line.startswith("untrusted comment:")
    ]
    if not lines:
        raise ManifestVerifyError("empty pubkey")
    raw = _minsig_decode(lines[0])
    if len(raw) != 42:
        raise ManifestVerifyError(f"expected 42-byte pubkey, got {len(raw)}")
    if raw[:2] != _ALG_ED:
        raise ManifestVerifyError(f"unsupported minisign algorithm: {raw[:2]!r} (expected b'Ed')")
    return Ed25519PublicKey.from_public_bytes(raw[10:42])


def parse_signature(sig_text: str) -> bytes:
    """Parse a minisign-format signature blob. Accepts the full multi-line
    format (untrusted-comment + sig + optional trusted-comment + global-sig)
    OR a bare minisign base64 line.

    Returns the 64-byte Ed25519 signature.

    Minisign signature blob layout (74 bytes):
        [2 bytes signature_algorithm = "Ed"]
        [8 bytes key_id]
        [64 bytes Ed25519 signature]
    """
    lines = [
        line.strip()
        for line in sig_text.strip().splitlines()
        if line.strip() and not line.startswith(("untrusted comment:", "trusted comment:"))
    ]
    if not lines:
        raise ManifestVerifyError("empty signature")
    raw = _minsig_decode(lines[0])
    if len(raw) < 10:
        raise ManifestVerifyError(f"expected >= 10-byte minisign signature, got {len(raw)}")
    if raw[:2] != _ALG_ED:
        raise ManifestVerifyError(f"unsupported minisign algorithm in signature: {raw[:2]!r}")
    sig = raw[10:74]
    if len(sig) != 64:
        raise ManifestVerifyError(f"expected 64-byte Ed25519 signature, got {len(sig)}")
    return sig


def _parse_pubkey_raw(pubkey_text: str) -> tuple[Ed25519PublicKey, bytes]:
    """Internal: parse pubkey returning (key, key_id)."""
    lines = [
        line.strip()
        for line in pubkey_text.strip().splitlines()
        if line.strip() and not line.startswith("untrusted comment:")
    ]
    if not lines:
        raise ManifestVerifyError("empty pubkey")
    raw = _minsig_decode(lines[0])
    if len(raw) != 42:
        raise ManifestVerifyError(f"expected 42-byte pubkey, got {len(raw)}")
    if raw[:2] != _ALG_ED:
        raise ManifestVerifyError(f"unsupported minisign algorithm: {raw[:2]!r} (expected b'Ed')")
    return Ed25519PublicKey.from_public_bytes(raw[10:42]), raw[2:10]


def _parse_signature_raw(sig_text: str) -> tuple[bytes, bytes]:
    """Internal: parse signature returning (ed25519_sig, key_id)."""
    lines = [
        line.strip()
        for line in sig_text.strip().splitlines()
        if line.strip() and not line.startswith(("untrusted comment:", "trusted comment:"))
    ]
    if not lines:
        raise ManifestVerifyError("empty signature")
    raw = _minsig_decode(lines[0])
    if len(raw) < 10:
        raise ManifestVerifyError(f"expected >= 10-byte minisign signature, got {len(raw)}")
    if raw[:2] != _ALG_ED:
        raise ManifestVerifyError(f"unsupported minisign algorithm in signature: {raw[:2]!r}")
    sig = raw[10:74]
    if len(sig) != 64:
        raise ManifestVerifyError(f"expected 64-byte Ed25519 signature, got {len(sig)}")
    return sig, raw[2:10]


def verify_manifest(envelope: dict, pubkey_text: str) -> VerifiedManifest:
    """Verify a manifest envelope against a minisign public key.

    The envelope must have ``payload`` and ``signature`` top-level keys.
    Verification canonicalizes the full envelope (with signature set to ""),
    matching P7's signing convention exactly.

    Returns a VerifiedManifest on success; raises ManifestVerifyError on
    any failure.
    """
    if "payload" not in envelope or "signature" not in envelope:
        raise ManifestVerifyError("envelope missing 'payload' or 'signature'")
    payload = envelope["payload"]
    if not isinstance(payload, dict):
        raise ManifestVerifyError("payload must be an object")
    signature_blob = envelope["signature"]

    canonical_bytes = canonicalize(envelope)
    pubkey, pk_key_id = _parse_pubkey_raw(pubkey_text)
    sig, sig_key_id = _parse_signature_raw(signature_blob)

    if pk_key_id != sig_key_id:
        raise ManifestVerifyError("key_id mismatch between public key and signature")

    try:
        pubkey.verify(sig, canonical_bytes)
    except InvalidSignature as exc:
        raise ManifestVerifyError("signature verification failed") from exc
    return VerifiedManifest(payload=payload, signature_blob=signature_blob)


def verify_manifest_file(envelope_path: Path | str, pubkey_path: Path | str) -> VerifiedManifest:
    """Convenience wrapper: read envelope JSON + pubkey from files."""
    envelope_path = Path(envelope_path)
    pubkey_path = Path(pubkey_path)
    envelope = json.loads(envelope_path.read_text("utf-8"))
    pubkey_text = pubkey_path.read_text("utf-8")
    return verify_manifest(envelope, pubkey_text)
