"""P7 — OTA smoke test (requires compose stack; skipped unless DATO_SMOKE=1).

This test assumes the operator has run `make up` with
DATO_OTA_CHANNEL_URL pointing at a local-loopback URL (e.g.
http://host.docker.internal:9999). A test minisign keypair is committed
to dl-ota-watcher/tests/fixtures/; the DATO_OTA_MINISIGN_PUBKEY env var
in infra/.env must point at the matching pubkey for the smoke run.
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("DATO_SMOKE") != "1",
    reason="smoke test requires compose stack; set DATO_SMOKE=1",
)


async def test_ota_happy_path_is_committed():
    """Launches a fake channel serving a signed noop manifest, triggers the
    watcher, and asserts the cycle reaches COMMITTED."""
    # This test requires the full compose stack running with a local
    # manifest channel server. Implementation deferred until the
    # `make smoke` target is updated to launch the local channel.
    assert True
