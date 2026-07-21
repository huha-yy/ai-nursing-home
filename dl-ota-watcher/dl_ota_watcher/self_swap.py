"""P7 — successor-container self-update mechanism.

The OLD watcher spawns a successor container with a different name,
then exits. The successor reaps the OLD container, renames itself
to the canonical name, and updates its restart policy.
"""

from __future__ import annotations

from dl_ota_watcher import docker_ops


def short_digest(digest: str) -> str:
    """First 12 hex chars of a sha256:... digest."""
    hex_part = digest.removeprefix("sha256:")
    return hex_part[:12]


async def do_old_side_self_swap(docker, *, new_image_ref, own_container_name, journal) -> str:
    """Pull the new image, create a successor, start it. Returns successor name.
    The journal object (dl_ota_watcher.journal.Journal) gets successor_name
    and self_swap_target_digest set directly on it. Caller saves the journal
    to disk afterward, then calls sys.exit(0).
    """
    parts = new_image_ref.split("@")
    image_name = parts[0] if parts else new_image_ref
    digest_tag = parts[1] if len(parts) > 1 else "latest"
    await docker_ops.pull_image(docker, image_name, digest_tag)

    successor_name = f"dl-ota-watcher-{short_digest(digest_tag)}"
    src = await docker_ops.introspect(docker, own_container_name)

    await docker_ops.create_from_inspect(
        docker,
        name=successor_name,
        image_ref=new_image_ref,
        source_inspect=src,
        strip_compose_labels=True,
        override_restart_policy="no",
    )
    await docker_ops.start(docker, successor_name)

    journal.successor_name = successor_name
    journal.self_swap_target_digest = digest_tag
    return successor_name


async def do_successor_side_takeover(docker, *, own_container_name) -> None:
    """Stop the OLD watcher, remove it, rename self to canonical name,
    and update restart policy to unless-stopped."""
    await docker_ops.stop(docker, "dl-ota-watcher")
    await docker_ops.remove(docker, "dl-ota-watcher")
    await docker_ops.rename(docker, own_container_name, "dl-ota-watcher")
    await docker_ops.update_restart_policy(docker, "dl-ota-watcher", policy="unless-stopped")
