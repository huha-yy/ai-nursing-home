"""The single module that talks to Docker (spec §8).

Uses aiodocker against the dato-docker-proxy socket-proxy — never the raw
socket. Every create payload is built here from a fixed hardened HostConfig;
no caller-supplied HostConfig field is accepted. Audit is enforced inside the
client and is fail-closed: the audit hook is awaited before the Docker call,
so a failed audit write aborts the operation.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import aiodocker
from aiodocker.exceptions import DockerError

from dl_control.agents.provisioning.errors import ProvisioningError

# audit(action, target, meta) -> awaitable
AuditHook = Callable[[str, str, dict], Awaitable[None]]

_HOME_MOUNT = "/home/node/.openclaw"
_CONFIG_MOUNT = "/app/config"
_NETWORK = "dato_net"


def build_create_config(
    *,
    name: str,
    image: str,
    host_agent_dir: str,
    agent_id: str,
    tier: str = "tier0",
) -> dict:
    """The fixed hardened container-create payload (spec §8, §13). The agent
    directory is bound as two directory binds; nothing here is caller-tunable."""
    config: dict = {
        "Image": image,
        "User": "1000:1000",
        "Labels": {
            "com.docker.compose.project": "dato",
            "dato.agent.id": agent_id,
        },
        "HostConfig": {
            "Binds": [
                f"{host_agent_dir}:{_HOME_MOUNT}:rw",
                f"{host_agent_dir}/config:{_CONFIG_MOUNT}:rw",
            ],
            "Privileged": False,
            "CapDrop": ["ALL"],
            "CapAdd": [],
            "RestartPolicy": {"Name": "unless-stopped"},
            "NetworkMode": _NETWORK,
        },
        "NetworkingConfig": {"EndpointsConfig": {_NETWORK: {}}},
    }
    # Tier 1: DNS through dl-egress-dns + per-agent log tag (spec §10.2).
    if tier == "tier1":
        config["HostConfig"]["Dns"] = ["dl-egress-dns"]
        config["HostConfig"]["LogConfig"] = {
            "Type": "json-file",
            "Config": {"tag": f"agent_uuid={agent_id},tier=tier1"},
        }
    return config


class DockerClient:
    """Thin aiodocker wrapper. Construct with an aiodocker.Docker (or a fake
    in tests). `from_host` builds one bound to the socket-proxy."""

    def __init__(self, docker) -> None:
        self._docker = docker

    @classmethod
    def from_host(cls, docker_host: str) -> DockerClient:
        return cls(aiodocker.Docker(url=docker_host))

    async def close(self) -> None:
        await self._docker.close()

    async def _audited(self, audit: AuditHook, action: str, target: str, meta: dict, call):
        """Run `call()` framed by audit. Pre-call audit is awaited first
        (fail-closed); a failure is audited as <action>_failed and re-raised
        as a ProvisioningError."""
        await audit(action, target, meta)
        try:
            return await call()
        except ProvisioningError:
            raise
        except Exception as exc:  # noqa: BLE001 — every Docker failure is audited
            await audit(f"{action}_failed", target, {"error": str(exc)})
            raise ProvisioningError(action, str(exc)) from exc

    async def create_container(
        self,
        *,
        audit: AuditHook,
        name: str,
        image: str,
        host_agent_dir: str,
        agent_id: str,
        tier: str = "tier0",
    ) -> str:
        """Create the agent container from the hardened payload; return its id."""
        config = build_create_config(
            name=name,
            image=image,
            host_agent_dir=host_agent_dir,
            agent_id=agent_id,
            tier=tier,
        )

        async def _call():
            container = await self._docker.containers.create(config=config, name=name)
            return container.id

        return await self._audited(audit, "container_create", name, {"payload": config}, _call)

    async def start_container(self, *, audit: AuditHook, name: str, container_id: str) -> None:
        async def _call():
            container = await self._docker.containers.get(container_id)
            await container.start()

        await self._audited(audit, "container_start", name, {"container_id": container_id}, _call)

    async def stop_container(self, *, audit: AuditHook, name: str, container_id: str) -> None:
        async def _call():
            container = await self._docker.containers.get(container_id)
            await container.stop()

        await self._audited(audit, "container_stop", name, {"container_id": container_id}, _call)

    async def remove_container(self, *, audit: AuditHook, name: str, container_id: str) -> None:
        """Force-remove (kills a running container + removes in one call)."""

        async def _call():
            container = await self._docker.containers.get(container_id)
            await container.delete(force=True)

        await self._audited(audit, "container_remove", name, {"container_id": container_id}, _call)

    async def inspect_container(self, *, audit: AuditHook, name: str) -> dict | None:
        """Inspect by name. Returns the inspect dict, or None if absent."""
        await audit("container_inspect", name, {})
        try:
            container = await self._docker.containers.get(name)
            return await container.show()
        except DockerError as exc:
            if exc.status == 404:
                return None
            await audit("container_inspect_failed", name, {"error": str(exc)})
            raise ProvisioningError("container_inspect", str(exc)) from exc
        except Exception as exc:
            await audit("container_inspect_failed", name, {"error": str(exc)})
            raise ProvisioningError("container_inspect", str(exc)) from exc

    async def inspect_image(
        self,
        *,
        audit: AuditHook,
        image_id: str,
    ) -> dict | None:
        """Inspect an image by id. Returns the inspect dict, or None if absent."""
        await audit("image_inspect", image_id, {})
        try:
            image = await self._docker.images.get(image_id)
            if isinstance(image, dict):
                return image
            return await image.show()
        except DockerError as exc:
            if exc.status == 404:
                return None
            await audit("image_inspect_failed", image_id, {"error": str(exc)})
            raise ProvisioningError("image_inspect", str(exc)) from exc
        except Exception as exc:
            await audit("image_inspect_failed", image_id, {"error": str(exc)})
            raise ProvisioningError("image_inspect", str(exc)) from exc

    async def recreate_container(
        self,
        *,
        audit: AuditHook,
        name: str,
        image: str,
        host_agent_dir: str,
        agent_id: str,
        tier: str = "tier0",
    ) -> str:
        """Stop+remove+create+start in sequence. Uses the existing hardened
        build_create_config; the new image replaces the existing container's
        image, all other config (mounts, network, caps, restart policy)
        derives from the fixed payload — there is no caller-supplied override."""
        existing = await self.inspect_container(audit=audit, name=name)
        if existing is not None:
            cid = existing["Id"]
            await self.stop_container(audit=audit, name=name, container_id=cid)
            await self.remove_container(audit=audit, name=name, container_id=cid)
        new_id = await self.create_container(
            audit=audit,
            name=name,
            image=image,
            host_agent_dir=host_agent_dir,
            agent_id=agent_id,
            tier=tier,
        )
        await self.start_container(audit=audit, name=name, container_id=new_id)
        return new_id

    async def wait_for_health(
        self,
        *,
        audit: AuditHook,
        name: str,
        timeout_s: int,
        poll_interval_s: float = 2.0,
    ) -> bool:
        """Poll inspect until State.Health.Status == 'healthy' OR
        State.Status == 'running' AND no healthcheck configured. Returns
        False on timeout."""
        import asyncio
        import time

        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            info = await self.inspect_container(audit=audit, name=name)
            if info is not None:
                state = info.get("State") or {}
                health = (state.get("Health") or {}).get("Status")
                if health == "healthy":
                    return True
                if health is None and state.get("Status") == "running":
                    return True
            await asyncio.sleep(poll_interval_s)
        return False

    async def healthcheck(self, *, audit: AuditHook) -> dict:
        """Proxy reachability probe — hits /version (gated by VERSION=1 on the
        socket-proxy). MUST NOT use system.info() — /info is gated by SYSTEM,
        which the proxy keeps at 0."""

        async def _call():
            return await self._docker.version()

        return await self._audited(audit, "docker_healthcheck", "-", {}, _call)
