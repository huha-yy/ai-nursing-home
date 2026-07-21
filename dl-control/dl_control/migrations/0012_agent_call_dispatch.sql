-- 0012_agent_call_dispatch.sql
-- P13d — agent peer interface. Spec §5.6/§7.1: the unacked-dispatch repost
-- budget must be durable (an in-memory counter would reset every restart and
-- blind-repost forever). Idempotent DDL (0009 idiom): this file must survive
-- delivery through both the boot migrate one-shot (_schema_migrations) and
-- the OTA bundle channel (ota_migrations) — the trackers are not reconciled.
-- RLS policies and role grants on agent_call landed in 0011 and are unchanged.

ALTER TABLE agent_call ADD COLUMN IF NOT EXISTS dispatch_count int NOT NULL DEFAULT 0;
