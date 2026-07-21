"""P13d — agent-side task receiver (D-P13D-1/2/3): auth, ack shape,
correlation dedup (the §5.6 normative contract), task execution via the
command seam, and callback delivery with the per-agent token."""

from __future__ import annotations

import importlib.util
import json
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

import pytest

RECEIVER = Path(__file__).parent.parent / "openclaw" / "receiver" / "dato_task_receiver.py"

# A fake agent command: bumps a counter file (execution count is how dedup is
# observed) and prints a JSON line, exercising the --json parse path. The
# {message} placeholder is consumed as a trailing argv element.
FAKE_CMD_TEMPLATE = (
    "{python} -c "
    '"import pathlib,sys;'
    "p=pathlib.Path(sys.argv[1]);"
    "p.write_text(str(int(p.read_text() or '0')+1) if p.exists() else '1');"
    "print('noise');"
    'print(\'{{\\"answer\\": 42}}\')" '
    "{counter} {message}"
)


class _ControlStub(BaseHTTPRequestHandler):
    """Captures agent callbacks; scripted status codes."""

    callbacks: list[dict] = []
    status = 200

    def do_POST(self):  # noqa: N802 — BaseHTTPRequestHandler API
        length = int(self.headers.get("Content-Length", "0"))
        doc = json.loads(self.rfile.read(length))
        doc["_auth"] = self.headers.get("Authorization")
        doc["_path"] = self.path
        type(self).callbacks.append(doc)
        self.send_response(type(self).status)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, fmt, *args):  # noqa: A003
        pass


@pytest.fixture
def control_server():
    _ControlStub.callbacks = []
    _ControlStub.status = 200
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ControlStub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()


@pytest.fixture
def receiver(monkeypatch, tmp_path, control_server):
    counter = tmp_path / "exec-count"
    fake_cmd = FAKE_CMD_TEMPLATE.format(python=sys.executable, counter=counter, message="{message}")
    monkeypatch.setenv("DL_INTERNAL_TOKEN", "agent-token")
    monkeypatch.setenv("DATO_TASK_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("DATO_TASK_AGENT_CMD", fake_cmd)
    monkeypatch.setenv("DL_CONTROL_URL", f"http://127.0.0.1:{control_server.server_port}")
    monkeypatch.setenv("DATO_TASK_TIMEOUT_SECONDS", "30")
    spec = importlib.util.spec_from_file_location("dato_task_receiver", RECEIVER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.CALLBACK_BACKOFF_SECONDS = 0.05  # fast retries under test
    mod.REDELIVERY_SCAN_INTERVAL = 0.2  # fast scan under test
    mod.STATE_DIR.mkdir(parents=True, exist_ok=True)
    threading.Thread(target=mod._redeliver_pending, daemon=True).start()
    server = ThreadingHTTPServer(("127.0.0.1", 0), mod.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield mod, server.server_port, counter
    server.shutdown()


def _post(port, body, *, token="agent-token"):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/dato/task",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def _task(corr=None):
    return {
        "correlation_id": str(corr or uuid4()),
        "run_id": str(uuid4()),
        "step_key": "ask",
        "message": "hello world",
    }


def _wait_for(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def test_auth_required(receiver):
    _mod, port, _counter = receiver
    status, _ = _post(port, _task(), token="wrong")
    assert status == 401


def test_bad_payload_is_400(receiver):
    _mod, port, _counter = receiver
    status, _ = _post(port, {"message": "no correlation"})
    assert status == 400
    status, _ = _post(port, {"correlation_id": "../escape", "message": "m"})
    assert status == 400


def test_ack_runs_task_and_delivers_callback(receiver):
    _mod, port, counter = receiver
    corr = uuid4()
    status, doc = _post(port, _task(corr))
    assert status == 202
    assert doc == {"ack": True, "correlation_id": str(corr), "duplicate": False}
    assert _wait_for(lambda: _ControlStub.callbacks)
    cb = _ControlStub.callbacks[0]
    assert cb["_path"] == "/api/internal/workflows/agent-callback"
    assert cb["_auth"] == "Bearer agent-token"
    assert cb["correlation_id"] == str(corr)
    assert cb["status"] == "ok"
    assert cb["result"] == {"answer": 42}  # last JSON line wins
    assert counter.read_text() == "1"


def test_duplicate_post_is_deduped(receiver):
    _mod, port, counter = receiver
    corr = uuid4()
    _post(port, _task(corr))
    assert _wait_for(lambda: _ControlStub.callbacks)
    status, doc = _post(port, _task(corr))
    assert status == 202 and doc["duplicate"] is True
    time.sleep(0.3)
    assert counter.read_text() == "1"  # the task ran exactly once


def test_failed_command_reports_error(receiver, monkeypatch):
    mod, port, _counter = receiver
    monkeypatch.setattr(mod, "AGENT_CMD", f'{sys.executable} -c "import sys; sys.exit(3)"')
    _post(port, _task())
    assert _wait_for(lambda: _ControlStub.callbacks)
    assert _ControlStub.callbacks[0]["status"] == "error"


def test_stale_409_stops_retrying_and_marks_delivered(receiver):
    mod, port, _counter = receiver
    _ControlStub.status = 409
    corr = uuid4()
    _post(port, _task(corr))
    state_file = mod.STATE_DIR / f"{corr}.json"
    assert _wait_for(
        lambda: state_file.exists() and json.loads(state_file.read_text()).get("status") == "done"
    )
    state = json.loads(state_file.read_text())
    assert state["delivered"] is True  # 409 = stale; stop retrying
    assert len(_ControlStub.callbacks) == 1  # no retry storm


def test_malformed_command_reports_error(receiver, monkeypatch):
    mod, port, _counter = receiver
    monkeypatch.setattr(mod, "AGENT_CMD", "node 'unclosed quote")
    _post(port, _task())
    assert _wait_for(lambda: _ControlStub.callbacks)
    cb = _ControlStub.callbacks[0]
    assert cb["status"] == "error"
    assert "unparseable" in cb.get("error", "")


def test_background_redelivery_recovers_undelivered(receiver):
    mod, port, _counter = receiver
    _ControlStub.status = 503  # callback always fails
    corr = uuid4()
    _post(port, _task(corr))
    state_file = mod.STATE_DIR / f"{corr}.json"
    assert _wait_for(
        lambda: (
            state_file.exists()
            and json.loads(state_file.read_text()).get("status") == "done"
            and json.loads(state_file.read_text()).get("delivered") is False
        )
    )
    _ControlStub.status = 200  # recovery — control is back
    assert _wait_for(
        lambda: json.loads(state_file.read_text()).get("delivered") is True, timeout=15.0
    )  # generous for scan interval
    assert len(_ControlStub.callbacks) >= 2  # at least one failed + one success
