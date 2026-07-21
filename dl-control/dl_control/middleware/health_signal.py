"""Health-signal detection middleware for passive health monitoring.

Scans incoming chat messages for resident names + health-related keywords and
automatically writes alerts to the nursing_health_alerts table. The middleware
is non-blocking: scan + alert creation runs in a fire-and-forget background task
so it never slows down the chat response.
"""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.parse

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Health keyword taxonomy (Chinese)
# ---------------------------------------------------------------------------

HEALTH_KEYWORDS = {
    "感冒": ["感冒", "发烧", "发热", "咳嗽", "流鼻涕", "喉咙痛", "嗓子疼", "鼻塞"],
    "疼痛": ["头疼", "头痛", "肚子疼", "腹痛", "腰疼", "腰痛", "腿疼", "胸闷", "胸痛", "关节痛"],
    "消化": ["吃不下", "没胃口", "恶心", "呕吐", "拉肚子", "腹泻", "便秘", "消化不良"],
    "用药": ["吃什么药", "怎么吃药", "药量", "忘记吃药", "停药", "换药", "副作用"],
    "跌倒": ["摔倒", "摔了", "跌倒", "站不稳", "头晕", "眩晕"],
    "皮肤": ["褥疮", "压疮", "红肿", "溃烂", "瘙痒", "皮疹"],
    "睡眠": ["失眠", "睡不着", "睡不好", "夜醒", "昼夜颠倒"],
    "心理": ["想不开", "不想活了", "抑郁", "焦虑", "害怕", "孤独"],
}

# Flat lookup: keyword -> category (longer keywords first so they match before
# shorter substrings).
_KEYWORD_TO_CATEGORY: dict[str, str] = {}
for _cat, _kwds in HEALTH_KEYWORDS.items():
    for _kw in sorted(_kwds, key=len, reverse=True):
        _KEYWORD_TO_CATEGORY[_kw] = _cat


# ---------------------------------------------------------------------------
# Pure detection function (testable without database or async)
# ---------------------------------------------------------------------------


def detect_health_signals(message: str, residents: list[dict]) -> list[dict]:
    """Scan *message* for resident names AND health keywords.

    Returns a list of dicts, each with ``resident_name``, ``resident_id``, and
    ``category``.  When the same message mentions multiple residents or matches
    multiple keyword categories the cross-product is returned.
    """
    if not message or not residents:
        return []

    # Which residents are mentioned by name?
    mentioned = [r for r in residents if r["name"] in message]
    if not mentioned:
        return []

    # Which health categories are triggered?
    matched_categories: set[str] = set()
    for keyword, category in _KEYWORD_TO_CATEGORY.items():
        if keyword in message:
            matched_categories.add(category)

    if not matched_categories:
        return []

    results: list[dict] = []
    for r in mentioned:
        for cat in sorted(matched_categories):
            results.append(
                {
                    "resident_name": r["name"],
                    "resident_id": r["id"],
                    "category": cat,
                }
            )
    return results


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------


async def load_residents(db) -> list[dict]:
    """Return all residents as ``[{id, name}, ...]``."""
    async with db.conn(user_id=None, role="system") as conn:
        rows = await conn.execute("SELECT id, name FROM nursing_residents")
        return [{"id": r[0], "name": r[1]} for r in await rows.fetchall()]


async def create_health_alert(
    db,
    resident_name: str,
    resident_id: str,
    message: str,
    category: str,
) -> None:
    """Insert a health alert into ``nursing_health_alerts``."""
    severity = "warning" if category in ("跌倒", "心理") else "info"

    async with db.conn(user_id=None, role="system") as conn:
        await conn.execute(
            "INSERT INTO nursing_health_alerts (resident_id, content, category, severity) "
            "VALUES ($1, $2, $3, $4)",
            resident_id,
            message,
            category,
            severity,
        )

    logger.info(
        "health_alert_created",
        resident=resident_name,
        resident_id=resident_id,
        category=category,
        severity=severity,
    )


# ---------------------------------------------------------------------------
# Request-body text extraction
# ---------------------------------------------------------------------------


def _extract_message_text(body: bytes, content_type: str) -> str:
    """Best-effort extraction of a human-readable message from an HTTP body."""
    if not body:
        return ""

    # -- JSON body --
    if "application/json" in content_type:
        try:
            data = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return ""
        if isinstance(data, dict):
            # Common message-field names
            for key in ("message", "content", "text", "body", "query", "prompt", "input"):
                val = data.get(key)
                if isinstance(val, str) and val.strip():
                    return val
            # Chat-style messages array (OpenAI / Anthropic shape)
            msgs = data.get("messages")
            if isinstance(msgs, list):
                parts = []
                for m in msgs:
                    if isinstance(m, dict):
                        c = m.get("content") or m.get("message") or ""
                        if isinstance(c, str):
                            parts.append(c)
                return " ".join(parts)
        return ""

    # -- Form-encoded body --
    if "application/x-www-form-urlencoded" in content_type:
        try:
            text = body.decode("utf-8", errors="replace")
            parsed = urllib.parse.parse_qs(text)
        except Exception:
            return ""
        for key in ("message", "content", "text", "body", "query", "input"):
            vals = parsed.get(key, [])
            if vals and vals[0].strip():
                return vals[0]
        return ""

    # -- Plain text --
    if "text/plain" in content_type:
        try:
            return body.decode("utf-8", errors="replace")[:2000]
        except Exception:
            return ""

    # -- Fallback: decode anything and hope --
    try:
        return body.decode("utf-8", errors="replace")[:2000]
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# ASGI middleware
# ---------------------------------------------------------------------------


class HealthSignalMiddleware:
    """Raw ASGI middleware that buffers POST bodies, scans them for
    resident-name + health-keyword pairs, and fire-and-forgets alert creation.

    The body is buffered and replayed so downstream handlers see it unchanged.
    """

    def __init__(self, app, db) -> None:
        self.app = app
        self.db = db

    async def __call__(self, scope, receive, send):  # noqa: D401
        # Only intercept HTTP POST requests.
        if scope["type"] != "http" or scope["method"] != "POST":
            await self.app(scope, receive, send)
            return

        # Buffer the entire request body.
        chunks: list[bytes] = []
        more_body = True
        while more_body:
            msg = await receive()
            if msg["type"] == "http.request":
                chunks.append(msg.get("body", b""))
                more_body = msg.get("more_body", False)

        body = b"".join(chunks)

        # Extract the Content-Type header.
        ct = ""
        for hdr_name, hdr_value in scope.get("headers", []):
            if hdr_name == b"content-type":
                ct = hdr_value.decode("latin-1", errors="replace")
                break

        # Fire-and-forget: scan the body in a background task.
        if body:
            text = _extract_message_text(body, ct)
            if text:
                # Don't await — keep the chat response fast.
                asyncio.create_task(self._scan_message(text))

        # Replay the buffered body so downstream handlers see it unchanged.
        body_sent = False

        async def _receive():
            nonlocal body_sent
            if not body_sent:
                body_sent = True
                return {
                    "type": "http.request",
                    "body": body,
                    "more_body": False,
                }
            return await receive()

        await self.app(scope, _receive, send)

    async def _scan_message(self, text: str) -> None:
        """Background task: load residents, detect signals, write alerts."""
        try:
            residents = await load_residents(self.db)
            results = detect_health_signals(text, residents)
            for r in results:
                await create_health_alert(
                    self.db,
                    r["resident_name"],
                    r["resident_id"],
                    text,
                    r["category"],
                )
        except Exception:
            logger.warning("health_signal_scan_error", exc_info=True)
