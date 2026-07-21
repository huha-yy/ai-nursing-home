"""P7 manifest — signed payload+signature envelope + verify gates."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx

from dl_ota_watcher.signing import InvalidSignatureError, verify_signature

ALLOWED_SERVICES = {
    "dato-control",
    "dl-cognee",
    "dl-llm-proxy",
    "dl-llm-local",
    "dl-egress-dns",
    "dl-ota-watcher",
    "dato-caddy",
    "openclaw",
}
MANIFEST_FORMAT_SUPPORTED = 1


class InvalidManifestError(ValueError):
    pass


@dataclass(frozen=True)
class ManifestService:
    image: str
    digest: str


@dataclass(frozen=True)
class ManifestMigration:
    name: str
    sha256: str
    sql: str


@dataclass(frozen=True)
class Manifest:
    version: str
    released_at: str
    min_appliance_version: str | None
    services: dict[str, ManifestService]
    target_data_schema: int
    migrations: list[ManifestMigration]


def _canonicalize_payload(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


async def fetch_manifest(channel_url: str, bearer_token: str) -> bytes:
    url = f"{channel_url.rstrip('/')}/manifests/dato/latest.json"
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(
            url,
            headers={"Authorization": f"Bearer {bearer_token}"},
        )
        r.raise_for_status()
        return r.content


def parse_and_verify(
    raw_bytes: bytes,
    pubkey_b64: str,
    *,
    current_data_schema: int,
    current_appliance_version: str,
) -> Manifest:
    try:
        envelope = json.loads(raw_bytes)
    except json.JSONDecodeError as e:
        raise InvalidManifestError(f"Envelope is not valid JSON: {e}") from e

    payload = envelope.get("payload")
    sig = envelope.get("signature")
    if not isinstance(payload, dict) or not isinstance(sig, str):
        raise InvalidManifestError("Envelope must have 'payload' (object) and 'signature' (string)")

    canonical = _canonicalize_payload(payload)
    if not verify_signature(canonical, sig, pubkey_b64):
        raise InvalidSignatureError("Manifest signature verification failed")

    if payload.get("manifest_format") != MANIFEST_FORMAT_SUPPORTED:
        raise InvalidManifestError(
            f"Unsupported manifest_format: {payload.get('manifest_format')!r}"
        )

    required = {"version", "released_at", "services", "target_data_schema", "migrations"}
    missing = required - payload.keys()
    if missing:
        raise InvalidManifestError(f"Missing required fields: {sorted(missing)}")

    services = _parse_services(payload["services"])
    migrations = _parse_migrations(payload.get("migrations", []))

    target_schema = int(payload["target_data_schema"])
    if target_schema < current_data_schema:
        raise InvalidManifestError(
            f"target_data_schema={target_schema} below current_data_schema={current_data_schema}"
        )

    min_app = payload.get("min_appliance_version")
    if min_app is not None and not isinstance(min_app, str):
        raise InvalidManifestError("min_appliance_version must be a string or null")
    if min_app is not None and _vt(min_app) > _vt(current_appliance_version):
        raise InvalidManifestError(
            f"min_appliance_version={min_app} requires running appliance "
            f">= it; current={current_appliance_version}"
        )

    return Manifest(
        version=payload["version"],
        released_at=payload["released_at"],
        min_appliance_version=min_app,
        services=services,
        target_data_schema=target_schema,
        migrations=migrations,
    )


def _parse_services(raw: Any) -> dict[str, ManifestService]:
    if not isinstance(raw, dict):
        raise InvalidManifestError("services must be a JSON object")
    unknown = set(raw.keys()) - ALLOWED_SERVICES
    if unknown:
        raise InvalidManifestError(f"unknown service key(s): {sorted(unknown)}")
    out: dict[str, ManifestService] = {}
    for name, body in raw.items():
        if not isinstance(body, dict):
            raise InvalidManifestError(f"service {name!r} entry must be an object")
        extra = set(body.keys()) - {"image", "digest"}
        if extra:
            raise InvalidManifestError(f"service {name!r} has unexpected keys: {sorted(extra)}")
        if not isinstance(body.get("image"), str) or not isinstance(body.get("digest"), str):
            raise InvalidManifestError(f"service {name!r} missing image/digest")
        if not body["digest"].startswith("sha256:") or len(body["digest"]) != 71:
            raise InvalidManifestError(
                f"service {name!r} digest must be 'sha256:<64-hex>' (got {body['digest']!r})"
            )
        out[name] = ManifestService(image=body["image"], digest=body["digest"])
    return out


def _parse_migrations(raw: Any) -> list[ManifestMigration]:
    if not isinstance(raw, list):
        raise InvalidManifestError("migrations must be a JSON array")
    out: list[ManifestMigration] = []
    for m in raw:
        if not isinstance(m, dict):
            raise InvalidManifestError("migration entries must be objects")
        if not all(k in m for k in ("name", "sha256", "sql")):
            raise InvalidManifestError("migration missing name/sha256/sql")
        out.append(ManifestMigration(name=m["name"], sha256=m["sha256"], sql=m["sql"]))
    return out


def should_apply(m: Manifest, *, current_version: str) -> bool:
    return _vt(m.version) > _vt(current_version)


def is_downgrade(m: Manifest, *, current_version: str) -> bool:
    return _vt(m.version) < _vt(current_version)


def _vt(v: str) -> tuple[int, ...]:
    try:
        return tuple(int(x) for x in v.split("."))
    except (ValueError, AttributeError):
        return (0,)
