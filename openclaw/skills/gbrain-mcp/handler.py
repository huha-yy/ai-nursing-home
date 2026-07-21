"""gbrain-mcp skill handler — thin HTTP shim to GBrain MCP API.

Enables agents to search, read, and write the knowledge base through
conversation.  Follows the same pattern as cognee/handler.py: the skill
is registered but not callable as a tool; agents use ``process`` to
``python3 -c`` import.

Authentication (priority order):
1. OAuth client_credentials — if GBRAIN_CLIENT_ID + GBRAIN_CLIENT_SECRET are set
   (auto-refreshes expired tokens)
2. Static bearer token — GBRAIN_API_KEY env var
3. File fallback — /run/secrets/gbrain_api_key
"""

import json
import os
import re
import sys
import time

import httpx

_GBRAIN_URL = os.environ.get("GBRAIN_URL", "http://dl-gbrain:8080")

# Lazily-loaded lint schema (loaded once on first put_page)
_LINT_SCHEMA: dict | None = None
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)


def _parse_frontmatter_dict(content: str) -> dict | None:
    """Parse YAML frontmatter into a dict using regex (no pyyaml dependency)."""
    m = _FRONTMATTER_RE.search(content)
    if not m:
        return None
    raw = m.group(1)
    result = {}
    for line in raw.splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            key = key.strip().lower()
            val = val.strip()
            result[key] = val
    return result


def _get_lint_schema() -> dict | None:
    global _LINT_SCHEMA
    if _LINT_SCHEMA is not None:
        return _LINT_SCHEMA
    try:
        _scripts = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "scripts",
        )
        if _scripts not in sys.path:
            sys.path.insert(0, _scripts)
        from lint_frontmatter import load_schema  # noqa: E402
        _LINT_SCHEMA = load_schema()
    except Exception:
        _LINT_SCHEMA = {}
    return _LINT_SCHEMA or None

# OAuth token cache
_OAUTH_TOKEN: str | None = None
_OAUTH_TOKEN_EXPIRES_AT: float = 0.0


def _api_key() -> str:
    """Resolve the bearer token.

    Priority: OAuth client_credentials → static key env var → file fallback.
    """
    client_id = os.environ.get("GBRAIN_CLIENT_ID")
    client_secret = os.environ.get("GBRAIN_CLIENT_SECRET")
    if client_id and client_secret:
        return _oauth_token(client_id, client_secret)

    key = os.environ.get("GBRAIN_API_KEY")
    if key:
        return key
    try:
        with open("/run/secrets/gbrain_api_key") as f:
            return f.read().strip()
    except OSError:
        pass
    raise RuntimeError(
        "No GBrain credentials found. Set GBRAIN_CLIENT_ID + GBRAIN_CLIENT_SECRET, "
        "GBRAIN_API_KEY, or mount /run/secrets/gbrain_api_key."
    )


def _oauth_token(client_id: str, client_secret: str) -> str:
    """Get (or refresh) an OAuth access token via client_credentials grant."""
    global _OAUTH_TOKEN, _OAUTH_TOKEN_EXPIRES_AT

    # Return cached token if still valid (with 60s buffer)
    now = time.time()
    if _OAUTH_TOKEN and _OAUTH_TOKEN_EXPIRES_AT > now + 60:
        return _OAUTH_TOKEN

    with httpx.Client(base_url=_GBRAIN_URL, timeout=httpx.Timeout(10.0)) as client:
        resp = client.post(
            "/token",
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
                "scope": "read write",
            },
        )
        resp.raise_for_status()
        data = resp.json()
        _OAUTH_TOKEN = data["access_token"]
        _OAUTH_TOKEN_EXPIRES_AT = now + data.get("expires_in", 3600)
        return _OAUTH_TOKEN


def _mcp_call(method: str, arguments: dict | None = None) -> dict:
    """Make an MCP tool call to GBrain.

    Returns the parsed result dict.  Raises on HTTP/transport errors.
    """
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": method,
            "arguments": arguments or {},
        },
    }
    with httpx.Client(
        base_url=_GBRAIN_URL,
        headers={
            "Authorization": f"Bearer {_api_key()}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
        timeout=httpx.Timeout(10.0, read=30.0),
    ) as client:
        resp = client.post("/mcp", json=payload)
        resp.raise_for_status()
        return _parse_sse_response(resp.text)


def _parse_sse_response(text: str) -> dict:
    """Extract the JSON payload from an SSE ``data:`` line."""
    for line in text.splitlines():
        if line.startswith("data: "):
            raw = line.removeprefix("data: ")
            return json.loads(raw)
    return json.loads(text)


def search(query: str, limit: int = 5) -> list[dict]:
    """Search the GBrain knowledge base.

    Parameters:
    - query (string, required): The search query in Chinese or English.
    - limit (int, optional, default=5): Max results to return (1-20).

    Returns: list of {slug, title, type, score, chunk_text}
    """
    result = _mcp_call("search", {"query": query, "limit": limit})
    content = result.get("result", {}).get("content", [])
    if not content:
        return []
    raw = content[0].get("text", "[]")
    return json.loads(raw)


def get_page(slug: str) -> dict | None:
    """Read a page from the GBrain knowledge base by slug.

    Parameters:
    - slug (string, required): The page slug (e.g. "daien/product/care_robot").

    Returns: {slug, title, type, content, updated_at, ...} or None if not found.
    """
    try:
        result = _mcp_call("get_page", {"slug": slug})
    except Exception:
        return None
    content = result.get("result", {}).get("content", [])
    if not content:
        return None
    raw = content[0].get("text", "{}")
    return json.loads(raw)


def put_page(slug: str, content: str) -> dict:
    """Write or update a page in the GBrain knowledge base.

    Parameters:
    - slug (string, required): Page slug (e.g. "daien/product/care_robot_v2").
    - content (string, required): Full markdown with YAML frontmatter.

    Returns: dict with result fields (slug, title, etc.) or empty dict on error.
    """
    # --- lint check before write ---
    _schema = _get_lint_schema()
    if _schema:
        _fm = _parse_frontmatter_dict(content)
        if _fm:
            from lint_frontmatter import validate_frontmatter_dict
            _type = _fm.get("type", "")
            if _type in _schema:
                _miss_req, _miss_opt, _ = validate_frontmatter_dict(_fm, _type, _schema)
                if _miss_req:
                    print(f"[lint] ❌ 拒绝写入 {slug} — 缺少必填字段: {', '.join(_miss_req)}", file=sys.stderr)
                    return {}
                if _miss_opt:
                    print(f"[lint] ⚠️ 写入 {slug} — 缺少可选字段: {', '.join(_miss_opt)}", file=sys.stderr)
    # --- end lint check ---

    try:
        result = _mcp_call("put_page", {"slug": slug, "content": content})
    except Exception:
        return {}
    content_list = result.get("result", {}).get("content", [])
    if not content_list:
        return {}
    raw = content_list[0].get("text", "{}")
    return json.loads(raw)
