#!/usr/bin/env python3
"""DNSPod DDNS updater for eldcare.cn nursing home deployments.

Detects the current IPv4 address and updates the configured DNS A
record via the Tencent Cloud DNSPod API.  Intended as a systemd timer
or cron job (every 120–300 s).

Credentials (from DNSPod console https://console.dnspod.cn/account/token):

    DNSPOD_SECRET_ID=AKID...
    DNSPOD_SECRET_KEY=xxx...

Usage:
    python3 ddns_update.py                          # one-shot
    python3 ddns_update.py --daemon --interval 120  # daemon
"""

from __future__ import annotations

import json
import logging
import os
import socket
import sys
import time
from datetime import datetime, timezone

from tencentcloud.common import credential
from tencentcloud.common.exception.tencent_cloud_sdk_exception import (
    TencentCloudSDKException,
)
from tencentcloud.dnspod.v20210323 import dnspod_client, models

logger = logging.getLogger("ddns")

# ── config ──────────────────────────────────────────────────────────
DOMAIN = os.environ.get("DDNS_DOMAIN", "eldcare.cn")
SUB_DOMAIN = os.environ.get("DDNS_SUB_DOMAIN", "hz-sanfu")
RECORD_TYPE = "A"
RECORD_LINE = "默认"
TTL = int(os.environ.get("DDNS_TTL", "600"))

SECRET_ID = os.environ["DNSPOD_SECRET_ID"]
SECRET_KEY = os.environ["DNSPOD_SECRET_KEY"]

STATE_FILE = os.environ.get(
    "DDNS_STATE_FILE",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".ddns_state.json"),
)

# ── helpers ─────────────────────────────────────────────────────────


def get_current_ip() -> str:
    """IPv4 address of the interface with the default route."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        pass
    for addr in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
        ip = addr[4][0]
        if not ip.startswith("127."):
            return ip
    raise RuntimeError("Cannot determine local IPv4 address")


def _client():
    cred = credential.Credential(SECRET_ID, SECRET_KEY)
    return dnspod_client.DnspodClient(cred, "")


def find_record(sub_domain: str) -> dict | None:
    """Return the record dict for *sub_domain*, or None."""
    req = models.DescribeRecordListRequest()
    req.Domain = DOMAIN
    req.Subdomain = sub_domain
    req.RecordType = RECORD_TYPE
    try:
        resp = _client().DescribeRecordList(req)
    except TencentCloudSDKException as exc:
        logger.error("DescribeRecordList failed: %s", exc)
        return None
    if not resp.RecordList:
        return None
    return {
        "id": resp.RecordList[0].RecordId,
        "value": resp.RecordList[0].Value,
    }


def update_record(record_id: int, ip: str, sub_domain: str) -> bool:
    """Update an existing A record.  Returns True on success."""
    req = models.ModifyRecordRequest()
    req.Domain = DOMAIN
    req.SubDomain = sub_domain
    req.RecordType = RECORD_TYPE
    req.RecordLine = RECORD_LINE
    req.Value = ip
    req.TTL = TTL
    req.RecordId = record_id
    try:
        _client().ModifyRecord(req)
        return True
    except TencentCloudSDKException as exc:
        logger.error("ModifyRecord failed: %s", exc)
        return False


def create_record(ip: str, sub_domain: str) -> int | None:
    """Create an A record.  Returns the new record ID, or None."""
    req = models.CreateRecordRequest()
    req.Domain = DOMAIN
    req.SubDomain = sub_domain
    req.RecordType = RECORD_TYPE
    req.RecordLine = RECORD_LINE
    req.Value = ip
    req.TTL = TTL
    try:
        resp = _client().CreateRecord(req)
        return resp.RecordId
    except TencentCloudSDKException as exc:
        logger.error("CreateRecord failed: %s", exc)
        return None


def _load_state() -> dict | None:
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _save_state(state: dict) -> None:
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, STATE_FILE)


def run_once() -> bool:
    """One update cycle.  Returns True if the record was changed."""
    ip = get_current_ip()
    logger.info("Current IP: %s", ip)

    state = _load_state()
    if state and state.get("last_ip") == ip:
        logger.debug("IP unchanged, skip")
        return False

    record = find_record(SUB_DOMAIN)
    if record is None:
        logger.info("Creating A record %s.%s → %s", SUB_DOMAIN, DOMAIN, ip)
        rid = create_record(ip, SUB_DOMAIN)
        if rid is None:
            return False
    else:
        if record["value"] == ip:
            logger.info("DNS already %s, skip", ip)
            _save_state({"last_ip": ip, "record_id": record["id"], "updated": now_iso()})
            return False
        logger.info("Updating %s → %s", record["value"], ip)
        if not update_record(record["id"], ip, SUB_DOMAIN):
            return False
        rid = record["id"]

    _save_state({"last_ip": ip, "record_id": rid, "updated": now_iso()})
    logger.info("✅ %s.%s → %s", SUB_DOMAIN, DOMAIN, ip)
    return True


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def daemon(interval: int = 120) -> None:
    logger.info("DDNS daemon: %s.%s every %ds", SUB_DOMAIN, DOMAIN, interval)
    while True:
        try:
            run_once()
        except Exception:
            logger.exception("Cycle failed")
        time.sleep(interval)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s ddns %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    if "--daemon" in sys.argv:
        try:
            idx = sys.argv.index("--interval")
            interval = int(sys.argv[idx + 1])
        except (ValueError, IndexError):
            interval = 120
        daemon(interval)
    else:
        ok = run_once()
        sys.exit(0 if ok else 1)
