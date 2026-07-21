"""Provisioning orchestration — the provision + restart flows (spec §9).

Concurrency: each flow claims the agent with a compare-and-swap status UPDATE
(spec §5.3). No DB transaction is held across Docker I/O — every DB touch is
its own short transaction.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

import structlog

from dl_control.agents import registry
from dl_control.agents.provisioning import compose_mirror, config_gen
from dl_control.agents.provisioning.errors import ProvisioningError
from dl_control.agents.provisioning.fs_safety import (
    atomic_write_text,
    copy_managed_file,
    mkdir_safe,
    read_managed_text,
    resolve_agent_dir,
)
from dl_control.agents.provisioning.workspace import seed_workspace
from dl_control.audit.service import write_event
from dl_control.db import Database

logger = structlog.get_logger(__name__)

_PROVISION_FROM = ("registered", "error", "stopped")


async def write_event_safe(db, actor_user_id, action, *, target, meta):
    """write_event that never raises (reconciler must stay best-effort)."""
    try:
        async with db.conn(user_id=actor_user_id, role="system") as conn:
            await write_event(
                conn,
                actor_user_id=actor_user_id,
                action=action,
                target=target,
                meta=meta,
            )
    except Exception:  # noqa: BLE001
        logger.warning("audit write failed for %s", action, exc_info=True)


_BUNDLE_SNAPSHOT_PATH = Path(
    os.environ.get(
        "DL_CONTROL_BUNDLE_SNAPSHOT_PATH",
        "/data/secrets/.install-bundle.json",
    )
)


def _admin_dsn_for_db(owner_dsn: str, db_name: str) -> str:
    """Replace the database name in the owner DSN (spec §5.1 step 3c).
    Uses urlparse for reliable extraction instead of fragile string replace."""
    from urllib.parse import urlparse, urlunparse

    parsed = urlparse(owner_dsn)
    new = parsed._replace(path=f"/{db_name}")
    return urlunparse(new)


class AgentNotFoundError(RuntimeError):
    """No registry row for the given agent id (-> HTTP 404)."""


class TierNotSupportedError(RuntimeError):
    """Provisioning a tier-1 agent before P4 (-> HTTP 409)."""


class AgentBusyError(RuntimeError):
    """The agent is in-flight or in the wrong state for this op (-> HTTP 409)."""


@dataclass(frozen=True)
class ProvisioningConfig:
    """The provisioning-relevant slice of Settings (spec §12.2)."""

    agents_root: str
    host_agents_root: str
    templates_root: str
    openclaw_image: str
    deepseek_api_key: str
    site_host: str
    pexels_api_key: str | None = None
    tavily_api_key: str | None = None
    xiaomi_mimo_api_key: str | None = None
    feishu_webhook_url: str | None = None
    precreated_agents_root: str = "/app/precreated_agents"
    local_llm_base_url: str | None = None  # P4: Tier 1 pre-flight
    local_llm_api_key: str | None = None  # P4: forwarded to upstream
    owner_dsn: str | None = None  # P4: for per-agent DB ops
    # P5 dl-cognee integration
    cognee_url: str = "http://dl-cognee:8080"
    cognee_admin_token: str | None = None
    # P6 dl-llm-local
    local_llm_default_model: str = "qwen3.5:9b"
    local_llm_keep_alive_seconds: int = 1800
    # P10: ComfyUI remote URL (e.g. http://192.168.10.70:8188)
    comfyui_url: str | None = None

    @classmethod
    def from_settings(cls, s) -> ProvisioningConfig:
        return cls(
            agents_root=s.agents_root,
            host_agents_root=s.host_agents_root,
            templates_root=s.templates_root,
            openclaw_image=s.openclaw_image,
            deepseek_api_key=s.deepseek_api_key.get_secret_value(),
            site_host=s.site_host,
            pexels_api_key=s.pexels_api_key.get_secret_value() if s.pexels_api_key else None,
            tavily_api_key=s.tavily_api_key.get_secret_value() if s.tavily_api_key else None,
            xiaomi_mimo_api_key=s.xiaomi_mimo_api_key.get_secret_value() if s.xiaomi_mimo_api_key else None,
            feishu_webhook_url=s.feishu_webhook_url,
            local_llm_base_url=(str(s.local_llm_base_url) if s.local_llm_base_url else None),
            local_llm_api_key=(
                s.local_llm_api_key.get_secret_value() if s.local_llm_api_key else None
            ),
            owner_dsn=s.owner_dsn.get_secret_value() if s.owner_dsn else None,
            cognee_url=s.dl_cognee_url,
            cognee_admin_token=(
                s.dl_cognee_admin_token.get_secret_value() if s.dl_cognee_admin_token else None
            ),
            local_llm_default_model=s.local_llm_default_model,
            local_llm_keep_alive_seconds=s.local_llm_keep_alive_seconds,
            comfyui_url=s.comfyui_url or None,
            precreated_agents_root=s.precreated_agents_root,
        )


def _make_audit(db: Database, actor_user_id: str | None):
    """An audit hook bound to the acting user. Each call is its own short
    transaction — safe to invoke from inside the DockerClient (spec §8)."""

    async def audit(action: str, target: str, meta: dict) -> None:
        async with db.conn(user_id=actor_user_id, role="admin") as conn:
            await write_event(
                conn,
                actor_user_id=actor_user_id,
                action=action,
                target=target,
                meta=meta,
            )

    return audit


def _existing_token(env_path: Path, *, agent_dir: Path | None = None) -> str | None:
    """Reuse a previously-generated OPENCLAW_TOKEN if config/.env already has
    one (so a re-provision does not rotate the gateway token).

    The on-disk value is shell-quoted (see render_env_file). shlex.split with
    posix=True decodes the quoting back to the raw token, so we don't end up
    re-quoting an already-quoted string on the next write."""
    import shlex

    if not env_path.exists():
        return None
    for line in read_managed_text(env_path, agent_dir=agent_dir).splitlines():
        if not line.startswith("OPENCLAW_TOKEN="):
            continue
        try:
            parts = shlex.split(line, posix=True)
        except ValueError:
            return None
        if parts and "=" in parts[0]:
            return parts[0].split("=", 1)[1]
    return None


def _generate_config_set(
    cfg: ProvisioningConfig,
    agent_dir: Path,
    row: dict,
    *,
    tier1_env_lines: str = "",
    cognee_internal_token: str = "",
) -> None:
    """Render + write openclaw.json, skills.yaml, config/.env. The openclaw.json
    `meta` block is carried forward from any existing file on every path.
    P4: tier1_env_lines carries Tier-1-specific env vars across restarts.
    P5: cognee_internal_token provides a freshly-minted token on first provision."""
    config_dir = agent_dir / "config"
    openclaw_path = agent_dir / "openclaw.json"
    existing = (
        read_managed_text(openclaw_path, agent_dir=agent_dir) if openclaw_path.exists() else None
    )
    atomic_write_text(
        agent_dir / "skills.yaml",
        config_gen.render_skills_yaml(row.get("skill_list") or []),
        agent_dir=agent_dir,
    )
    token = (
        _existing_token(config_dir / ".env", agent_dir=agent_dir)
        or config_gen.generate_openclaw_token()
    )
    # P3: carry forward existing FEISHU_* env keys from the per-agent .env
    # so a restart preserves wizard-written credentials (spec §7.8).
    feishu_lines = ""
    has_suffixed = False  # D-FEISHU-1: track if _DN* variants exist
    # P4: carry forward Tier 1 env lines across restarts.
    t1_lines = tier1_env_lines
    # P5: carry forward dl-cognee env vars across restarts.
    cognee_lines = ""
    # P9: carry forward GBrain env vars across restarts.
    gbrain_lines = ""
    # P10: carry forward COMFYUI_URL across restarts.
    comfyui_lines = ""
    env_path = config_dir / ".env"
    if env_path.exists():
        for line in read_managed_text(env_path, agent_dir=agent_dir).splitlines():
            if line.startswith("FEISHU_APP_ID_") or line.startswith("FEISHU_APP_SECRET_"):
                feishu_lines += line + "\n"
                has_suffixed = True
            elif (
                line.startswith("FEISHU_APP_ID=")
                or line.startswith("FEISHU_APP_SECRET=")
            ):
                # D-FEISHU-1: skip bare FEISHU_APP_ID/SECRET if a _DN* variant
                # is already present — stale carry-forward from another agent
                # would silently override the correct per-agent credentials.
                if not (has_suffixed and line.startswith("FEISHU_APP_")):
                    feishu_lines += line + "\n"
            elif line.startswith("PEXELS_API_KEY="):
                feishu_lines += line + "\n"
            elif line.startswith("TAVILY_API_KEY="):
                feishu_lines += line + "\n"
            elif line.startswith("XIAOMI_MIMO_API_KEY="):
                feishu_lines += line + "\n"
            elif line.startswith("FEISHU_WEBHOOK_URL="):
                feishu_lines += line + "\n"
            elif (
                line.startswith("DL_AGENT_DB_DSN=")
                or line.startswith("REDIS_KEY_PREFIX=")
                or line.startswith("LLM_BASE_URL=")
            ):
                t1_lines += line + "\n"
            elif line.startswith("DL_INTERNAL_TOKEN=") or line.startswith("DL_COGNEE_URL="):
                cognee_lines += line + "\n"
            elif (
                line.startswith("GBRAIN_API_KEY=")
                or line.startswith("GBRAIN_CLIENT_ID=")
                or line.startswith("GBRAIN_CLIENT_SECRET=")
            ):
                gbrain_lines += line + "\n"
            elif line.startswith("COMFYUI_URL="):
                comfyui_lines += line + "\n"
    openclaw_text = config_gen.render_openclaw_json(
        cfg.templates_root,
        row,
        site_host=cfg.site_host,
        existing_json=existing,
        default_model=cfg.local_llm_default_model,
        comfyui_configured=bool(cfg.comfyui_url or comfyui_lines),
    )
    atomic_write_text(
        config_dir / ".env",
        config_gen.render_env_file(
            openclaw_token=token,
            deepseek_api_key=cfg.deepseek_api_key,
            pexels_api_key=cfg.pexels_api_key if cfg.pexels_api_key else "",
            tavily_api_key=cfg.tavily_api_key if cfg.tavily_api_key else "",
            xiaomi_mimo_api_key=cfg.xiaomi_mimo_api_key if cfg.xiaomi_mimo_api_key else "",
            feishu_webhook_url=cfg.feishu_webhook_url if cfg.feishu_webhook_url else "",
            agent_id=row["id"],
            feishu_env_lines=feishu_lines,
            tier1_env_lines=t1_lines,
            gbrain_env_lines=gbrain_lines,
            cognee_env_lines=cognee_lines,
            cognee_internal_token=cognee_internal_token,
            comfyui_url=cfg.comfyui_url or "",
        ),
        agent_dir=agent_dir,
    )


def _env_dl_internal_token_hash(agent_dir: Path) -> bytes | None:
    """Extract DL_INTERNAL_TOKEN from config/.env and return its sha256 digest.
    Returns None if the file or key is absent — caller is responsible for the
    DB UPDATE that keeps the hash in sync with the on-disk token."""
    import hashlib
    import shlex

    env_path = agent_dir / "config" / ".env"
    if not env_path.exists():
        return None
    for line in read_managed_text(env_path, agent_dir=agent_dir).splitlines():
        if not line.startswith("DL_INTERNAL_TOKEN="):
            continue
        try:
            parts = shlex.split(line, posix=True)
        except ValueError:
            return None
        if parts and "=" in parts[0]:
            token = parts[0].split("=", 1)[1]
            return hashlib.sha256(token.encode()).digest()
    return None


async def _passes_liveness(docker, audit, name: str, grace_seconds: float) -> bool:
    """Two-inspect liveness check (spec §9.1 step 9). BOTH inspects must show
    Running + not Restarting, AND the RestartCount and StartedAt must be
    unchanged across the grace window — this rejects a crash-looping container
    that RestartPolicy=unless-stopped keeps flapping back to Running."""
    first = await docker.inspect_container(audit=audit, name=name)
    if first is None:
        return False
    await asyncio.sleep(grace_seconds)
    second = await docker.inspect_container(audit=audit, name=name)
    if second is None:
        return False
    s1, s2 = first.get("State", {}), second.get("State", {})
    return bool(
        s1.get("Running")
        and not s1.get("Restarting")
        and s2.get("Running")
        and not s2.get("Restarting")
        and first.get("RestartCount") == second.get("RestartCount")
        and s1.get("StartedAt") == s2.get("StartedAt")
    )


async def _refresh_compose_mirror(
    db: Database, cfg: ProvisioningConfig, actor_user_id: str | None
) -> None:
    async with db.conn(user_id=actor_user_id, role="admin") as conn:
        agents = await registry.list_agents(conn)
    compose_mirror.write_compose_mirror(
        cfg.agents_root,
        agents,
        host_agents_root=cfg.host_agents_root,
        openclaw_image=cfg.openclaw_image,
    )


async def _resolve_openclaw_digest(
    docker,
    audit,
    container_id: str,
    bundle_snapshot_path: Path = _BUNDLE_SNAPSHOT_PATH,
) -> str | None:
    """Resolve the current OpenClaw image digest from the running container
    via a three-tier chain: (1) RepoDigests from docker inspect,
    (2) install-bundle snapshot match, (3) NULL with WARN."""
    container = await docker.inspect_container(audit=audit, name=container_id)
    if container is None:
        await audit(
            "agent_provision.opaque_openclaw_digest",
            container_id,
            {"reason": "container_not_found"},
        )
        return None
    image_id = container.get("Image")
    if not image_id:
        await audit(
            "agent_provision.opaque_openclaw_digest",
            container_id,
            {"reason": "no_image_id"},
        )
        return None
    image = await docker.inspect_image(audit=audit, image_id=image_id)
    if image is None:
        await audit(
            "agent_provision.opaque_openclaw_digest",
            container_id,
            {"reason": "image_inspect_returned_none", "image_id": image_id},
        )
        return None
    # Tier 1: RepoDigests
    repo_digests = image.get("RepoDigests") or []
    if repo_digests:
        _, _, digest = repo_digests[0].partition("@")
        return digest
    # Tier 2: install-bundle snapshot match
    if bundle_snapshot_path.exists():
        bundle = json.loads(bundle_snapshot_path.read_text())
        oc = bundle.get("payload", {}).get("services", {}).get("openclaw", {})
        if oc.get("image_id") == image_id:
            digest = oc.get("digest")
            if digest:
                return digest
    # Tier 3: opaque
    logger.warning(
        "agent_provision.opaque_openclaw_digest",
        agent_container_id=container_id,
        image_id=image_id,
    )
    await audit(
        "agent_provision.opaque_openclaw_digest",
        container_id,
        {"image_id": image_id},
    )
    return None


async def provision_agent(
    db: Database,
    docker,
    cfg: ProvisioningConfig,
    *,
    actor_user_id: str | None,
    agent_id: str,
    liveness_grace_seconds: float = 3.0,
    allowed_from: tuple[str, ...] = _PROVISION_FROM,
) -> str:
    """Materialize a registry row into a running container (spec §9.1).
    Returns the terminal status ('active'). Raises on failure."""
    audit = _make_audit(db, actor_user_id)
    name = f"dato-agent-{agent_id}"

    # 1. Load + tier gate.
    async with db.conn(user_id=actor_user_id, role="admin") as conn:
        row = await registry.get_agent(conn, agent_id)
    if row is None:
        raise AgentNotFoundError(agent_id)

    tier = row["tier"]
    # Tier gate: allow tier0 and tier1 (was tier0-only before P4).
    if tier not in ("tier0", "tier1"):
        raise TierNotSupportedError(agent_id)

    # Tier 1 pre-flight: LOCAL_LLM_BASE_URL must be configured + reachable
    # (D-P4-3 fail-closed). P6 extends: if pointing at in-appliance proxy,
    # also check that Ollama is reachable.
    if tier == "tier1":
        if not cfg.local_llm_base_url:
            raise ProvisioningError(
                "tier1_preflight",
                "LOCAL_LLM_BASE_URL is not configured — cannot provision "
                "Tier 1 agent. Set DL_CONTROL_LOCAL_LLM_BASE_URL in the "
                "dl-control environment.",
            )
        if not cfg.owner_dsn:
            raise ProvisioningError(
                "tier1_preflight",
                "DL_CONTROL_OWNER_DSN is not configured — needed for per-agent DB creation.",
            )
        # P6: probe Ollama + proxy when using in-appliance LLM.
        if "dl-llm-proxy" in (cfg.local_llm_base_url or ""):
            try:
                from dl_control.llm.status import get_llm_status

                status = await get_llm_status(
                    model_name=cfg.local_llm_default_model,
                )
                if not status.get("proxy_healthy"):
                    raise ProvisioningError(
                        "tier1_preflight",
                        "dl-llm-proxy /healthz reports unhealthy — cannot provision Tier 1 agent.",
                    )
                if not status.get("ollama_reachable"):
                    raise ProvisioningError(
                        "tier1_preflight",
                        "dl-llm-local (Ollama) is unreachable from "
                        "dl-control — cannot provision Tier 1 agent. "
                        "Check that the local-llm compose profile is enabled.",
                    )
            except ProvisioningError:
                raise
            except Exception as exc:
                raise ProvisioningError(
                    "tier1_preflight", f"Failed to probe local LLM status: {exc}"
                ) from exc

    # 2. CAS-claim the agent.
    async with db.conn(user_id=actor_user_id, role="admin") as conn:
        claimed = await registry.claim_status(
            conn, agent_id, new_status="provisioning", allowed_from=allowed_from
        )
        if not claimed:
            raise AgentBusyError(agent_id)
        await write_event(
            conn,
            actor_user_id=actor_user_id,
            action="agent_provision_started",
            target=str(agent_id),
            meta={},
        )

    try:
        agent_dir = resolve_agent_dir(cfg.agents_root, agent_id)

        # --- Tier 1: per-agent DB lifecycle (spec §5.1) ---
        tier1_env_lines = ""
        if tier == "tier1":
            from dl_control.agents.provisioning.per_agent_db import (
                build_per_agent_dsn,
                create_per_agent_db,
            )
            from dl_control.per_agent_migrations.migrations import (
                apply_per_agent_migrations,
            )

            db_name, role_name, password = await create_per_agent_db(
                cfg.owner_dsn,
                agent_id=agent_id,
            )
            agent_dsn = build_per_agent_dsn(
                db_name,
                role_name,
                password,
            )
            await apply_per_agent_migrations(_admin_dsn_for_db(cfg.owner_dsn, db_name))
            # Update registry row with DB names.
            async with db.conn(user_id=actor_user_id, role="admin") as conn:
                await conn.execute(
                    "UPDATE agents SET per_agent_db_name = %s, "
                    "per_agent_db_role = %s WHERE id = %s",
                    (db_name, role_name, agent_id),
                )
            tier1_env_lines = (
                f"DL_AGENT_DB_DSN={config_gen._sh_single_quote(agent_dsn)}\n"
                f"REDIS_KEY_PREFIX={config_gen._sh_single_quote(f'agent:{agent_id}:')}\n"
                f"LLM_BASE_URL={config_gen._sh_single_quote('http://dl-llm-proxy:8080/v1')}\n"
            )

        # --- P5: per-library migrations (Tier 1 only) + secrets ---
        if tier == "tier1":
            from dl_control.per_library_migrations.migrations import (
                apply_per_library_migrations,
            )

            admin_dsn = _admin_dsn_for_db(cfg.owner_dsn, db_name)
            await apply_per_library_migrations(admin_dsn)
            # Write per-agent DB password to secrets directory (atomic, 0600).
            import os as _os

            secrets_dir = Path(cfg.agents_root) / "secrets" / "per_agent_db"
            secrets_dir.mkdir(parents=True, exist_ok=True)
            secret_path = secrets_dir / agent_id
            tmp = secret_path.with_suffix(secret_path.suffix + ".tmp")
            fd = _os.open(tmp, _os.O_WRONLY | _os.O_CREAT | _os.O_TRUNC, 0o600)
            try:
                _os.write(fd, password.encode())
            finally:
                _os.close(fd)
            tmp.rename(secret_path)

        # --- P5: mint DL_INTERNAL_TOKEN + auto-private library + skill_list ---
        import hashlib
        import secrets as secrets_mod

        dl_internal_token = secrets_mod.token_hex(32)
        token_hash = hashlib.sha256(dl_internal_token.encode()).digest()
        async with db.conn(user_id=actor_user_id, role="admin") as conn:
            await conn.execute(
                "UPDATE agents SET internal_token_hash = %s WHERE id = %s",
                (token_hash, agent_id),
            )

        short8 = agent_id.replace("-", "")[:8]
        library_slug = f"agent_{short8}_private"
        library_sensitivity = "restricted"
        if tier == "tier0":
            library_storage_kind = "shared"
            library_db_name = None
            library_db_role = None
        else:
            library_storage_kind = "isolated"
            library_db_name = db_name
            library_db_role = role_name

        async with db.conn(user_id=actor_user_id, role="admin") as conn:
            await conn.execute(
                "INSERT INTO knowledge_libraries "
                "(slug, display_name, sensitivity, storage_kind, "
                "per_library_db_name, per_library_db_role, owner_agent_id) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (slug) DO NOTHING",
                (
                    library_slug,
                    row["display_name"] + " Private",
                    library_sensitivity,
                    library_storage_kind,
                    library_db_name,
                    library_db_role,
                    agent_id,
                ),
            )
            cur = await conn.execute(
                "SELECT id FROM knowledge_libraries WHERE slug = %s",
                (library_slug,),
            )
            lib_row = await cur.fetchone()
            if lib_row is not None:
                library_id = lib_row[0]
                await conn.execute(
                    "DELETE FROM agent_library_access WHERE agent_id = %s AND library_id = %s",
                    (agent_id, library_id),
                )
                await conn.execute(
                    "INSERT INTO agent_library_access "
                    "(agent_id, library_id, access) VALUES (%s, %s, %s)",
                    (agent_id, library_id, "read_write"),
                )

        # Add cognee to skill_list if not present.
        skill_list: list[str] = row["skill_list"] or []
        if "cognee" not in skill_list:
            from psycopg.types.json import Jsonb

            skill_list.append("cognee")
            async with db.conn(user_id=actor_user_id, role="admin") as conn:
                await conn.execute(
                    "UPDATE agents SET skill_list = %s WHERE id = %s",
                    (Jsonb(skill_list), agent_id),
                )
            row["skill_list"] = skill_list

        # 3. Reconcile a pre-existing container (idempotent retry).
        existing = await docker.inspect_container(audit=audit, name=name)
        if existing is not None:
            await docker.remove_container(audit=audit, name=name, container_id=existing["Id"])
        # 4-7. Directories (symlink-safe), config set, workspace.
        mkdir_safe(cfg.agents_root, agent_dir)
        mkdir_safe(cfg.agents_root, agent_dir / "config")
        _generate_config_set(
            cfg,
            agent_dir,
            row,
            tier1_env_lines=tier1_env_lines,
            cognee_internal_token=dl_internal_token,
        )
        # Resolve seed overlay dir for precreated agents.
        overlay = None
        precreated_id = row.get("precreated_id")
        if precreated_id:
            candidate = Path(cfg.precreated_agents_root) / precreated_id / "workspace"
            if candidate.is_dir():
                overlay = candidate
        seed_workspace(cfg.templates_root, agent_dir, row, seed_overlay_dir=overlay)
        # 8. Create + persist the container id immediately, then start.
        container_id = await docker.create_container(
            audit=audit,
            name=name,
            image=cfg.openclaw_image,
            host_agent_dir=f"{cfg.host_agents_root.rstrip('/')}/{agent_id}",
            agent_id=agent_id,
            tier=tier,
        )
        async with db.conn(user_id=actor_user_id, role="admin") as conn:
            await registry.set_container_id(conn, agent_id, container_id=container_id)
        digest = await _resolve_openclaw_digest(docker, audit, container_id)
        if digest is not None:
            async with db.conn(user_id=actor_user_id, role="admin") as conn:
                await registry.set_openclaw_digest(conn, agent_id, digest=digest)
        await docker.start_container(audit=audit, name=name, container_id=container_id)
        # 9. Post-start liveness.
        if not await _passes_liveness(docker, audit, name, liveness_grace_seconds):
            raise ProvisioningError("liveness", "container did not stay healthy")
    except Exception as exc:  # noqa: BLE001 — every failure -> status error
        step = exc.step if isinstance(exc, ProvisioningError) else "provision"
        async with db.conn(user_id=actor_user_id, role="admin") as conn:
            await registry.set_status(conn, agent_id, status="error")
            await write_event(
                conn,
                actor_user_id=actor_user_id,
                action="agent_provision_failed",
                target=str(agent_id),
                meta={"step": step},
            )
        raise

    # Success.  Clear needs_restart so a credential-save-then-provision
    # workflow doesn't leave the flag stuck (Bug 1, spec §9.2.1).
    async with db.conn(user_id=actor_user_id, role="admin") as conn:
        await conn.execute(
            "UPDATE agents SET status = 'active', needs_restart = false WHERE id = %s",
            (agent_id,),
        )
        await write_event(
            conn,
            actor_user_id=actor_user_id,
            action="agent_provisioned",
            target=str(agent_id),
            meta={},
        )
    await _refresh_compose_mirror(db, cfg, actor_user_id)
    return "active"


async def reconcile_stale_provisioning(db: Database) -> list[str]:
    """Sweep agents stranded in 'provisioning' by a crash to 'error' (spec
    §9.4). Returns the swept agent ids. Run once at startup."""
    swept: list[str] = []
    async with db.conn(user_id=None, role="system") as conn:
        stale = await registry.list_agents_in_status(conn, "provisioning")
        for row in stale:
            await registry.set_status(conn, row["id"], status="error")
            await write_event(
                conn,
                actor_user_id=None,
                action="agent_provisioning_reconciled",
                target=row["id"],
                meta={"note": "reconciled stale provisioning on startup"},
            )
            swept.append(row["id"])
    return swept


async def reconcile_active_agents(
    db: Database,
    docker,
    cfg: ProvisioningConfig,
    *,
    actor_user_id: str | None = None,
    concurrency: int = 4,
) -> dict[str, str]:
    """Recover registry-active agents whose container is stopped/missing
    (spec SS4, Policy B). Best-effort: never raises; per-agent failures are
    isolated. Returns {agent_id: outcome} with outcome in
    {"running","recreated","failed"}."""
    results: dict[str, str] = {}
    audit = _make_audit(db, actor_user_id)
    sem = asyncio.Semaphore(concurrency)

    async def _one(agent: dict) -> None:
        agent_id = str(agent["id"])
        name = f"dato-agent-{agent_id}"
        async with sem:
            try:
                info = await docker.inspect_container(audit=audit, name=name)
                if info is not None and info.get("State", {}).get("Running") is True:
                    live_id = info.get("Id")
                    if live_id and agent.get("container_id") != live_id:
                        async with db.conn(user_id=None, role="system") as conn:
                            await registry.set_container_id(conn, agent_id, container_id=live_id)
                    results[agent_id] = "running"
                    return
                await provision_agent(
                    db,
                    docker,
                    cfg,
                    actor_user_id=actor_user_id,
                    agent_id=agent_id,
                    allowed_from=("active",) + _PROVISION_FROM,
                )
                results[agent_id] = "recreated"
                await write_event_safe(
                    db, actor_user_id, "agent_reconcile_recreated", target=agent_id, meta={}
                )
            except Exception as exc:  # noqa: BLE001 — isolate per-agent failure
                results[agent_id] = "failed"
                logger.warning("reconcile failed for %s: %s", agent_id, exc)
                await write_event_safe(
                    db,
                    actor_user_id,
                    "agent_reconcile_failed",
                    target=agent_id,
                    meta={"error": type(exc).__name__},
                )

    try:
        async with db.conn(user_id=None, role="system") as conn:
            actives = await registry.list_agents_in_status(conn, "active")
        if not actives:
            return results
        await write_event_safe(
            db,
            actor_user_id,
            "agent_reconcile_started",
            target="",
            meta={"candidates": len(actives)},
        )
        await asyncio.gather(*(_one(a) for a in actives))
        summary = {
            "running": sum(v == "running" for v in results.values()),
            "recreated": sum(v == "recreated" for v in results.values()),
            "failed": sum(v == "failed" for v in results.values()),
        }
        await write_event_safe(
            db, actor_user_id, "agent_reconcile_complete", target="", meta=summary
        )
        logger.info("active-agent reconcile complete: %s", summary)
    except Exception:  # noqa: BLE001 — never let the reconciler take down dl-control
        logger.exception("active-agent reconcile aborted")
    return results


# Files that form the regenerated config set (spec §9.3). config/.env is
# under the config/ subdir; the other two sit in the agent dir root.
_CONFIG_SET = ("openclaw.json", "skills.yaml", "config/.env")


def _config_set_paths(agent_dir: Path) -> list[Path]:
    return [agent_dir / rel for rel in _CONFIG_SET]


def _bak(path: Path) -> Path:
    """The .bak sibling of a managed file (openclaw.json -> openclaw.json.bak,
    .env -> .env.bak)."""
    return path.parent / (path.name + ".bak")


async def _recover_restart(
    db: Database,
    docker,
    cfg: ProvisioningConfig,
    audit,
    *,
    agent_id: str,
    name: str,
    container_id: str,
    agent_dir: Path,
    replaced: bool,
    backups_complete: bool,
    failing_step: str,
    grace: float,
    actor_user_id: str | None,
) -> str:
    """Post-stop recovery (spec §9.3.1). Returns the terminal status."""
    # (a) Stop the (possibly flapping) container and confirm it is quiescent.
    with contextlib.suppress(ProvisioningError):
        await docker.stop_container(audit=audit, name=name, container_id=container_id)
    # Confirm quiescence (spec §9.3.1 requires Running==false and
    # Restarting==false before touching config). If the container is
    # still alive (e.g. docker stop timed out), skip the restore and
    # go straight to error — we must not write config over a live mount.
    quiescent = False
    try:
        insp = await docker.inspect_container(audit=audit, name=name)
        if insp is not None:
            s = insp.get("State", {})
            if not s.get("Running") and not s.get("Restarting"):
                quiescent = True
    except ProvisioningError:
        pass
    # (b) If backups were NOT completed (symlink detected during backup), the
    # agent's config is compromised — never restart on unsafe config.
    if not backups_complete:
        async with db.conn(user_id=actor_user_id, role="admin") as conn:
            await registry.set_status(conn, agent_id, status="error")
            await write_event(
                conn,
                actor_user_id=actor_user_id,
                action="agent_restart_failed",
                target=str(agent_id),
                meta={
                    "step": failing_step,
                    "recovered": False,
                    "reason": "backup_failed_symlink_check",
                },
            )
        return "error"
    # (c) If the replace phase ran AND the container is confirmed stopped,
    # restore the ENTIRE .bak set.
    if replaced and quiescent:
        for path in _config_set_paths(agent_dir):
            bak = _bak(path)
            if bak.exists():
                copy_managed_file(bak, path, agent_dir=agent_dir)
    # (d) Start on the last-known-good config; check liveness.
    recovered = False
    try:
        await docker.start_container(audit=audit, name=name, container_id=container_id)
        recovered = await _passes_liveness(docker, audit, name, grace)
    except ProvisioningError:
        recovered = False
    status = "active" if recovered else "error"
    async with db.conn(user_id=actor_user_id, role="admin") as conn:
        if recovered:
            await conn.execute(
                "UPDATE agents SET status = 'active', needs_restart = false WHERE id = %s",
                (agent_id,),
            )
        else:
            await registry.set_status(conn, agent_id, status=status)
        await write_event(
            conn,
            actor_user_id=actor_user_id,
            action="agent_restart_failed",
            target=str(agent_id),
            meta={"step": failing_step, "recovered": recovered},
        )
    return status


async def restart_agent(
    db: Database,
    docker,
    cfg: ProvisioningConfig,
    *,
    actor_user_id: str | None,
    agent_id: str,
    liveness_grace_seconds: float = 3.0,
) -> str:
    """Regenerate the config set and restart the container (spec §9.3).
    Returns the terminal status. The restart stops the container first so the
    config files are quiescent while regenerated (no meta-write race)."""
    audit = _make_audit(db, actor_user_id)
    name = f"dato-agent-{agent_id}"

    async with db.conn(user_id=actor_user_id, role="admin") as conn:
        row = await registry.get_agent(conn, agent_id)
    if row is None:
        raise AgentNotFoundError(agent_id)
    if row["tier"] not in ("tier0", "tier1"):
        raise TierNotSupportedError(agent_id)
    container_id = row.get("container_id")
    if not container_id:
        raise AgentBusyError(f"{agent_id} has no container to restart")

    # CAS-claim: only an 'active' agent can be restarted.
    async with db.conn(user_id=actor_user_id, role="admin") as conn:
        claimed = await registry.claim_status(
            conn, agent_id, new_status="provisioning", allowed_from=("active",)
        )
        if not claimed:
            raise AgentBusyError(agent_id)
        await write_event(
            conn,
            actor_user_id=actor_user_id,
            action="agent_restart_started",
            target=str(agent_id),
            meta={},
        )

    agent_dir = resolve_agent_dir(cfg.agents_root, agent_id)
    replaced = False
    backups_complete = False
    try:
        # Re-validate the config/ directory is not a symlink (defense against
        # a restart-time race; spec §4.3, threat model §13).
        mkdir_safe(cfg.agents_root, agent_dir / "config")
        # Stop first — the config files become quiescent (spec §9.3 step 2).
        await docker.stop_container(audit=audit, name=name, container_id=container_id)
        # Back up the whole config set (spec §9.3 step 4). Track completion
        # separately: if a symlink is detected during backup, the agent must
        # NOT be restarted on potentially compromised config.
        for path in _config_set_paths(agent_dir):
            if path.exists():
                copy_managed_file(path, _bak(path), agent_dir=agent_dir)
        backups_complete = True
        # Mark the replace phase as entered BEFORE any live-file write. A
        # partial _generate_config_set (one file rewritten, the next blowing
        # up) must still trigger a full .bak-set restore (spec §9.3.1).
        replaced = True
        _generate_config_set(cfg, agent_dir, row)
        # Start + liveness.
        await docker.start_container(audit=audit, name=name, container_id=container_id)
        if not await _passes_liveness(docker, audit, name, liveness_grace_seconds):
            raise ProvisioningError("liveness", "container unhealthy after restart")
    except Exception as exc:  # noqa: BLE001 — recovery handles every post-stop failure
        step = exc.step if isinstance(exc, ProvisioningError) else "restart"
        final = await _recover_restart(
            db,
            docker,
            cfg,
            audit,
            agent_id=agent_id,
            name=name,
            container_id=container_id,
            agent_dir=agent_dir,
            replaced=replaced,
            backups_complete=backups_complete,
            failing_step=step,
            grace=liveness_grace_seconds,
            actor_user_id=actor_user_id,
        )
        if final != "active":
            # Unrecovered hard failure: surface as a ProvisioningError so the
            # API maps it to HTTP 500 (spec §10.1) instead of returning a
            # success response with status=error.
            raise ProvisioningError(
                "restart_recovery",
                "restart failed and the last-known-good config did not start",
            ) from exc
        return final

    async with db.conn(user_id=actor_user_id, role="admin") as conn:
        # P3: clear needs_restart on happy-path restart success only.
        # NOT done in _recover_restart — recovery restores old config.
        await conn.execute(
            "UPDATE agents SET status = 'active', needs_restart = false WHERE id = %s",
            (agent_id,),
        )
        # Sync DL_INTERNAL_TOKEN hash to DB — carry-forward from a restart
        # may preserve a token whose hash has drifted (e.g. manual .env edit
        # or a pre-P5 agent that never had a hash). Recompute on every
        # restart so the DB always matches the on-disk .env. Update happens
        # ONLY after liveness passes (spec §9.3 step 8c), so a failed start
        # that triggers _recover_restart does not leave the hash pointing
        # at the replacement config that was rolled back.
        token_hash = _env_dl_internal_token_hash(agent_dir)
        if token_hash is not None:
            await conn.execute(
                "UPDATE agents SET internal_token_hash = %s WHERE id = %s",
                (token_hash, agent_id),
            )
        await write_event(
            conn,
            actor_user_id=actor_user_id,
            action="agent_restarted",
            target=str(agent_id),
            meta={},
        )
    await _refresh_compose_mirror(db, cfg, actor_user_id)
    return "active"
