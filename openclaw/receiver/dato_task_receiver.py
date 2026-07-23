#!/usr/bin/env python3
"""dato task receiver — the agent-side task-intake endpoint (workflow spec §7).

Runs as a sidecar process inside the dato OpenClaw container, started by the
entrypoint wrapper. Stdlib only — no extra dependencies in the agent image.

Normative contract (spec §5.6):
- intake DEDUPS on correlation_id: a re-post of a seen id is a no-op that
  re-returns the same ack (and re-delivers a stored result whose callback
  never succeeded) — dedup state is one JSON file per correlation under the
  mounted agent home, so it survives container restarts;
- auth both ways is the per-agent DL_INTERNAL_TOKEN: constant-time compare on
  intake, presented as Bearer on the result callback (D-P13D-3).

The agent turn itself runs through DATO_TASK_AGENT_CMD (D-P13D-2) — the one
OpenClaw coupling, deliberately env-overridable. {message} is substituted as
a single argv element (no shell)."""

from __future__ import annotations

import hmac
import json
import logging
import os
import shlex
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s dato-task-receiver %(levelname)s %(message)s"
)
logger = logging.getLogger("dato_task_receiver")

TOKEN = os.environ.get("DL_INTERNAL_TOKEN", "")
PORT = int(os.environ.get("DATO_TASK_RECEIVER_PORT", "18790"))
CONTROL_URL = os.environ.get("DL_CONTROL_URL", "http://dato-control:8080").rstrip("/")
AGENT_CMD = os.environ.get(
    "DATO_TASK_AGENT_CMD", "openclaw agent --json --session-id dato --message {message}"
)
STATE_DIR = Path(
    os.environ.get("DATO_TASK_STATE_DIR", str(Path.home() / ".openclaw" / "dato-tasks"))
)
TASK_TIMEOUT = int(os.environ.get("DATO_TASK_TIMEOUT_SECONDS", "1500"))
CALLBACK_RETRIES = 5
CALLBACK_BACKOFF_SECONDS = 5.0
REDELIVERY_SCAN_INTERVAL = 60.0


def _state_path(correlation_id: str) -> Path:
    return STATE_DIR / f"{correlation_id}.json"


def _write_state(correlation_id: str, state: dict) -> None:
    path = _state_path(correlation_id)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state))
    tmp.replace(path)


def _try_extract_json(text: str) -> dict | None:
    """Best-effort extraction of a JSON dict from agent CLI output.

    Tries, in order:
    1. Direct json.loads on the full text.
    2. Backward line scan (the current approach) but only lines that begin and
       end with braces to reduce false positives.
    3. Unescape double-escaped strings (common OpenClaw `--json` artifact where
       the LLM output is re-encoded as a JSON string within a JSON field).
    Returns None when nothing parseable is found.
    """
    # 1. Direct parse
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, TypeError):
        pass

    # 2. Backward line scan — look for lines bookended by braces
    for line in reversed(text.splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                data = json.loads(line)
                if isinstance(data, dict):
                    return data
            except json.JSONDecodeError:
                continue

    # 3. Unescape double-escaped strings (e.g. \\n → real newline, \\\" → ")
    if "\\\\n" in text or '\\\\"' in text:
        try:
            unescaped = text.encode("utf-8").decode("unicode_escape")
            for line in reversed(unescaped.splitlines()):
                line = line.strip()
                if line.startswith("{") and line.endswith("}"):
                    data = json.loads(line)
                    if isinstance(data, dict):
                        return data
        except Exception:
            pass

    return None


def run_task(message: str, session_id: str | None = None) -> dict:
    """One agent turn via the configured command. Returns the §7.1 callback
    payload: ok + the last parseable JSON line of stdout (the --json shape),
    falling back to raw text; nonzero exit / timeout → error. Reads AGENT_CMD
    at call time (tests monkeypatch the module global).

    session_id: if provided, replaces --session-id in the command with a
    unique value so each task gets a fresh agent context (avoids stale
    conversation history poisoning responses)."""
    try:
        parts = shlex.split(AGENT_CMD)
    except ValueError as exc:
        return {"status": "error", "error": f"agent command unparseable: {exc}"}
    # Replace --session-id value with a unique per-task session to avoid
    # accumulated conversation context from previous failed runs.
    if session_id:
        final_parts = []
        skip_next = False
        for i, part in enumerate(parts):
            if skip_next:
                skip_next = False
                continue
            if part == "--session-id" and i + 1 < len(parts):
                final_parts.append(part)
                final_parts.append(session_id)
                skip_next = True
            else:
                final_parts.append(part)
        parts = final_parts
    argv = [message if part == "{message}" else part for part in parts]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=TASK_TIMEOUT)
    except subprocess.TimeoutExpired:
        return {"status": "error", "error": f"agent task timed out after {TASK_TIMEOUT}s"}
    except OSError as exc:
        return {"status": "error", "error": f"agent command failed to start: {exc}"}
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "agent command failed").strip()
        return {"status": "error", "error": detail[-2000:]}
    out = proc.stdout.strip()
    # Try to extract a structured JSON dict from the output
    result = _try_extract_json(out)
    if result is not None:
        return {"status": "ok", "result": result}
    return {"status": "ok", "result": {"text": out[-8000:]}}


def deliver_callback(correlation_id: str, payload: dict) -> bool:
    """POST the result to dl-control. True = delivered OR provably stale
    (409 — dl-control will never accept it; stop retrying, §5.6)."""
    body = json.dumps({"correlation_id": correlation_id, **payload}).encode()
    for i in range(CALLBACK_RETRIES):
        req = urllib.request.Request(
            f"{CONTROL_URL}/api/internal/workflows/agent-callback",
            data=body,
            headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status // 100 == 2:
                    return True
        except urllib.error.HTTPError as exc:
            if exc.code == 409:
                logger.info("callback stale (correlation=%s)", correlation_id)
                return True
            logger.warning("callback HTTP %s (correlation=%s)", exc.code, correlation_id)
        except OSError as exc:
            logger.warning("callback failed (correlation=%s): %s", correlation_id, exc)
        time.sleep(CALLBACK_BACKOFF_SECONDS * (i + 1))
    return False


def worker(correlation_id: str, message: str) -> None:
    # Use a unique session per task to avoid stale conversation context
    # from previous runs poisoning the agent's responses.
    session_id = f"task-{correlation_id[:8]}"
    payload = run_task(message, session_id=session_id)
    delivered = deliver_callback(correlation_id, payload)
    _write_state(correlation_id, {"status": "done", "payload": payload, "delivered": delivered})


def redeliver(correlation_id: str, state: dict) -> None:
    if deliver_callback(correlation_id, state["payload"]):
        _write_state(correlation_id, {**state, "delivered": True})


def _run_agent(message: str, session_id: str) -> dict:
    """Run openclaw agent synchronously and return parsed JSON result."""
    cmd = AGENT_CMD.replace("{message}", shlex.quote(message)).replace("--session-id dato", f"--session-id {session_id}")
    try:
        proc = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=TASK_TIMEOUT,
            env={**os.environ, "DEEPSEEK_API_KEY": os.environ.get("DEEPSEEK_API_KEY", ""),
                 "OPENAI_API_KEY": os.environ.get("DEEPSEEK_API_KEY", ""),
                 "OPENAI_BASE_URL": "https://api.deepseek.com"}
        )
        # Try to extract JSON from stdout/stderr (embedded agent output)
        combined = (proc.stdout or "") + (proc.stderr or "")
        # Find the last '{' and try to parse balanced JSON from there
        import re as _re
        last_brace = combined.rfind('{')
        if last_brace >= 0:
            try:
                data = json.loads(combined[last_brace:])
                # Walk the JSON tree to find the "text" field
                def find_text(obj, depth=0):
                    if depth > 10: return None
                    if isinstance(obj, dict):
                        if "text" in obj and isinstance(obj["text"], str) and len(obj["text"]) > 3:
                            return obj["text"]
                        for v in obj.values():
                            r = find_text(v, depth+1)
                            if r: return r
                    elif isinstance(obj, list):
                        for v in obj:
                            r = find_text(v, depth+1)
                            if r: return r
                    return None
                reply = find_text(data)
                if reply:
                    return {"reply": reply, "stop_reason": data.get("stopReason", "unknown")}
            except (json.JSONDecodeError, KeyError):
                pass
        # Fallback: return raw text
        return {"reply": combined[:1000], "error": "no structured output"}
    except Exception as e:
        return {"reply": "", "error": str(e)[:500]}


class Handler(BaseHTTPRequestHandler):
    server_version = "dato-task-receiver"

    def log_message(self, fmt, *args):  # noqa: A003 — BaseHTTPRequestHandler API
        logger.info(fmt, *args)

    def _reply(self, code: int, doc: dict) -> None:
        body = json.dumps(doc).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):  # noqa: N802 — BaseHTTPRequestHandler API
        # Synchronous chat endpoint — runs agent and returns result directly
        if self.path == "/dato/chat":
            auth = self.headers.get("Authorization", "")
            presented = auth[7:] if auth.startswith("Bearer ") else ""
            if not TOKEN or not hmac.compare_digest(presented, TOKEN):
                return self._reply(401, {"error": "invalid token"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length)) if length > 0 else {}
                message = body.get("message", "")
                session_id = body.get("session_id", f"chat-{uuid.uuid4().hex[:8]}")
                if not message:
                    return self._reply(400, {"error": "message required"})
                result = _run_agent(message, session_id)
                return self._reply(200, result)
            except Exception:
                return self._reply(500, {"error": "chat failed"})

        if self.path != "/dato/task":
            return self._reply(404, {"error": "not found"})
        auth = self.headers.get("Authorization", "")
        presented = auth[7:] if auth.startswith("Bearer ") else ""
        if not TOKEN or not hmac.compare_digest(presented, TOKEN):
            return self._reply(401, {"error": "invalid token"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            doc = json.loads(self.rfile.read(length))
            correlation_id = str(uuid.UUID(str(doc["correlation_id"])))
            message = str(doc["message"])
        except (ValueError, KeyError, TypeError):
            return self._reply(400, {"error": "bad request"})
        path = _state_path(correlation_id)
        try:
            # Atomic create is the dedup arbiter: exactly one post wins.
            with path.open("x") as f:
                json.dump({"status": "accepted"}, f)
        except FileExistsError:
            state = json.loads(path.read_text())
            if state.get("status") == "done" and not state.get("delivered"):
                threading.Thread(
                    target=redeliver, args=(correlation_id, state), daemon=True
                ).start()
            return self._reply(
                202, {"ack": True, "correlation_id": correlation_id, "duplicate": True}
            )
        threading.Thread(target=worker, args=(correlation_id, message), daemon=True).start()
        return self._reply(202, {"ack": True, "correlation_id": correlation_id, "duplicate": False})

    def do_GET(self):  # noqa: N802 — BaseHTTPRequestHandler API
        if self.path == "/healthz":
            return self._reply(200, {"ok": True})
        return self._reply(404, {"error": "not found"})


def _redeliver_pending() -> None:
    """Background scanner: retry callback delivery for any done-but-undelivered
    results. Handles the case where a transient dl-control outage outlasts the
    per-callback retry window — a later duplicate POST will not arrive because
    dl-control saw the ack and marked the call dispatched (§5.6)."""
    while True:
        time.sleep(REDELIVERY_SCAN_INTERVAL)
        try:
            for path in sorted(STATE_DIR.glob("*.json")):
                try:
                    state = json.loads(path.read_text())
                except (json.JSONDecodeError, OSError):
                    continue
                if state.get("status") != "done" or state.get("delivered"):
                    continue
                corr = path.stem
                payload = state.get("payload", {})
                if deliver_callback(corr, payload):
                    _write_state(corr, {**state, "delivered": True})
        except Exception:
            logger.exception("redelivery scan error")


def main() -> None:
    if not TOKEN:
        raise SystemExit("DL_INTERNAL_TOKEN is required")
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    threading.Thread(target=_redeliver_pending, daemon=True).start()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    logger.info("listening on :%d (state=%s)", PORT, STATE_DIR)
    server.serve_forever()


if __name__ == "__main__":
    main()
