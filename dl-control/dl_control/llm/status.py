"""P6 — LLM status helper: probes ollama + proxy, assembles dashboard payload."""

from __future__ import annotations

import httpx


async def get_llm_status(
    *,
    model_name: str = "qwen3.5:9b",
    keep_alive_seconds: int = 1800,
) -> dict:
    """Probe dl-llm-local (Ollama /api/tags) and dl-llm-proxy /healthz.

    Returns a dict suitable for the /api/internal/llm-status endpoint
    and the dashboard widget.
    """
    result = {
        "proxy_healthy": False,
        "ollama_reachable": False,
        "model_loaded": False,
        "model_name": model_name,
        "keep_alive_seconds": keep_alive_seconds,
        "last_request_at": None,
        "request_count": 0,
    }

    # Probe dl-llm-proxy /healthz (always at internal address).
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=2.0)) as c:
            r = await c.get("http://dl-llm-proxy:8080/healthz")
            if r.status_code == 200:
                data = r.json()
                result["proxy_healthy"] = data.get("status") == "ok"
                result["last_request_at"] = data.get("last_request_at")
                result["request_count"] = data.get("request_count", 0)
    except httpx.RequestError:
        pass

    # Probe dl-llm-local /api/tags.
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=2.0)) as c:
            r = await c.get("http://dl-llm-local:11434/api/tags")
            if r.status_code == 200:
                result["ollama_reachable"] = True
                data = r.json()
                model_names = {m.get("name") for m in data.get("models", [])}
                # Ollama reports models with ":latest" suffix sometimes.
                result["model_loaded"] = (
                    model_name in model_names or f"{model_name}:latest" in model_names
                )
    except httpx.RequestError:
        pass

    return result
