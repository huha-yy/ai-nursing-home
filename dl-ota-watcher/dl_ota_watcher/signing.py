"""Minisign signature verification — baked-in public key trust root.

The minisign public key is baked into the Docker image at build time and
is NOT overridable at runtime.  This module uses the ``cryptography``
library (already a dependency) for Ed25519 verification — no subprocess
calls, no CLI dependency.
"""

from __future__ import annotations

import json
from base64 import b64decode, b64encode

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

# Minisign uses a custom base64 alphabet:
#   abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789+/
# Standard base64:
#   ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/
_MINISIGN_ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789+/"
_STANDARD_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"

_TRANSLATE_TO_MINISIGN = str.maketrans(_STANDARD_ALPHABET, _MINISIGN_ALPHABET)
_TRANSLATE_FROM_MINISIGN = str.maketrans(_MINISIGN_ALPHABET, _STANDARD_ALPHABET)

# Algorithm tag for Ed25519 in minisign: "Ed" = 0x4564
_ALG_ED = b"Ed"

# Minisign context for key derivation / checksum
_CONTEXT = b"Minisign"


def _minsig_decode(data: str) -> bytes:
    """Decode a minisign-base64 string to raw bytes."""
    std_b64 = data.translate(_TRANSLATE_FROM_MINISIGN)
    padding = 4 - len(std_b64) % 4
    if padding != 4:
        std_b64 += "=" * padding
    return b64decode(std_b64)


def _minsig_encode(data: bytes) -> str:
    """Encode raw bytes to a minisign-base64 string (no padding)."""
    return b64encode(data).decode("ascii").translate(_TRANSLATE_TO_MINISIGN).rstrip("=")


def _parse_signature_line(signature_b64: str) -> tuple[bytes, bytes]:
    """Parse a minisign signature line.

    Returns (signature_bytes, key_id).
    """
    lines = [
        line.strip()
        for line in signature_b64.strip().splitlines()
        if line.strip() and not line.startswith(("untrusted comment:", "trusted comment:"))
    ]
    if not lines:
        raise InvalidSignatureError("empty signature")
    raw = _minsig_decode(lines[0])
    if len(raw) < 10:
        raise InvalidSignatureError("Signature line too short")
    if raw[:2] != _ALG_ED:
        raise InvalidSignatureError(f"Unknown algorithm: {raw[:2]!r}")
    key_id = raw[2:10]
    sig = raw[10:74]  # 64-byte Ed25519 signature
    if len(sig) != 64:
        raise InvalidSignatureError("Truncated Ed25519 signature")
    return sig, key_id


def _parse_public_key(pubkey_b64: str) -> tuple[Ed25519PublicKey, bytes]:
    """Parse a minisign public key into a cryptography key object.

    Public key format (42 bytes): sig_alg(2) + key_id(8) + pubkey(32).

    Returns (public_key, key_id).
    """
    lines = [
        line.strip()
        for line in pubkey_b64.strip().splitlines()
        if line.strip() and not line.startswith(("untrusted comment:", "trusted comment:"))
    ]
    if not lines:
        raise InvalidSignatureError("empty public key")
    raw = _minsig_decode(lines[0])
    if len(raw) != 42:
        raise InvalidSignatureError(f"Expected 42-byte minisign public key, got {len(raw)}")
    if raw[:2] != _ALG_ED:
        raise InvalidSignatureError(f"Unknown algorithm in public key: {raw[:2]!r}")
    key_id = raw[2:10]
    pk_bytes = raw[10:42]  # 32-byte Ed25519 public key
    pubkey = Ed25519PublicKey.from_public_bytes(pk_bytes)
    return pubkey, key_id


def verify_signature(
    payload: bytes,
    signature_b64: str,
    pubkey_b64: str,
) -> bool:
    """Verify a minisign detached signature.

    Args:
        payload: The canonical bytes that were signed.
        signature_b64: The minisign signature line (base64).
        pubkey_b64: The minisign public key line (base64).

    Returns True if the signature is valid, False otherwise.
    """
    try:
        pubkey, pk_key_id = _parse_public_key(pubkey_b64)
        sig, sig_key_id = _parse_signature_line(signature_b64)
    except (ValueError, InvalidSignatureError):
        return False

    if pk_key_id != sig_key_id:
        return False

    try:
        pubkey.verify(sig, payload)
        return True
    except InvalidSignature:
        return False


class InvalidSignatureError(ValueError):
    """Raised when a minisign signature or key is malformed."""


def canonicalize(manifest_dict: dict) -> bytes:
    """Serialize a manifest dict to canonical JSON for signing.

    - Strips the ``signature`` field, sets it to ``""``.
    - Serializes with sorted keys, no insignificant whitespace.
    - UTF-8 encoded, no BOM.
    """
    working = dict(manifest_dict)
    working["signature"] = ""
    return json.dumps(working, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )
