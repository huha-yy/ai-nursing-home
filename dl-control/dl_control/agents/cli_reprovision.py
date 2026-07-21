"""P6 — CLI entrypoint for `make reprovision-tier1`."""

from __future__ import annotations

import asyncio
import json
import sys

from dl_control.agents.provisioning.docker_client import DockerClient
from dl_control.agents.provisioning.service import ProvisioningConfig
from dl_control.agents.reprovision import reprovision_tier1_agents
from dl_control.db import Database
from dl_control.settings import load_settings


async def _main() -> int:
    settings = load_settings()
    db = Database(dsn=settings.db_url.get_secret_value())
    await db.connect()
    docker = DockerClient.from_host(settings.docker_host)
    cfg = ProvisioningConfig.from_settings(settings)

    summary = await reprovision_tier1_agents(
        db=db,
        docker=docker,
        cfg=cfg,
        reason="cli",
    )
    print(json.dumps(summary, indent=2, default=str))

    await docker.close()
    await db.close()

    return 1 if summary["failed"] else 0


def main() -> None:
    sys.exit(asyncio.run(_main()))


if __name__ == "__main__":
    main()
