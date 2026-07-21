<!-- dato-prod-tree: do not edit on the appliance -->

# AGENTS.md — Operating manual for AI agents on the dato appliance

This file is the source of truth for any AI agent performing maintenance
or recovery on a customer appliance. The dev repo's AGENTS.md (which
describes the multi-agent development workflow) is stripped by the
export process — it must never reach the appliance.

## 0. Read these first, in order

1. The runbook matching the task under `docs/runbooks/`.
2. `cat VERSION` to read the installed source commit. `git log -1` shows
   the same SHA in the `Source-Commit:` trailer. There are no version
   tags on this repo — every export is a single squashed commit.

## 1. What this appliance is

`dato` is a productized, on-premise multi-agent appliance built on
OpenClaw. It runs on dedicated hardware and is managed from a single
admin web UI. The system self-updates over the air via the dato OTA
channel.

## 2. Repo layout (top level)

```
dato/
├── AGENTS.md
├── README.md
├── VERSION                ← single line: 40-char source dev commit SHA
├── Makefile
├── pyproject.toml
├── uv.lock
├── dl-control/            ← admin web UI + agent registry
├── dl-llm-proxy/          ← LLM passthrough + tier-1 guardrail
├── dl-cognee/             ← per-agent knowledge graph
├── dl-llm-local/          ← bundled local model (Ollama)
├── dl-ota-watcher/        ← OTA self-update watcher
├── dl-egress-dns/         ← DNS-level LLM-deny guardrail
├── dl_shared/             ← shared library code
├── openclaw/              ← the dato OpenClaw image source
├── infra/                 ← compose, Caddy, Postgres, Redis
├── templates/             ← workspace templates
├── tests/                 ← top-level smoke tests
└── docs/runbooks/         ← operator how-tos
```

## 3. Commands

| Need                       | Command                |
|----------------------------|------------------------|
| Bring up the stack         | `make up`              |
| Tear down                  | `make down`            |
| Restart                    | `make restart`         |
| Tail logs                  | `make logs`            |
| psql into Postgres         | `make psql`            |
| redis-cli                  | `make redis-cli`      |
| Run smoke tests            | `make smoke`           |
| Tail dl-control logs       | `make control-logs`    |
| Tail dl-cognee logs        | `make cognee-logs`     |

## 4. Conventions

- **Do not modify this repo directly on the appliance.** The OTA watcher
  manages updates. Manual changes will be overwritten by the next OTA
  cycle.
- **Secrets never enter git.** `infra/.env` is gitignored; use
  `infra/.env.example` as a template.
- **Source-commit identity is in `VERSION` and in the squashed commit's
  `Source-Commit:` trailer.** There are no version tags.

## 5. When in doubt

- **Appliance not behaving?** Check `make ps` and `make logs`.
- **Need to recover?** See `docs/runbooks/`.
- **Security concern?** Contact your dato vendor immediately.
