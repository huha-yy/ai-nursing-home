"""P6 — per-agent rate limit wiring for dl-llm-proxy.

Reuses dl_shared.rate_limit.RateLimitMiddleware (sliding-window via Redis
sorted-set + Lua). Keyed by agent_id from request.state (set by auth middleware).
"""

from __future__ import annotations


def build_llm_proxy_rate_limit_key(request) -> str:
    agent_id = getattr(request.state, "agent_id", None)
    return f"llmproxy:agent:{agent_id}" if agent_id else ""
