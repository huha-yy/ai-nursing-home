from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

logger = logging.getLogger(__name__)


class SecretsManager:
    """Unified secrets manager using HKDF-SHA256 and AES-256-GCM.

    Derived from Plan 14 design spec.
    """

    VERSION_V1 = 0x01
    INFO_V1 = b"dl_agents:secrets:v1"

    def __init__(
        self,
        device_secret_path: str | Path,
        device_id: str = "default-device-id",
    ) -> None:
        self._device_secret_path = Path(device_secret_path)
        self._device_id = device_id
        self._key_cache: dict[int, bytes] = {}
        self._persisted_keys: dict[int, bytes] = {}
        self._current_key_id = 1
        self._load_keys()

    def _keys_path(self) -> Path:
        """Path to the sidecar file that persists rotated derived keys."""
        return self._device_secret_path.parent / (self._device_secret_path.name + ".keys")

    def _load_keys(self) -> None:
        """Load previously persisted rotated keys from the sidecar file."""
        sidecar = self._keys_path()
        if not sidecar.exists():
            return
        try:
            data = json.loads(sidecar.read_text())
            for k, v in data.items():
                if k == "current":
                    self._current_key_id = v
                else:
                    self._persisted_keys[int(k)] = bytes.fromhex(v)
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning("Failed to load persisted keys from %s: %s", sidecar, exc)

    def _save_keys(self) -> None:
        """Persist rotated derived keys to the sidecar file.

        Only keys with key_id > 1 are persisted (key_id=1 is always
        derivable from the device secret at ``device_secret_path``).
        """
        sidecar = self._keys_path()
        data: dict[str, str | int] = {"current": self._current_key_id}
        for k, v in self._persisted_keys.items():
            data[str(k)] = v.hex()
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        sidecar.write_text(json.dumps(data))

    def _record_key(self, key_id: int, derived_key: bytes) -> None:
        """Persist a rotated derived key to the sidecar file and cache."""
        if key_id <= 1:
            return  # key_id=1 is always derivable from device_secret
        self._persisted_keys[key_id] = derived_key
        self._key_cache[key_id] = derived_key
        self._save_keys()

    def _get_key(self, key_id: int) -> bytes:
        if key_id in self._key_cache:
            return self._key_cache[key_id]

        # Check persisted rotated keys first
        if key_id in self._persisted_keys:
            self._key_cache[key_id] = self._persisted_keys[key_id]
            return self._key_cache[key_id]

        # Derive from device secret (used for key_id=1 and any
        # non-rotated keys derived from the original device secret)
        if not self._device_secret_path.exists():
            raise FileNotFoundError(f"Device secret not found at {self._device_secret_path}")

        device_secret = self._device_secret_path.read_bytes()

        info = self.INFO_V1 + key_id.to_bytes(4, "big")
        hkdf = HKDF(
            algorithm=SHA256(),
            length=32,
            salt=self._device_id.encode("utf-8"),
            info=info,
        )
        self._key_cache[key_id] = hkdf.derive(device_secret)

        return self._key_cache[key_id]

    def encrypt(self, plaintext: str | bytes, key_id: int | None = None) -> bytes:
        """AES-256-GCM encrypt. Returns packed blob [version:1B][key_id:4B][nonce:12B][ct]."""
        if key_id is None:
            key_id = self._current_key_id

        if isinstance(plaintext, str):
            plaintext = plaintext.encode("utf-8")

        key = self._get_key(key_id)
        nonce = os.urandom(12)
        aesgcm = AESGCM(key)

        header = bytes([self.VERSION_V1]) + key_id.to_bytes(4, "big")
        ciphertext = aesgcm.encrypt(nonce, plaintext, header)

        return header + nonce + ciphertext

    def decrypt(self, packed: bytes) -> str:
        """Decrypt packed blob. Rejects unknown key versions."""
        if len(packed) < 1 + 4 + 12 + 1:
            raise ValueError("Packed blob too short")

        version = packed[0]
        if version != self.VERSION_V1:
            raise ValueError(f"Unsupported encryption version: {version}")

        key_id = int.from_bytes(packed[1:5], "big")
        nonce = packed[5:17]
        ciphertext = packed[17:]

        key = self._get_key(key_id)
        aesgcm = AESGCM(key)

        try:
            plaintext = aesgcm.decrypt(nonce, ciphertext, packed[:5])
        except Exception as exc:
            raise ValueError("Decryption failed (bad key or tampered data)") from exc

        return plaintext.decode("utf-8")

    def rotate_keys(self, new_secret: bytes) -> dict[int, int]:
        """Derive new key, persist it, mark as current.

        Returns old_key_id -> new_key_id mapping.
        """
        old_key_id = self._current_key_id
        new_key_id = old_key_id + 1

        info = self.INFO_V1 + new_key_id.to_bytes(4, "big")
        hkdf = HKDF(
            algorithm=SHA256(),
            length=32,
            salt=self._device_id.encode("utf-8"),
            info=info,
        )
        new_key = hkdf.derive(new_secret)
        self._record_key(new_key_id, new_key)
        self._current_key_id = new_key_id
        self._save_keys()

        return {old_key_id: new_key_id}
