CREATE TABLE IF NOT EXISTS _per_agent_schema_migrations (
    version     INT PRIMARY KEY,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS audit_log_mirror (
    id                  BIGSERIAL PRIMARY KEY,
    source_audit_log_id BIGINT NOT NULL UNIQUE,
    actor_user_id       UUID,
    action              TEXT NOT NULL,
    target              TEXT NOT NULL,
    meta                JSONB,
    mirrored_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
