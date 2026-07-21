"""P7 — OTA update state machine.

Orchestrates the full OTA cycle: fetch manifest, verify, pull images,
apply migrations, recreate containers, roll openclaw, health check,
and handle self-swap of the watcher itself.

Every destructive Docker operation is gated by a journal save first.
"""

from __future__ import annotations

import asyncio
import contextlib
import enum
import json
import logging
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import aiodocker
import asyncpg
import httpx
import redis.asyncio as redis

from dl_ota_watcher import db, docker_ops, journal, licence, self_swap, signing, state_store
from dl_ota_watcher.manifest import (
    InvalidManifestError,
    Manifest,
    fetch_manifest,
    is_downgrade,
    parse_and_verify,
    should_apply,
)

logger = logging.getLogger(__name__)

_BUNDLE_PATH = "/app/secrets/.install-bundle.json"

# ---------------------------------------------------------------------------
# States
# ---------------------------------------------------------------------------


class UpdateState(enum.StrEnum):
    """String-based state enum for the OTA cycle."""

    IDLE = "idle"
    CHECKING = "checking"
    FETCHING = "fetching"
    PREFLIGHT = "preflight"
    MIGRATING = "migrating"
    APPLYING = "applying"
    ROLL_OPENCLAW = "roll_openclaw"
    HEALTH_CHECKING = "health_checking"
    COMMITTED_AWAITING_SELF_SWAP = "committed_awaiting_self_swap"
    COMMITTED = "committed"
    ROLLING_BACK = "rolling_back"
    ROLLED_BACK = "rolled_back"
    ROLLBACK_FAILED = "rollback_failed"
    SELF_SWAP_FAILED = "self_swap_failed"


_WATCHER_NAME = "dl-ota-watcher"
_OPENCLAW_NAME = "openclaw"

# Services excluded from the standard APPLYING recreate loop.
_SKIP_APPLY = {_OPENCLAW_NAME, _WATCHER_NAME}

# Redis channels
_REDIS_ROLL_RESULT = "dato:ota:roll-result"

# ---------------------------------------------------------------------------
# Audit posting
# ---------------------------------------------------------------------------


async def _post_audit(
    control_url: str,
    internal_api_key: str,
    action: str,
    ota_version: str,
    journal_snapshot: dict | None = None,
) -> None:
    """POST an audit event to dl-control.  Failures are logged, never raised."""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10)) as client:
            payload: dict = {
                "ota_version": ota_version,
                "state": action,
                "journal_snapshot": journal_snapshot or {},
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            await client.post(
                f"{control_url.rstrip('/')}/api/internal/ota/audit",
                json=payload,
                headers={"Authorization": f"Bearer {internal_api_key}"},
            )
    except Exception:
        logger.warning("audit post failed for action=%s", action, exc_info=True)


# ---------------------------------------------------------------------------
# Container digest helpers
# ---------------------------------------------------------------------------


async def _get_container_digest(
    docker,
    name: str,
    install_bundle: dict | None = None,
) -> str:
    """Extract the registry digest (sha256:...) of a running container's image.

    Falls back to the install-bundle snapshot when RepoDigests is empty
    (images loaded via ``docker load`` lose their registry digests).

    Returns ``"unknown"`` if the container or image cannot be inspected.
    """
    try:
        info = await docker_ops.introspect(docker, name)
        image_id = info["Image"]
        image_info = await docker.images.inspect(image_id)
        repo_digests: list[str] = image_info.get("RepoDigests") or []
        if repo_digests:
            _, _, digest = repo_digests[0].partition("@")
            if digest.startswith("sha256:"):
                return digest

        if install_bundle is not None:
            payload = install_bundle.get("payload")
            if isinstance(payload, dict):
                services = payload.get("services")
                if isinstance(services, dict):
                    entry = services.get(name)
                    if isinstance(entry, dict) and entry.get("image_id") == image_id:
                        digest = entry.get("digest", "")
                        if digest.startswith("sha256:"):
                            return digest
    except aiodocker.exceptions.DockerError:
        logger.debug("cannot inspect container %s", name, exc_info=True)
    return "unknown"


# ---------------------------------------------------------------------------
# Install-bundle helpers (post-pull retag)
# ---------------------------------------------------------------------------


def _read_install_bundle(*, bundle_path_override: str | None = None) -> dict | None:
    """Read the signed install-bundle snapshot. Returns None if absent or unreadable."""
    bundle_path = Path(bundle_path_override or _BUNDLE_PATH)
    if not bundle_path.exists():
        logger.warning("install-bundle snapshot absent — skipping retag")
        return None
    try:
        return json.loads(bundle_path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("cannot read install-bundle for retag: %s", exc)
        return None


async def _retag_from_bundle(docker, image_id: str, svc_name: str) -> None:
    """Retag a pulled image with its compose_ref from the install-bundle snapshot.

    Reads the install bundle, looks up *svc_name* in ``payload.services``,
    and calls :func:`docker_ops.tag_image` with the ``compose_ref`` if found.
    Logs warnings and skips gracefully when the bundle or field is missing.
    """
    bundle = _read_install_bundle()
    if bundle is None:
        return
    services = (bundle.get("payload") or {}).get("services") or {}
    svc_entry = services.get(svc_name)
    if not svc_entry or not isinstance(svc_entry, dict):
        logger.warning("service %r not in install-bundle — skipping retag", svc_name)
        return
    compose_ref = svc_entry.get("compose_ref")
    if not compose_ref or not isinstance(compose_ref, str):
        logger.warning("compose_ref missing for service %r — skipping retag", svc_name)
        return
    if compose_ref == svc_entry.get("image", ""):
        return
    await docker_ops.tag_image(docker, image_id, compose_ref)
    logger.info("retagged %s as %s", svc_name, compose_ref)


# ---------------------------------------------------------------------------
# Manifest helpers
# ---------------------------------------------------------------------------


async def _fetch_and_verify_manifest(
    settings,
    ota_state: state_store.OtaState,
    pool,
) -> tuple[Manifest, licence.Licence] | tuple[None, None]:
    """Fetch, parse and verify a manifest.  Returns (Manifest, Licence) or (None, None)."""
    lic = licence.load_licence(settings.licence_key_path)
    if not licence.is_valid(lic):
        logger.warning("licence expired — skipping poll cycle")
        await _post_audit(
            settings.control_url,
            settings.internal_api_key.get_secret_value() if settings.internal_api_key else "",
            "ota.check_failed",
            ota_state.current_version,
            journal_snapshot={"reason": "licence_expired"},
        )
        return None, None

    raw = await fetch_manifest(settings.channel_url, lic.manifest_token)
    current_schema = await db.get_current_schema_version(pool)
    manifest = parse_and_verify(
        raw,
        settings.minisign_pubkey,
        current_data_schema=current_schema,
        current_appliance_version=ota_state.current_version,
    )
    return manifest, lic


# ---------------------------------------------------------------------------
# Rollback
# ---------------------------------------------------------------------------


async def _run_rollback(
    docker,
    settings,
    j: journal.ApplyJournal,
    manifest: Manifest,
    journal_path: str | Path,
    ota_state: state_store.OtaState,
    ota_state_path: str | Path,
) -> None:
    """Revert applied services to their previous digests."""
    j.state = UpdateState.ROLLING_BACK.value
    await _post_audit(
        settings.control_url,
        settings.internal_api_key.get_secret_value() if settings.internal_api_key else "",
        "ota.rollback_started",
        j.manifest_version,
        journal_snapshot=j.to_dict(),
    )
    journal.save_journal(j, journal_path)

    try:
        for svc_name in reversed(j.applied_services):
            prev_digest = j.prev_digests.get(svc_name)
            if not prev_digest or prev_digest == "unknown":
                continue
            svc = manifest.services.get(svc_name)
            if svc is None:
                continue
            old_ref = f"{svc.image}@{prev_digest}"
            journal.save_journal(j, journal_path)
            try:
                pulled = await docker_ops.pull_image(docker, svc.image, prev_digest)
                await _retag_from_bundle(docker, pulled["Id"], svc_name)
            except aiodocker.exceptions.DockerError:
                logger.warning("rollback pull failed for %s, continuing", svc_name, exc_info=True)
            await docker_ops.recreate_service_container(
                docker,
                svc_name,
                new_image_ref=old_ref,
            )
            logger.info("rollback applied for %s -> %s", svc_name, old_ref)

        j.state = UpdateState.ROLLED_BACK.value
        journal.mark_rolled_back(j, journal_path)
        await _post_audit(
            settings.control_url,
            settings.internal_api_key.get_secret_value() if settings.internal_api_key else "",
            "ota.rollback_completed",
            j.manifest_version,
            journal_snapshot=j.to_dict(),
        )
    except aiodocker.exceptions.DockerError:
        logger.exception("rollback failed — entering rollback_failed state")
        j.state = UpdateState.ROLLBACK_FAILED.value
        journal.save_journal(j, journal_path)
        await _post_audit(
            settings.control_url,
            settings.internal_api_key.get_secret_value() if settings.internal_api_key else "",
            "ota.rollback_failed",
            j.manifest_version,
            journal_snapshot=j.to_dict() | {"reason": "docker_error_during_rollback"},
        )


# ---------------------------------------------------------------------------
# Redis helpers
# ---------------------------------------------------------------------------


async def _wait_for_roll_result(
    redis_client: redis.Redis,
    job_id: str,
    timeout_s: int = 300,
) -> dict:
    """Subscribe to :data:`_REDIS_ROLL_RESULT` and wait for the matching job_id message."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        pubsub = redis_client.pubsub()
        await pubsub.subscribe(_REDIS_ROLL_RESULT)
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break

            async def _next_message() -> dict:
                async for message in pubsub.listen():
                    if message and message.get("type") == "message":
                        data = message.get("data")
                        if isinstance(data, (bytes, str)):
                            data = json.loads(data)
                        if isinstance(data, dict) and data.get("job_id") == job_id:
                            return data

            return await asyncio.wait_for(_next_message(), timeout=remaining)
        except asyncio.TimeoutError:
            logger.error("timed out waiting for roll-openclaw result")
            return {"status": "timeout"}
        except (redis.exceptions.TimeoutError, redis.exceptions.ConnectionError):
            logger.warning("redis_roll_result_timeout — reconnecting")
            await asyncio.sleep(1)
        finally:
            await pubsub.unsubscribe(_REDIS_ROLL_RESULT)

    logger.error("deadline exceeded waiting for roll-openclaw result")
    return {"status": "timeout"}


# ---------------------------------------------------------------------------
# State machine core
# ---------------------------------------------------------------------------


async def run_state_machine(
    docker,
    settings,
    manifest: Manifest,
    licence_obj: licence.Licence,
    journal_path: str | Path,
    ota_state: state_store.OtaState,
    ota_state_path: str | Path,
    pool,
    redis_client: redis.Redis,
) -> None:
    """Drive the full OTA apply cycle.

    Writes the journal **before** every destructive step so a crashed
    watcher can resume from the last durable state.
    """
    control_url = settings.control_url
    internal_api_key = (
        settings.internal_api_key.get_secret_value() if settings.internal_api_key else ""
    )
    j = journal.create_journal(
        manifest.version,
        previous_digests={},
    )

    # ---- FETCHING ----------------------------------------------------------
    j.state = UpdateState.FETCHING.value
    await _post_audit(
        control_url,
        internal_api_key,
        "ota.fetch_started",
        j.manifest_version,
        journal_snapshot=j.to_dict(),
    )
    journal.save_journal(j, journal_path)

    for svc_name, svc in manifest.services.items():
        pulled = await docker_ops.pull_image(docker, svc.image, svc.digest)
        await _retag_from_bundle(docker, pulled["Id"], svc_name)
        logger.info("pulled %s:%s", svc.image, svc.digest)
    logger.info("all images pulled for version=%s", manifest.version)

    # ---- PREFLIGHT ---------------------------------------------------------
    j.state = UpdateState.PREFLIGHT.value
    await _post_audit(
        control_url,
        internal_api_key,
        "ota.preflight",
        j.manifest_version,
        journal_snapshot=j.to_dict(),
    )
    journal.save_journal(j, journal_path)

    j.prev_digests = {}
    j.target_digests = {}
    install_bundle = _read_install_bundle()
    for svc_name in manifest.services:
        j.prev_digests[svc_name] = await _get_container_digest(docker, svc_name, install_bundle)
        j.target_digests[svc_name] = manifest.services[svc_name].digest
    journal.save_journal(j, journal_path)

    # ---- MIGRATING ---------------------------------------------------------
    j.state = UpdateState.MIGRATING.value
    await _post_audit(
        control_url,
        internal_api_key,
        "ota.migrate_started",
        j.manifest_version,
        journal_snapshot=j.to_dict(),
    )
    journal.save_journal(j, journal_path)

    # MIGRATING uses the owner DSN pool so DDL grants are available.
    # The app DSN pool has only DML grants on watcher tables.
    # Only create the owner pool when there are actual migrations to run.
    if manifest.migrations or (
        manifest.target_data_schema > await db.get_current_schema_version(pool)
    ):
        owner_pool = await _create_owner_pool(settings)
        try:
            for mig in manifest.migrations:
                already = await db.is_migration_applied(pool, mig.name, mig.sha256)
                if not already:
                    await db.run_migration(owner_pool, mig.sql, mig.name)
                    await db.record_migration(owner_pool, mig.name, mig.sha256)
                    logger.info("migration %s applied", mig.name)

            if manifest.target_data_schema > await db.get_current_schema_version(pool):
                await db.set_schema_version(owner_pool, manifest.target_data_schema)
        finally:
            await owner_pool.close()

    # ---- APPLYING ----------------------------------------------------------
    j.state = UpdateState.APPLYING.value
    await _post_audit(
        control_url,
        internal_api_key,
        "ota.apply_started",
        j.manifest_version,
        journal_snapshot=j.to_dict(),
    )
    journal.save_journal(j, journal_path)

    for svc_name, svc in manifest.services.items():
        if svc_name in _SKIP_APPLY:
            continue
        new_ref = f"{svc.image}@{svc.digest}"
        journal.save_journal(j, journal_path)
        await docker_ops.recreate_service_container(
            docker,
            svc_name,
            new_image_ref=new_ref,
        )
        j.applied_services.append(svc_name)
        logger.info("recreated %s -> %s", svc_name, new_ref)
        journal.save_journal(j, journal_path)

    # Wait for dato-control to become healthy before roll-openclaw
    if "dato-control" in j.applied_services:
        ok = await docker_ops.wait_for_health(
            docker,
            "dato-control",
            timeout_s=60,
        )
        if not ok:
            logger.error("dato-control health check failed after apply — rolling back")
            await _run_rollback(
                docker,
                settings,
                j,
                manifest,
                journal_path,
                ota_state,
                ota_state_path,
            )
            return

    # ---- ROLL OPENCLAW -----------------------------------------------------
    openclaw_svc = manifest.services.get(_OPENCLAW_NAME)
    if openclaw_svc is not None:
        openclaw_new_digest = openclaw_svc.digest
        if openclaw_new_digest != ota_state.current_openclaw_digest:
            j.state = UpdateState.ROLL_OPENCLAW.value
            await _post_audit(
                control_url,
                internal_api_key,
                "ota.roll_openclaw_started",
                j.manifest_version,
                journal_snapshot=j.to_dict(),
            )
            journal.save_journal(j, journal_path)

            try:
                job_id = str(uuid.uuid4())
                roll_payload = {
                    "job_id": job_id,
                    "ota_version": manifest.version,
                    "target_digest": openclaw_new_digest,
                    "mode": "apply",
                }
                j.job_id = job_id
                journal.save_journal(j, journal_path)
                async with httpx.AsyncClient(timeout=httpx.Timeout(30)) as client:
                    r = await client.post(
                        f"{control_url.rstrip('/')}/api/internal/ota/roll-openclaw",
                        json=roll_payload,
                        headers={"Authorization": f"Bearer {internal_api_key}"},
                    )
                    r.raise_for_status()

                # Wait for the result on Redis
                result = await _wait_for_roll_result(
                    redis_client,
                    job_id=j.job_id,
                    timeout_s=settings.health_window_seconds,
                )

                if result.get("status") == "committed":
                    j.openclaw_committed = True
                    j.openclaw_prev_digest = result.get("representative_prev_digest")
                    ota_state.current_openclaw_digest = openclaw_new_digest
                    state_store.save_state(ota_state, ota_state_path)
                    journal.save_journal(j, journal_path)
                    openclaw_info = await docker.images.inspect(
                        f"{openclaw_svc.image}@{openclaw_new_digest}"
                    )
                    await _retag_from_bundle(
                        docker,
                        openclaw_info["Id"],
                        _OPENCLAW_NAME,
                    )
                    logger.info("openclaw roll committed (%s)", openclaw_new_digest)
                else:
                    logger.error(
                        "openclaw roll failed status=%s — rolling back",
                        result.get("status"),
                    )
                    await _run_rollback(
                        docker,
                        settings,
                        j,
                        manifest,
                        journal_path,
                        ota_state,
                        ota_state_path,
                    )
                    return
            except httpx.HTTPError:
                logger.exception("openclaw roll HTTP call failed — rolling back")
                await _run_rollback(
                    docker,
                    settings,
                    j,
                    manifest,
                    journal_path,
                    ota_state,
                    ota_state_path,
                )
                return

    # ---- HEALTH CHECKING ---------------------------------------------------
    j.state = UpdateState.HEALTH_CHECKING.value
    await _post_audit(
        control_url,
        internal_api_key,
        "ota.health_check_started",
        j.manifest_version,
        journal_snapshot=j.to_dict(),
    )
    journal.save_journal(j, journal_path)

    for svc_name in j.applied_services:
        ok = await docker_ops.wait_for_health(
            docker,
            svc_name,
            timeout_s=settings.health_window_seconds,
        )
        if not ok:
            logger.error("health check failed for %s — rolling back", svc_name)
            await _run_rollback(
                docker,
                settings,
                j,
                manifest,
                journal_path,
                ota_state,
                ota_state_path,
            )
            return

    # Also health-check openclaw if it was tracked
    if j.openclaw_committed and _OPENCLAW_NAME in manifest.services:
        ok = await docker_ops.wait_for_health(
            docker,
            _OPENCLAW_NAME,
            timeout_s=settings.health_window_seconds,
        )
        if not ok:
            logger.error("openclaw health check failed — rolling back")
            await _run_rollback(
                docker,
                settings,
                j,
                manifest,
                journal_path,
                ota_state,
                ota_state_path,
            )
            return

    # ---- COMMITTED / SELF-SWAP ----------------------------------------------
    watcher_svc = manifest.services.get(_WATCHER_NAME)
    if watcher_svc is not None:
        current_digest = await docker_ops.get_self_image_digest(docker)
        if current_digest is None or watcher_svc.digest != current_digest:
            j.state = UpdateState.COMMITTED_AWAITING_SELF_SWAP.value
            await _post_audit(
                control_url,
                internal_api_key,
                "ota.committed_awaiting_self_swap",
                j.manifest_version,
                journal_snapshot=j.to_dict(),
            )
            journal.save_journal(j, journal_path)

            new_ref = f"{watcher_svc.image}@{watcher_svc.digest}"
            try:
                await self_swap.do_old_side_self_swap(
                    docker,
                    new_image_ref=new_ref,
                    own_container_name=_WATCHER_NAME,
                    journal=j,
                )
                journal.save_journal(j, journal_path)
                logger.info("self-swap successor launched: %s", j.successor_name)
                sys.exit(0)
            except Exception:
                logger.exception("self-swap failed")
                ota_state.record_self_swap_failure(watcher_svc.digest)
                state_store.save_state(ota_state, ota_state_path)
                j.state = UpdateState.SELF_SWAP_FAILED.value
                journal.save_journal(j, journal_path)
                await _post_audit(
                    control_url,
                    internal_api_key,
                    "ota.self_swap_failed",
                    j.manifest_version,
                    journal_snapshot=j.to_dict() | {"reason": "self_swap_exception"},
                )
                return

    # ---- COMMITTED ---------------------------------------------------------
    j.state = UpdateState.COMMITTED.value
    ota_state.current_version = manifest.version
    state_store.save_state(ota_state, ota_state_path)
    journal.mark_committed(j, journal_path)
    await _post_audit(
        control_url,
        internal_api_key,
        "ota.committed",
        j.manifest_version,
        journal_snapshot=j.to_dict(),
    )
    logger.info("OTA update committed — version=%s", manifest.version)


# ---------------------------------------------------------------------------
# Poll entry point
# ---------------------------------------------------------------------------


async def run_poll(
    docker,
    settings,
    journal_path: str | Path,
    ota_state: state_store.OtaState,
    ota_state_path: str | Path,
    pool,
    redis_client: redis.Redis,
) -> None:
    """Main poll entry point — called from the poll loop.

    1. Load licence, check validity.
    2. Fetch manifest from the configured channel.
    3. Parse and verify (signature, schema, appliance version).
    4. Check ``should_apply`` and self-swap suppression gate.
    5. Dispatch to :func:`run_state_machine`.
    """
    control_url = settings.control_url
    internal_api_key = (
        settings.internal_api_key.get_secret_value() if settings.internal_api_key else ""
    )

    try:
        manifest, lic = await _fetch_and_verify_manifest(settings, ota_state, pool)
        if manifest is None or lic is None:
            return

        # Already at or above the target version?
        if not should_apply(manifest, current_version=ota_state.current_version):
            if is_downgrade(manifest, current_version=ota_state.current_version):
                await _post_audit(
                    control_url,
                    internal_api_key,
                    "ota.check_skipped",
                    ota_state.current_version,
                    journal_snapshot={"reason": "downgrade_detected"},
                )
            return

        # Self-swap suppression gate
        watcher_svc = manifest.services.get(_WATCHER_NAME)
        if watcher_svc is not None and ota_state.is_self_swap_suppressed(watcher_svc.digest):
            logger.warning(
                "self-swap suppressed for digest=%s",
                watcher_svc.digest,
            )
            await _post_audit(
                control_url,
                internal_api_key,
                "ota.check_skipped",
                ota_state.current_version,
                journal_snapshot={
                    "reason": "self_swap_suppressed",
                    "digest": watcher_svc.digest,
                },
            )
            return

        await run_state_machine(
            docker,
            settings,
            manifest,
            lic,
            journal_path,
            ota_state,
            ota_state_path,
            pool,
            redis_client,
        )

    except signing.InvalidSignatureError:
        logger.exception("manifest signature verification failed")
        await _post_audit(
            control_url,
            internal_api_key,
            "ota.check_failed",
            ota_state.current_version,
            journal_snapshot={"reason": "signature_failed"},
        )

    except InvalidManifestError:
        logger.exception("manifest validation failed")
        await _post_audit(
            control_url,
            internal_api_key,
            "ota.check_failed",
            ota_state.current_version,
            journal_snapshot={"reason": "invalid_manifest"},
        )

    except httpx.RequestError:
        logger.warning("HTTP error during poll — skipping cycle", exc_info=True)

    except aiodocker.exceptions.DockerError:
        logger.exception("Docker error during poll — will retry next cycle")

    except Exception:
        logger.exception("unexpected error in poll cycle")


# ---------------------------------------------------------------------------
# First-run install bundle bootstrap
# ---------------------------------------------------------------------------


def _version_lt(a: str, b: str) -> bool:
    """Return True if dotted-integer version a is strictly less than b."""

    def _parse(v: str) -> tuple[int, ...]:
        try:
            return tuple(int(x) for x in v.split("."))
        except (ValueError, AttributeError):
            return (0,)

    return _parse(a) < _parse(b)


def _extract_sig_line(signature_blob: str) -> str:
    """Extract the first non-comment base64 line from a minisign signature blob."""
    for line in signature_blob.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("untrusted comment:"):
            continue
        return stripped
    return ""


async def _maybe_bootstrap_from_bundle(
    state: state_store.OtaState,
    state_path: Path,
    *,
    minisign_pubkey: str = "",
    bundle_path_override: str | None = None,
) -> None:
    """At boot, seed OtaState from a signed install-bundle snapshot if this is a first run
    or the bundle version is newer than the current state."""
    bundle_path = Path(bundle_path_override or "/app/secrets/.install-bundle.json")
    if not bundle_path.exists():
        return

    envelope: dict = {}
    try:
        envelope = json.loads(bundle_path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("install-bundle bootstrap: cannot read bundle: %s", exc)
        return

    payload = envelope.get("payload")
    signature = envelope.get("signature", "")
    if not isinstance(payload, dict) or not signature:
        logger.warning("install-bundle bootstrap: malformed envelope")
        return

    try:
        canonical_bytes = signing.canonicalize(envelope)
        sig_line = _extract_sig_line(signature)
        if not sig_line or not signing.verify_signature(canonical_bytes, sig_line, minisign_pubkey):
            logger.warning("install-bundle bootstrap: signature verification failed")
            return
    except Exception as exc:
        logger.warning("install-bundle bootstrap verify failed: %s", exc)
        return

    if payload.get("manifest_format") != 1 or payload.get("bundle_format") != 1:
        logger.warning("install-bundle bootstrap: bad format")
        return
    if payload.get("placeholder") is True:
        logger.warning("install-bundle bootstrap: placeholder bundle, skipping")
        return

    bundle_version = str(payload["version"])
    if _version_lt(state.current_version, bundle_version):
        openclaw_digest = None
        services = payload.get("services", {})
        if isinstance(services, dict) and "openclaw" in services:
            openclaw_entry = services["openclaw"]
            if isinstance(openclaw_entry, dict):
                openclaw_digest = openclaw_entry.get("digest")
        old_version = state.current_version
        state.current_version = bundle_version
        state.current_openclaw_digest = openclaw_digest
        state_store.save_state(state, state_path)
        logger.info(
            "install-bundle bootstrap: version %s -> %s",
            old_version,
            bundle_version,
        )


# ---------------------------------------------------------------------------
# Resume from journal (startup recovery)
# ---------------------------------------------------------------------------


async def resume_from_journal(
    docker,
    settings,
    journal_path: str | Path,
    ota_state: state_store.OtaState,
    ota_state_path: str | Path,
    pool,
    redis_client: redis.Redis,
) -> None:
    """Called at startup.  If a durable journal exists, resume from its state.

    Resumption table:

    ===============================================  ==========================================
    Journal state                                     Resume action
    ===============================================  ==========================================
    idle / checking / fetching / preflight            Discard journal; return to IDLE.
    migrating                                         Re-run migrations (idempotent).
    applying                                          Re-run per-service recreate (idempotent).
    roll_openclaw                                     Re-poll job status; derive outcome.
    health_checking                                   Re-run health check.
    rolling_back                                      Re-run rollback (idempotent).
    committed_awaiting_self_swap                      Compare running digest to target; do
                                                      takeover or retry swap.
    committed / rolled_back / rollback_failed /        Terminal — clear journal.
    self_swap_failed
    ===============================================  ==========================================
    """
    j = journal.load_journal(journal_path)
    if j is None:
        return

    logger.info("resuming from journal state=%s update_id=%s", j.state, j.update_id)

    try:
        match j.state:
            case "idle" | "checking" | "fetching" | "preflight":
                _discard_journal(journal_path)

            case "migrating":
                await _resume_migrating(
                    docker,
                    settings,
                    j,
                    journal_path,
                    ota_state,
                    ota_state_path,
                    pool,
                    redis_client,
                )

            case "applying":
                await _resume_applying(
                    docker,
                    settings,
                    j,
                    journal_path,
                    ota_state,
                    ota_state_path,
                    pool,
                    redis_client,
                )

            case "roll_openclaw":
                await _resume_roll_openclaw(
                    docker,
                    settings,
                    j,
                    journal_path,
                    ota_state,
                    ota_state_path,
                    pool,
                    redis_client,
                )

            case "health_checking":
                await _resume_health_checking(
                    docker,
                    settings,
                    j,
                    journal_path,
                    ota_state,
                    ota_state_path,
                    pool,
                    redis_client,
                )

            case "rolling_back":
                await _resume_rolling_back(
                    docker,
                    settings,
                    j,
                    journal_path,
                    ota_state,
                    ota_state_path,
                    pool,
                )

            case "committed_awaiting_self_swap":
                await _resume_self_swap(
                    docker,
                    settings,
                    j,
                    journal_path,
                    ota_state,
                    ota_state_path,
                )

            case "committed" | "rolled_back" | "rollback_failed" | "self_swap_failed":
                _discard_journal(journal_path)

            case _:
                logger.warning(
                    "unknown journal state=%s — discarding journal",
                    j.state,
                )
                _discard_journal(journal_path)

    except aiodocker.exceptions.DockerError:
        logger.exception("Docker error during resume from state=%s", j.state)
    except Exception:
        logger.exception("unexpected error during resume from state=%s", j.state)


def _discard_journal(journal_path: str | Path) -> None:
    path = Path(journal_path)
    with contextlib.suppress(OSError):
        path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Resume helpers
# ---------------------------------------------------------------------------


async def _refetch_manifest_for_resume(
    settings,
    ota_state: state_store.OtaState,
    pool,
) -> Manifest | None:
    """Re-fetch and verify the manifest during resume.  Returns None on failure."""
    try:
        lic = licence.load_licence(settings.licence_key_path)
        if not licence.is_valid(lic):
            return None
        raw = await fetch_manifest(settings.channel_url, lic.manifest_token)
        current_schema = await db.get_current_schema_version(pool)
        return parse_and_verify(
            raw,
            settings.minisign_pubkey,
            current_data_schema=current_schema,
            current_appliance_version=ota_state.current_version,
        )
    except Exception:
        logger.warning("manifest re-fetch failed during resume", exc_info=True)
        return None


async def _create_owner_pool(settings) -> asyncpg.Pool:
    """Create a temporary owner-DSN pool for the MIGRATING phase.
    The owner role has DDL grants that the app role lacks."""
    owner_pool = await asyncpg.create_pool(
        settings.owner_dsn.get_secret_value(),
        min_size=1,
        max_size=1,
    )
    assert owner_pool is not None
    return owner_pool


async def _resume_migrating(
    docker,
    settings,
    j: journal.ApplyJournal,
    journal_path: str | Path,
    ota_state: state_store.OtaState,
    ota_state_path: str | Path,
    pool,
    redis_client: redis.Redis,
) -> None:
    """Re-run migrations (idempotent), then dispatch to the next state."""
    control_url = settings.control_url
    internal_api_key = (
        settings.internal_api_key.get_secret_value() if settings.internal_api_key else ""
    )

    manifest = await _refetch_manifest_for_resume(settings, ota_state, pool)
    if manifest is None:
        logger.error("cannot resume migrating — manifest fetch failed")
        return

    if manifest.migrations or (
        manifest.target_data_schema > await db.get_current_schema_version(pool)
    ):
        owner_pool = await _create_owner_pool(settings)
        try:
            for mig in manifest.migrations:
                already = await db.is_migration_applied(pool, mig.name, mig.sha256)
                if not already:
                    await db.run_migration(owner_pool, mig.sql, mig.name)
                    await db.record_migration(owner_pool, mig.name, mig.sha256)

            if manifest.target_data_schema > await db.get_current_schema_version(pool):
                await db.set_schema_version(owner_pool, manifest.target_data_schema)
        finally:
            await owner_pool.close()

    # Dispatch to APPLYING
    j.state = UpdateState.APPLYING.value
    await _post_audit(
        control_url,
        internal_api_key,
        "ota.apply_started",
        j.manifest_version,
        journal_snapshot=j.to_dict(),
    )
    journal.save_journal(j, journal_path)

    for svc_name in sorted(j.target_digests):
        if svc_name in _SKIP_APPLY:
            continue
        digest = j.target_digests[svc_name]
        svc = manifest.services.get(svc_name)
        if svc is None:
            continue
        new_ref = f"{svc.image}@{digest}"
        journal.save_journal(j, journal_path)
        await docker_ops.recreate_service_container(
            docker,
            svc_name,
            new_image_ref=new_ref,
        )
        if svc_name not in j.applied_services:
            j.applied_services.append(svc_name)
        journal.save_journal(j, journal_path)

    # Dispatch to roll_openclaw / health_checking via the normal path.
    await _continue_from_applying(
        docker,
        settings,
        j,
        manifest,
        journal_path,
        ota_state,
        ota_state_path,
        redis_client,
    )


async def _finish_health_and_commit(
    docker,
    settings,
    j: journal.ApplyJournal,
    manifest: Manifest,
    journal_path: str | Path,
    ota_state: state_store.OtaState,
    ota_state_path: str | Path,
) -> None:
    """Health-check applied services and commit (skip roll_openclaw)."""
    control_url = settings.control_url
    internal_api_key = (
        settings.internal_api_key.get_secret_value() if settings.internal_api_key else ""
    )

    for svc_name in j.applied_services:
        ok = await docker_ops.wait_for_health(
            docker,
            svc_name,
            timeout_s=settings.health_window_seconds,
        )
        if not ok:
            logger.error(
                "health check failed for %s on resume — rolling back",
                svc_name,
            )
            await _run_rollback(
                docker,
                settings,
                j,
                manifest,
                journal_path,
                ota_state,
                ota_state_path,
            )
            return

    j.state = UpdateState.COMMITTED.value
    ota_state.current_version = manifest.version
    state_store.save_state(ota_state, ota_state_path)
    journal.mark_committed(j, journal_path)
    await _post_audit(
        control_url,
        internal_api_key,
        "ota.committed",
        j.manifest_version,
        journal_snapshot=j.to_dict(),
    )


async def _resume_applying(
    docker,
    settings,
    j: journal.ApplyJournal,
    journal_path: str | Path,
    ota_state: state_store.OtaState,
    ota_state_path: str | Path,
    pool,
    redis_client: redis.Redis,
) -> None:
    """Re-run per-service recreate (idempotent), then continue."""
    manifest = await _refetch_manifest_for_resume(settings, ota_state, pool)
    if manifest is None:
        logger.error("cannot resume applying — manifest fetch failed")
        return

    for svc_name in sorted(j.target_digests):
        if svc_name in _SKIP_APPLY:
            continue
        digest = j.target_digests[svc_name]
        svc = manifest.services.get(svc_name)
        if svc is None:
            continue
        new_ref = f"{svc.image}@{digest}"
        journal.save_journal(j, journal_path)
        await docker_ops.recreate_service_container(
            docker,
            svc_name,
            new_image_ref=new_ref,
        )
        if svc_name not in j.applied_services:
            j.applied_services.append(svc_name)
        journal.save_journal(j, journal_path)

    await _continue_from_applying(
        docker,
        settings,
        j,
        manifest,
        journal_path,
        ota_state,
        ota_state_path,
        redis_client,
    )


async def _continue_from_applying(
    docker,
    settings,
    j: journal.ApplyJournal,
    manifest: Manifest,
    journal_path: str | Path,
    ota_state: state_store.OtaState,
    ota_state_path: str | Path,
    redis_client: redis.Redis,
) -> None:
    """After services are recreated, continue into ROLL_OPENCLAW or HEALTH_CHECKING."""
    control_url = settings.control_url
    internal_api_key = (
        settings.internal_api_key.get_secret_value() if settings.internal_api_key else ""
    )

    # ROLL OPENCLAW (if not yet done)
    openclaw_svc = manifest.services.get(_OPENCLAW_NAME)
    if openclaw_svc is not None and not j.openclaw_committed:
        openclaw_new_digest = openclaw_svc.digest
        if openclaw_new_digest != ota_state.current_openclaw_digest:
            j.state = UpdateState.ROLL_OPENCLAW.value
            journal.save_journal(j, journal_path)

            try:
                job_id = j.job_id or str(uuid.uuid4())
                roll_payload = {
                    "job_id": job_id,
                    "ota_version": manifest.version,
                    "target_digest": openclaw_new_digest,
                    "mode": "apply",
                }
                j.job_id = job_id
                journal.save_journal(j, journal_path)
                async with httpx.AsyncClient(timeout=httpx.Timeout(30)) as client:
                    r = await client.post(
                        f"{control_url.rstrip('/')}/api/internal/ota/roll-openclaw",
                        json=roll_payload,
                        headers={"Authorization": f"Bearer {internal_api_key}"},
                    )
                    r.raise_for_status()

                result = await _wait_for_roll_result(
                    redis_client,
                    job_id=j.job_id,
                    timeout_s=settings.health_window_seconds,
                )

                if result.get("status") == "committed":
                    j.openclaw_committed = True
                    j.openclaw_prev_digest = result.get("representative_prev_digest")
                    ota_state.current_openclaw_digest = openclaw_new_digest
                    state_store.save_state(ota_state, ota_state_path)
                    journal.save_journal(j, journal_path)
                    openclaw_info = await docker.images.inspect(
                        f"{openclaw_svc.image}@{openclaw_new_digest}"
                    )
                    await _retag_from_bundle(
                        docker,
                        openclaw_info["Id"],
                        _OPENCLAW_NAME,
                    )
                else:
                    logger.error("openclaw roll failed on resume — rolling back")
                    await _run_rollback(
                        docker,
                        settings,
                        j,
                        manifest,
                        journal_path,
                        ota_state,
                        ota_state_path,
                    )
                    return
            except httpx.HTTPError:
                logger.exception("openclaw roll HTTP error on resume — rolling back")
                await _run_rollback(
                    docker,
                    settings,
                    j,
                    manifest,
                    journal_path,
                    ota_state,
                    ota_state_path,
                )
                return

    # HEALTH CHECKING
    j.state = UpdateState.HEALTH_CHECKING.value
    journal.save_journal(j, journal_path)

    for svc_name in j.applied_services:
        ok = await docker_ops.wait_for_health(
            docker,
            svc_name,
            timeout_s=settings.health_window_seconds,
        )
        if not ok:
            logger.error("health check failed for %s on resume — rolling back", svc_name)
            await _run_rollback(
                docker,
                settings,
                j,
                manifest,
                journal_path,
                ota_state,
                ota_state_path,
            )
            return

    if j.openclaw_committed and _OPENCLAW_NAME in manifest.services:
        ok = await docker_ops.wait_for_health(
            docker,
            _OPENCLAW_NAME,
            timeout_s=settings.health_window_seconds,
        )
        if not ok:
            logger.error("openclaw health check failed on resume — rolling back")
            await _run_rollback(
                docker,
                settings,
                j,
                manifest,
                journal_path,
                ota_state,
                ota_state_path,
            )
            return

    # COMMITTED / SELF-SWAP
    watcher_svc = manifest.services.get(_WATCHER_NAME)
    if watcher_svc is not None:
        current_digest = await docker_ops.get_self_image_digest(docker)
        if current_digest is None or watcher_svc.digest != current_digest:
            j.state = UpdateState.COMMITTED_AWAITING_SELF_SWAP.value
            journal.save_journal(j, journal_path)

            new_ref = f"{watcher_svc.image}@{watcher_svc.digest}"
            try:
                await self_swap.do_old_side_self_swap(
                    docker,
                    new_image_ref=new_ref,
                    own_container_name=_WATCHER_NAME,
                    journal=j,
                )
                journal.save_journal(j, journal_path)
                sys.exit(0)
            except Exception:
                logger.exception("self-swap failed on resume")
                ota_state.record_self_swap_failure(watcher_svc.digest)
                state_store.save_state(ota_state, ota_state_path)
                j.state = UpdateState.SELF_SWAP_FAILED.value
                journal.save_journal(j, journal_path)
                return

    # COMMITTED
    j.state = UpdateState.COMMITTED.value
    ota_state.current_version = manifest.version
    state_store.save_state(ota_state, ota_state_path)
    journal.mark_committed(j, journal_path)
    await _post_audit(
        control_url,
        internal_api_key,
        "ota.committed",
        j.manifest_version,
        journal_snapshot=j.to_dict(),
    )


async def _resume_roll_openclaw(
    docker,
    settings,
    j: journal.ApplyJournal,
    journal_path: str | Path,
    ota_state: state_store.OtaState,
    ota_state_path: str | Path,
    pool,
    redis_client: redis.Redis,
) -> None:
    """Re-poll the roll-openclaw job status."""
    result = await _wait_for_roll_result(
        redis_client,
        job_id=j.job_id,
        timeout_s=settings.health_window_seconds,
    )

    manifest = await _refetch_manifest_for_resume(settings, ota_state, pool)

    if result.get("status") == "committed":
        j.openclaw_committed = True
        j.openclaw_prev_digest = result.get("representative_prev_digest")
        openclaw_new_digest = j.target_digests.get(_OPENCLAW_NAME, "")
        if openclaw_new_digest:
            ota_state.current_openclaw_digest = openclaw_new_digest
            state_store.save_state(ota_state, ota_state_path)
        journal.save_journal(j, journal_path)

        if manifest is not None:
            await _continue_from_applying(
                docker,
                settings,
                j,
                manifest,
                journal_path,
                ota_state,
                ota_state_path,
                redis_client,
            )
    else:
        logger.error("openclaw roll failed on resume — rolling back")
        if manifest is not None:
            await _run_rollback(
                docker,
                settings,
                j,
                manifest,
                journal_path,
                ota_state,
                ota_state_path,
            )
        else:
            j.state = UpdateState.ROLLBACK_FAILED.value
            journal.save_journal(j, journal_path)


async def _resume_health_checking(
    docker,
    settings,
    j: journal.ApplyJournal,
    journal_path: str | Path,
    ota_state: state_store.OtaState,
    ota_state_path: str | Path,
    pool,
    redis_client: redis.Redis,
) -> None:
    """Re-run health checks and proceed to commit or rollback."""
    manifest = await _refetch_manifest_for_resume(settings, ota_state, pool)
    if manifest is None:
        logger.error("cannot resume health_checking — manifest fetch failed")
        j.state = UpdateState.ROLLBACK_FAILED.value
        journal.save_journal(j, journal_path)
        return

    await _continue_from_applying(
        docker,
        settings,
        j,
        manifest,
        journal_path,
        ota_state,
        ota_state_path,
        redis_client,
    )


async def _resume_rolling_back(
    docker,
    settings,
    j: journal.ApplyJournal,
    journal_path: str | Path,
    ota_state: state_store.OtaState,
    ota_state_path: str | Path,
    pool,
) -> None:
    """Re-run rollback (idempotent)."""
    manifest = await _refetch_manifest_for_resume(settings, ota_state, pool)
    if manifest is None:
        j.state = UpdateState.ROLLBACK_FAILED.value
        journal.save_journal(j, journal_path)
        return

    await _run_rollback(
        docker,
        settings,
        j,
        manifest,
        journal_path,
        ota_state,
        ota_state_path,
    )


async def _resume_self_swap(
    docker,
    settings,
    j: journal.ApplyJournal,
    journal_path: str | Path,
    ota_state: state_store.OtaState,
    ota_state_path: str | Path,
) -> None:
    """Resume from COMMITTED_AWAITING_SELF_SWAP.

    Compare the running image digest to the journal's self_swap_target_digest.
    If they match, this is the **successor** — do takeover and commit.
    If they do not match, this is the **old** container — cleanup orphan
    successor if any, then retry the swap.
    """
    current_digest = await docker_ops.get_self_image_digest(docker)

    if current_digest is not None and current_digest == j.self_swap_target_digest:
        logger.info("resume: running as successor — doing takeover")
        await self_swap.do_successor_side_takeover(
            docker,
            own_container_name=j.successor_name or _WATCHER_NAME,
        )
        _discard_journal(journal_path)
        return

    # Old container still running — try the swap again
    logger.info("resume: old container still active — retrying self-swap")

    watcher_target_digest = j.self_swap_target_digest or j.target_digests.get(_WATCHER_NAME, "")
    if not watcher_target_digest:
        logger.error("no watcher target digest in journal — cannot resume swap")
        j.state = UpdateState.SELF_SWAP_FAILED.value
        journal.save_journal(j, journal_path)
        return

    # Cleanup orphan successor if it exists
    if j.successor_name:
        with contextlib.suppress(aiodocker.exceptions.DockerError):
            await docker_ops.stop(docker, j.successor_name)
        with contextlib.suppress(aiodocker.exceptions.DockerError):
            await docker_ops.remove(docker, j.successor_name)

    # Re-try the swap
    manifest = await _refetch_manifest_for_resume(settings, ota_state, None)
    if manifest is None:
        j.state = UpdateState.SELF_SWAP_FAILED.value
        journal.save_journal(j, journal_path)
        return

    watcher_svc = manifest.services.get(_WATCHER_NAME)
    if watcher_svc is None:
        logger.error("no watcher service in manifest — cannot resume swap")
        j.state = UpdateState.SELF_SWAP_FAILED.value
        journal.save_journal(j, journal_path)
        return

    new_ref = f"{watcher_svc.image}@{watcher_svc.digest}"
    try:
        await self_swap.do_old_side_self_swap(
            docker,
            new_image_ref=new_ref,
            own_container_name=_WATCHER_NAME,
            journal=j,
        )
        journal.save_journal(j, journal_path)
        logger.info("resume: self-swap successor re-launched")
        sys.exit(0)
    except Exception:
        logger.exception("self-swap retry failed")
        ota_state.record_self_swap_failure(watcher_svc.digest)
        state_store.save_state(ota_state, ota_state_path)
        j.state = UpdateState.SELF_SWAP_FAILED.value
        journal.save_journal(j, journal_path)
