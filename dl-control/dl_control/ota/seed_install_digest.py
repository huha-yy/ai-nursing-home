"""One-shot seeder: writes ota_schema_version + agents.current_openclaw_digest
from the verified install-bundle snapshot. Runs as the
dato-control-install-seed compose service (spec §11.1). Forward-only
via GREATEST(...) upsert; safe to re-run."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

from dl_control.db import Database

logger = logging.getLogger(__name__)


async def _seed(db: Database, bundle: dict) -> None:
    payload = bundle["payload"]
    target_schema = int(payload["target_data_schema"])

    services = payload.get("services", {})
    openclaw = services.get("openclaw")
    openclaw_digest = None
    if not isinstance(openclaw, dict) or "digest" not in openclaw:
        if payload.get("placeholder"):
            logger.warning(
                "bundle snapshot has no services.openclaw.digest — "
                "OTA openclaw rolling will be skipped until a real bundle is installed"
            )
        else:
            raise SystemExit("bundle snapshot payload.services.openclaw.digest is missing")
    else:
        openclaw_digest = openclaw["digest"]

    async with db.conn(user_id=None, role="system") as conn:
        await conn.execute(
            """
            INSERT INTO ota_schema_version (singleton, version)
            VALUES (TRUE, %s)
            ON CONFLICT (singleton) DO UPDATE
              SET version = GREATEST(ota_schema_version.version,
                                     EXCLUDED.version)
            """,
            (target_schema,),
        )

        await conn.execute(
            """
            UPDATE agents
            SET current_openclaw_digest = %s
            WHERE current_openclaw_digest IS NULL
            """,
            (openclaw_digest,),
        )

    logger.info(
        "seeded ota_schema_version.version=%s openclaw_digest=%s",
        target_schema,
        openclaw_digest,
    )


def _load_bundle(source: dict | Path) -> dict:
    """Load and validate a bundle dict or path."""
    bundle = json.loads(source.read_text("utf-8")) if isinstance(source, Path) else source
    if "payload" not in bundle or "signature" not in bundle:
        raise SystemExit("bundle snapshot missing 'payload'/'signature' keys")
    payload = bundle["payload"]
    if payload.get("placeholder"):
        logger.warning(
            "bundle snapshot is a vendor placeholder — "
            "OTA will not function until a real bundle is installed"
        )
    if payload.get("bundle_format") != 1:
        raise SystemExit(f"unsupported bundle_format: {payload.get('bundle_format')!r}")
    return bundle


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bundle-snapshot",
        default="/data/secrets/.install-bundle.json",
        help="Path to the verified install-bundle snapshot",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    dsn = os.environ.get("DL_CONTROL_OWNER_DSN")
    if not dsn:
        sys.exit("DL_CONTROL_OWNER_DSN is not set")

    bundle = _load_bundle(Path(args.bundle_snapshot))
    db = Database(dsn)

    async def _run() -> None:
        await db.connect()
        try:
            await _seed(db, bundle)
        finally:
            await db.close()

    asyncio.run(_run())


if __name__ == "__main__":
    main()
