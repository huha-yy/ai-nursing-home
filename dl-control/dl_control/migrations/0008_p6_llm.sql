-- P6 migration: dl-llm-local rollout
--
-- Adds two columns on `agents` to enable safe re-rendering of generated
-- config (openclaw.json + per-agent .env) on template-version bumps.
--
--   template_version   int       -- the template version the agent's config
--                                  was last rendered with. Bumped in code
--                                  when templates/openclaw.json.j2 changes
--                                  in a way that requires re-render.
--   last_rendered_hash text      -- sha256(rendered openclaw.json) at last
--                                  render. Reprovision skips agents whose
--                                  hash matches the current render (no-op).

ALTER TABLE agents
  ADD COLUMN IF NOT EXISTS template_version int NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS last_rendered_hash text;
