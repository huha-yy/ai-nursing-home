"""P7 — thin aiodocker wrapper for the watcher (Engine API via dato-docker-proxy)."""

from __future__ import annotations

import asyncio
import logging
import time

import aiodocker

logger = logging.getLogger(__name__)


async def pull_image(docker, image: str, digest: str) -> dict:
    await docker.images.pull(from_image=image, tag=digest)
    return await docker.images.inspect(f"{image}@{digest}")


async def tag_image(docker, image_id: str, compose_ref: str) -> None:
    """Tag a loaded image with compose_ref so :latest tracks OTA."""
    image_name, _, tag = compose_ref.rpartition(":")
    if not image_name or not tag:
        logger.warning("invalid compose_ref for retag: %r", compose_ref)
        return
    await docker.images.tag(image_id, repo=image_name, tag=tag)


async def introspect(docker, name: str) -> dict:
    container = await docker.containers.get(name)
    return await container.show()


async def stop(docker, name: str) -> None:
    container = await docker.containers.get(name)
    try:
        await container.stop()
    except aiodocker.exceptions.DockerError as e:
        if e.status not in (304, 404):
            raise


async def remove(docker, name: str) -> None:
    try:
        container = await docker.containers.get(name)
        await container.delete(force=True)
    except aiodocker.exceptions.DockerError as e:
        if e.status != 404:
            raise


async def create_from_inspect(
    docker,
    *,
    name: str,
    image_ref: str,
    source_inspect: dict,
    strip_compose_labels: bool = False,
    override_restart_policy: str | None = None,
) -> str:
    """Build a create payload from a previous inspect snapshot."""
    cfg = source_inspect["Config"]
    host = dict(source_inspect["HostConfig"])
    labels = dict(cfg.get("Labels") or {})
    if strip_compose_labels:
        labels = {k: v for k, v in labels.items() if not k.startswith("com.docker.compose.")}
    if override_restart_policy is not None:
        host["RestartPolicy"] = {"Name": override_restart_policy}

    # Capture ALL networks the source container was attached to.
    source_networks = (source_inspect.get("NetworkSettings") or {}).get("Networks") or {}
    networking_config = {
        "EndpointsConfig": {
            net_name: {k: v for k, v in net_cfg.items() if k in ("Aliases", "IPAMConfig", "Links")}
            for net_name, net_cfg in source_networks.items()
        }
    }

    payload = {
        "Image": image_ref,
        "User": cfg.get("User", ""),
        "Env": cfg.get("Env") or [],
        "Cmd": cfg.get("Cmd"),
        "WorkingDir": cfg.get("WorkingDir") or "",
        "Labels": labels,
        "HostConfig": host,
        "NetworkingConfig": networking_config,
    }
    container = await docker.containers.create_or_replace(name=name, config=payload)
    return container.id


async def start(docker, name: str) -> None:
    container = await docker.containers.get(name)
    await container.start()


async def rename(docker, name: str, new_name: str) -> None:
    container = await docker.containers.get(name)
    await container._query("rename", method="POST", params={"name": new_name})


async def update_restart_policy(docker, name: str, *, policy: str) -> None:
    container = await docker.containers.get(name)
    await container._query("update", method="POST", data={"RestartPolicy": {"Name": policy}})


async def wait_for_health(docker, name: str, *, timeout_s: int, poll_s: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            info = await introspect(docker, name)
            state = info["State"]
            h = (state.get("Health") or {}).get("Status")
            if h == "healthy" or (h is None and state.get("Status") == "running"):
                return True
        except aiodocker.exceptions.DockerError:
            pass
        await asyncio.sleep(poll_s)
    return False


async def recreate_service_container(
    docker,
    name: str,
    *,
    new_image_ref: str,
    strip_compose_labels: bool = False,
    override_restart_policy: str | None = None,
) -> str:
    """Stop → remove → create-from-inspect → start. Returns new container id."""
    src = await introspect(docker, name)
    await stop(docker, name)
    await remove(docker, name)
    new_id = await create_from_inspect(
        docker,
        name=name,
        image_ref=new_image_ref,
        source_inspect=src,
        strip_compose_labels=strip_compose_labels,
        override_restart_policy=override_restart_policy,
    )
    await start(docker, name)
    return new_id


async def get_self_image_digest(docker) -> str | None:
    """Return the manifest digest (sha256:...) of the running self container's
    image, or None if the image was loaded without a registry digest."""
    info = await introspect(docker, "dl-ota-watcher")
    image_id = info["Image"]
    image_info = await docker.images.inspect(image_id)
    repo_digests: list[str] = image_info.get("RepoDigests") or []
    if not repo_digests:
        return None
    _, _, digest = repo_digests[0].partition("@")
    return digest if digest.startswith("sha256:") else None
