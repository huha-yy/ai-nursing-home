"""Tier 1 DSN encryption — HKDF key derivation + AES-256-GCM packed format.

The encryption key is derived from the device secret + device ID using HKDF.
Encrypted DSNs are stored in a packed binary format in the
``tier1_agent_databases.encrypted_dsn`` BYTEA column.
"""

from __future__ import annotations

import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

# Info string for HKDF context separation
_INFO = b"dl_agents:tier1:dsn:v1"

# Packed format version byte
_VERSION_V1 = 0x01

# Key ID (4 bytes, big-endian) for v1
_KEY_ID_V1 = 0x00000001


def derive_key(device_secret: bytes, device_id: str) -> bytes:
    """Derive a 256-bit AES key from the device secret and device ID.

    Uses HKDF-SHA256 with:
    - IKM = raw device secret bytes
    - salt = device_id (as UTF-8 bytes)
    - info = ``"dl_agents:tier1:dsn:v1"``
    """
    hkdf = HKDF(
        algorithm=SHA256(),
        length=32,
        salt=device_id.encode("utf-8"),
        info=_INFO,
    )
    return hkdf.derive(device_secret)


def encrypt_dsn(plaintext: str, key: bytes) -> bytes:
    """Encrypt a DSN string using AES-256-GCM.

    Returns a packed binary blob:
    ``[version: 1B] [key_id: 4B BE] [nonce: 12B] [ciphertext + tag: NB]``
    """
    nonce = os.urandom(12)
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
    return bytes([_VERSION_V1]) + _KEY_ID_V1.to_bytes(4, "big") + nonce + ciphertext


def decrypt_dsn(packed: bytes, key: bytes) -> str:
    """Decrypt a packed DSN blob back to the original plaintext string.

    Raises ValueError if the packed format is invalid or the auth tag fails.
    """
    if len(packed) < 1 + 4 + 12 + 1:
        raise ValueError("Packed DSN too short")

    version = packed[0]
    if version != _VERSION_V1:
        raise ValueError(f"Unsupported DSN encryption version: {version}")

    key_id = int.from_bytes(packed[1:5], "big")
    if key_id != _KEY_ID_V1:
        raise ValueError(f"Unknown key ID: {key_id}")

    nonce = packed[5:17]
    ciphertext = packed[17:]

    aesgcm = AESGCM(key)
    try:
        plaintext = aesgcm.decrypt(nonce, ciphertext, None)
    except Exception as exc:
        raise ValueError("DSN decryption failed (bad key or tampered data)") from exc

    return plaintext.decode("utf-8")


async def reconcile_encrypted_dsns(pool, key: bytes) -> int:
    """Encrypt plaintext DSNs for any Tier 1 rows that lack an encrypted DSN.

    Returns the number of rows updated.
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, dsn FROM tier1_agent_databases "
            "WHERE encrypted_dsn IS NULL AND dsn IS NOT NULL"
        )
        updated = 0
        for row in rows:
            encrypted = encrypt_dsn(row["dsn"], key)
            await conn.execute(
                "UPDATE tier1_agent_databases SET encrypted_dsn = $1 WHERE id = $2",
                encrypted,
                row["id"],
            )
            updated += 1
        return updated
