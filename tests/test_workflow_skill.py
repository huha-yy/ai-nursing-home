"""P13d — the agent→workflow OpenClaw skill (spec §7.2): tool shapes, auth
header, and await_workflow's poll-until-terminal contract."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import httpx
import pytest

HANDLER = Path(__file__).parent.parent / "openclaw" / "skills" / "workflow" / "handler.py"


@pytest.fixture
def handler(monkeypatch):
    monkeypatch.setenv("DL_INTERNAL_TOKEN", "agent-token")
    monkeypatch.setenv("DL_CONTROL_URL", "http://dato-control:8080")
    spec = importlib.util.spec_from_file_location("workflow_handler", HANDLER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _mock(handler, monkeypatch, responder):
    def client():
        return httpx.Client(
            base_url="http://dato-control:8080",
            headers={"Authorization": "Bearer agent-token"},
            transport=httpx.MockTransport(responder),
        )

    monkeypatch.setattr(handler, "_client", client)


def test_start_workflow_posts_grant_protected_endpoint(handler, monkeypatch):
    seen = {}

    def responder(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.read())
        return httpx.Response(201, json={"run_id": "r-1"})

    _mock(handler, monkeypatch, responder)
    doc = handler.start_workflow(
        "hr.onboarding_email", input={"employee_id": "e1"}, correlation_key="e1"
    )
    assert doc == {"run_id": "r-1"}
    assert seen["url"].endswith("/api/v1/workflows/hr.onboarding_email/runs")
    assert seen["auth"] == "Bearer agent-token"
    assert seen["body"] == {"input": {"employee_id": "e1"}, "correlation_key": "e1"}


def test_get_workflow_status(handler, monkeypatch):
    def responder(request):
        assert request.url.path == "/api/v1/workflow-runs/r-2"
        return httpx.Response(200, json={"run_id": "r-2", "status": "running"})

    _mock(handler, monkeypatch, responder)
    assert handler.get_workflow_status("r-2")["status"] == "running"


def test_await_workflow_polls_until_terminal(handler, monkeypatch):
    statuses = iter(["pending", "running", "succeeded"])

    def responder(request):
        return httpx.Response(
            200, json={"run_id": "r-3", "status": next(statuses), "output": {"ok": True}}
        )

    _mock(handler, monkeypatch, responder)
    monkeypatch.setattr(handler.time, "sleep", lambda s: None)
    doc = handler.await_workflow("r-3", timeout_s=60, poll_s=0)
    assert doc["status"] == "succeeded"


def test_await_workflow_times_out(handler, monkeypatch):
    def responder(request):
        return httpx.Response(200, json={"run_id": "r-4", "status": "running"})

    _mock(handler, monkeypatch, responder)
    monkeypatch.setattr(handler.time, "sleep", lambda s: None)
    clock = iter(range(0, 10_000, 100))
    monkeypatch.setattr(handler.time, "monotonic", lambda: next(clock))
    with pytest.raises(TimeoutError):
        handler.await_workflow("r-4", timeout_s=50, poll_s=1)


def test_http_errors_surface(handler, monkeypatch):
    _mock(handler, monkeypatch, lambda r: httpx.Response(403))
    with pytest.raises(httpx.HTTPStatusError):
        handler.start_workflow("not.granted")
