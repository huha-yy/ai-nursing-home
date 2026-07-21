# dl-llm-local image provenance

| Field | Value |
|---|---|
| Upstream | `ollama/ollama:24.04` |
| Digest | `sha256:a6149234667efc71d37766d61c1a16f24c33e4cd7a0bf4125c44a7e47e2419c4` |
| Pinned date | 2026-05-24 |
| Baked model | `qwen3.5:9b` |
| Patches applied | curl ca-certificates installed via apt (upstream image ships neither curl nor wget) |
| Healthcheck tool | `curl` (apt-installed in Dockerfile; upstream image has neither) |

## Substitution log

If `qwen3.5:9b` is unavailable at build time, fall back in order:
1. `qwen3:8b`
2. `qwen2.5:7b`

Record the substitution here (date, tag picked, reason):

| Date | Original | Substituted | Reason |
|---|---|---|---|
| (none yet) | | | |

## Notes
- Ollama auto-detects CUDA / Metal / CPU at runtime. The image carries no
  hardware-specific build.
- Model is baked at build time (D-P6-4); the entrypoint only verifies.
- Provenance file format mirrors `openclaw/PROVENANCE.md` (P0).
