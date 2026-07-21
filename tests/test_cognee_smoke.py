"""P5 dl-cognee end-to-end smoke tests.

Run via `make smoke` — requires the full compose stack to be up.
Tests use fixtures from `tests/conftest.py` to reach dl-cognee via docker exec proxy.
"""

from __future__ import annotations

import json
import subprocess

import pytest


class _DockerExecClient:
    """httpx-like client that proxies requests to dl-cognee via docker exec."""

    def __init__(self, container: str = "dato-control", target_url: str = "http://dl-cognee:8080"):
        self._container = container
        self._target_url = target_url

    def _curl(
        self, method: str, path: str, *, body: dict | None = None, headers: dict | None = None
    ) -> tuple[int, str]:
        url = f"{self._target_url}{path}"
        cmd = [
            "docker",
            "exec",
            self._container,
            "curl",
            "-s",
            "-w",
            "\n%{http_code}",
            "-o",
            "-",
            "-X",
            method,
            url,
        ]
        for k, v in (headers or {}).items():
            cmd.extend(["-H", f"{k}: {v}"])
        if body is not None:
            cmd.extend(["-H", "Content-Type: application/json"])
            cmd.extend(["-d", json.dumps(body)])
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        output = result.stdout.rstrip("\n")
        # Last line is the HTTP status code.
        lines = output.split("\n")
        status_code = int(lines[-1])
        body_text = "\n".join(lines[:-1])
        return status_code, body_text

    def get(self, path: str) -> _Response:
        code, body = self._curl("GET", path)
        return _Response(code, body)

    def post(
        self, path: str, *, json: dict | None = None, headers: dict | None = None
    ) -> _Response:
        code, body = self._curl("POST", path, body=json, headers=headers)
        return _Response(code, body)


class _Response:
    def __init__(self, status_code: int, text: str):
        self.status_code = status_code
        self._text = text

    def json(self):
        return json.loads(self._text)


@pytest.fixture
def cognee_client():
    return _DockerExecClient()


@pytest.mark.smoke
def test_cognee_health(cognee_client):
    """dl-cognee /health returns ok after startup."""
    r = cognee_client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] in ("ok", "starting")
