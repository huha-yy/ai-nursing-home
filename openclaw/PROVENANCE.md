# dato OpenClaw Image — Provenance

## Pinned upstream

| Field | Value |
|-------|-------|
| **Image** | `ghcr.io/openclaw/openclaw` |
| **Pinned version** | `2026.4.8` |
| **Digest** | (populated at build time via `docker inspect`) |

## Patch set

| File | Purpose | Size | Source |
|------|---------|------|--------|
| `patches/feishu-reply-dispatcher.ts` | Error messages on LLM failure, streaming cards | 537 lines | `openclaw-mvp` |
| `patches/feishu-bot.ts` | Broadcast, dynamic agent creation, merge-forward | 1308 lines | `openclaw-mvp` |

## Version derivation

The dato image tag is derived as: `dato-openclaw:<pinned-upstream-version>[-patch<N>]`

- `dato-openclaw:2026.4.8` — first dato build from upstream tag `2026.4.8`.
- If patches are revised without bumping the upstream version: `dato-openclaw:2026.4.8-patch1`, etc.

## Patch reconciliation on upstream version bump

When the pinned OpenClaw version is bumped:
1. Diff the new base image's extension files against the old base image's files.
2. Identify which patch hunks still apply cleanly and which conflict.
3. Re-apply applicable hunks; resolve conflicts by inspecting the new upstream code.
4. Update the patch files in `openclaw/patches/`.
5. Update this `PROVENANCE.md` with the new pinned version and digest.
6. Run the patched image through Feishu integration smoke tests (P3).

## Rollback

If a newly-built dato image fails to start or malfunctions:
1. Revert the `FROM` line in `openclaw/Dockerfile` to the last known-good tag.
2. Revert the patch files to the last known-good state.
3. Rebuild and restart.
4. The previous `openclaw.json.bak` (per §5.3 of the design spec) provides a
   one-step config rollback if the failure is config-related.

## Compatibility

Before deploying a new dato image:
1. Validate that the image's OpenClaw version is compatible with the
   `openclaw.json` schema version expected by existing agent configs.
2. Check the OpenClaw changelog for breaking changes between versions.
3. Run the `openclaw.json` generation end-to-end against a test agent config.
