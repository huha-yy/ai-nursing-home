"""cognee skill handler — thin HTTP shim to dl-cognee."""

import os

import httpx

DL_INTERNAL_TOKEN = os.environ["DL_INTERNAL_TOKEN"]
DL_COGNEE_URL = os.environ.get("DL_COGNEE_URL", "http://dl-cognee:8080")


def _client():
    return httpx.Client(
        base_url=DL_COGNEE_URL,
        headers={"Authorization": f"Bearer {DL_INTERNAL_TOKEN}"},
        timeout=httpx.Timeout(5.0, read=30.0),
    )


def add(content: str, path: str | None = None, library: str | None = None):
    """Ingest content into a knowledge library."""
    body: dict = {"content": content}
    if path:
        body["path"] = path
    if library:
        body["library_slug"] = library
    with _client() as c:
        r = c.post("/v1/ingest", json=body)
        r.raise_for_status()
        return r.json()


def search(query: str, limit: int = 5, library_slugs: list[str] | None = None):
    """Search across knowledge libraries."""
    body: dict = {"query": query, "limit": limit}
    if library_slugs:
        body["library_slugs"] = library_slugs
    with _client() as c:
        r = c.post("/v1/search", json=body)
        r.raise_for_status()
        return r.json()
