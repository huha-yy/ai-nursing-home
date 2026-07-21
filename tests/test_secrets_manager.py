"""Tests for dl_shared.secrets.SecretsManager."""

import os
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def secrets_manager():
    from dl_shared.secrets import SecretsManager

    with tempfile.TemporaryDirectory() as tmpdir:
        secret_path = Path(tmpdir) / "device.secret"
        secret_path.write_bytes(os.urandom(32))
        manager = SecretsManager(str(secret_path), device_id="test-device")
        yield manager


def test_encrypt_decrypt_roundtrip(secrets_manager):
    plaintext = "hello, world"
    encrypted = secrets_manager.encrypt(plaintext)
    assert isinstance(encrypted, bytes)
    assert encrypted != plaintext.encode()

    decrypted = secrets_manager.decrypt(encrypted)
    assert decrypted == plaintext


def test_encrypt_different_outputs(secrets_manager):
    """Same plaintext should produce different ciphertexts (nonce randomness)."""
    c1 = secrets_manager.encrypt("test")
    c2 = secrets_manager.encrypt("test")
    assert c1 != c2


def test_decrypt_invalid_data(secrets_manager):
    with pytest.raises(ValueError):
        secrets_manager.decrypt(b"not-a-valid-blob")


def test_key_rotation(secrets_manager):
    plaintext = "before rotation"
    encrypted = secrets_manager.encrypt(plaintext)
    assert secrets_manager.decrypt(encrypted) == plaintext

    secrets_manager.rotate_keys(os.urandom(32))

    # Old ciphertext still decryptable with rotated key in sidecar
    assert secrets_manager.decrypt(encrypted) == plaintext

    # New encryption uses new key
    new_encrypted = secrets_manager.encrypt("after rotation")
    assert secrets_manager.decrypt(new_encrypted) == "after rotation"
