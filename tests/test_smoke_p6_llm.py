"""P6 — smoke test for dl-llm-local end-to-end (requires compose stack)."""

from __future__ import annotations

import pytest


@pytest.mark.docker
def test_smoke_p6_placeholder():
    """Placeholder — requires a running compose stack with dl-llm-local.

    Full test per spec §11.2: provisions a Tier 1 agent, verifies
    ollama_reachable, runs a /v1/chat/completions call through the proxy.
    """
    pass
