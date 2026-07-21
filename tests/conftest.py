"""Pytest fixtures for dato integration tests."""

import os
import subprocess
from pathlib import Path

import httpx
import psycopg
import pytest
import redis
import tenacity

ROOT = Path(__file__).resolve().parents[1]
INFRA_DIR = ROOT / "infra"


@pytest.fixture(scope="session")
def caddy_url():
    host = os.environ.get("CADDY_DOMAIN", "localhost")
    port = os.environ.get("CADDY_HTTPS_PORT", "9443")
    return f"https://{host}:{port}"


@pytest.fixture(scope="session")
def http_session():
    with httpx.Client(verify=False, timeout=10) as client:
        yield client


@pytest.fixture(scope="session")
def pg_conn():
    @tenacity.retry(stop=tenacity.stop_after_delay(60), wait=tenacity.wait_fixed(2))
    def _connect():
        user = os.environ.get("POSTGRES_USER", "dato")
        password = os.environ.get("POSTGRES_PASSWORD", "dev_password_change_me")
        dbname = os.environ.get("POSTGRES_DB", "dato")
        port = os.environ.get("POSTGRES_PORT", "5432")

        result = subprocess.run(
            [
                "docker",
                "inspect",
                "dato-postgres",
                "--format",
                "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        ip = result.stdout.strip()
        return psycopg.connect(host=ip, user=user, password=password, dbname=dbname, port=port)

    conn = _connect()
    yield conn
    conn.close()


@pytest.fixture(scope="session")
def redis_conn():
    @tenacity.retry(stop=tenacity.stop_after_delay(60), wait=tenacity.wait_fixed(2))
    def _connect():
        port = int(os.environ.get("REDIS_PORT", "6379"))

        result = subprocess.run(
            [
                "docker",
                "inspect",
                "dato-redis",
                "--format",
                "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        ip = result.stdout.strip()
        client = redis.Redis(host=ip, port=port, decode_responses=True)
        client.ping()
        return client

    client = _connect()
    yield client
    client.close()


@pytest.fixture(scope="session")
def dl_control_ready(http_session, caddy_url):
    """Block until dl-control answers /api/health through Caddy."""

    @tenacity.retry(stop=tenacity.stop_after_delay(180), wait=tenacity.wait_fixed(3))
    def _wait():
        r = http_session.get(f"{caddy_url}/api/health")
        assert r.status_code == 200
        return True

    return _wait()
