"""Foundation smoke tests — Caddy, Postgres, Redis, socket-proxy."""

import subprocess


def test_caddy_health_endpoint(http_session, caddy_url):
    """GET /health returns 200 with the static health page."""
    r = http_session.get(f"{caddy_url}/health")
    assert r.status_code == 200
    assert "dato" in r.text


def test_dl_control_health_through_caddy(http_session, caddy_url, dl_control_ready):
    """Caddy reverse-proxies /api/health to dl-control."""
    r = http_session.get(f"{caddy_url}/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_login_page_served_through_caddy(http_session, caddy_url, dl_control_ready):
    """The dl-control login page is reachable through Caddy."""
    r = http_session.get(f"{caddy_url}/login")
    assert r.status_code == 200
    assert "Sign in" in r.text


def test_root_redirects_to_admin(http_session, caddy_url, dl_control_ready):
    """GET / redirects toward the admin UI (no longer a 503 placeholder)."""
    r = http_session.get(f"{caddy_url}/", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/admin"


def test_xff_spoof_cannot_control_client_ip(http_session, caddy_url, pg_conn, dl_control_ready):
    """A client-supplied X-Forwarded-For must NOT become the recorded IP —
    Caddy overwrites it with the real peer (spec §6.8, Codex MAJOR)."""
    spoofed = "203.0.113.7"  # TEST-NET-3, never a real peer
    resp = http_session.post(
        f"{caddy_url}/login",
        data={"username": "ghost-xff-probe", "password": "x"},
        headers={"X-Forwarded-For": spoofed},
    )
    assert resp.status_code in (401, 429)
    with pg_conn.cursor() as cur:
        # audit_log is FORCE-RLS — read it under the system role.
        cur.execute("SELECT set_config('app.current_role', 'system', true)")
        cur.execute(
            "SELECT meta FROM audit_log WHERE action = 'login_failed' "
            "ORDER BY occurred_at DESC LIMIT 1"
        )
        row = cur.fetchone()
    assert row is not None, "login_failed audit row expected"
    assert row[0].get("ip") != spoofed


def test_postgres_extensions_loaded(pg_conn):
    """pgvector, uuid-ossp, and pgcrypto extensions are present."""
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT extname FROM pg_extension WHERE extname IN ('vector', 'uuid-ossp', 'pgcrypto')"
        )
        rows = cur.fetchall()
        extensions = {row[0] for row in rows}
    assert "vector" in extensions
    assert "uuid-ossp" in extensions
    assert "pgcrypto" in extensions


def test_redis_responds_to_ping(redis_conn):
    """Redis responds to PING with PONG."""
    assert redis_conn.ping() is True


def test_docker_proxy_reachable():
    """dl-docker-proxy is running and reachable on the internal network."""
    result = subprocess.run(
        ["docker", "inspect", "dato-docker-proxy", "--format", "{{.State.Status}}"],
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == "running"


def test_agent_manager_exists_after_bootstrap(pg_conn):
    """After bootstrap, agent-manager precreated seed is materialized."""
    with pg_conn.cursor() as cur:
        cur.execute("SELECT set_config('app.current_role', 'system', true)")
        cur.execute(
            "SELECT id, status, precreated_id FROM agents WHERE precreated_id = 'agent-manager'"
        )
        row = cur.fetchone()
    assert row is not None, "agent-manager precreated agent not found in DB"
    assert row[1] == "active", f"expected active, got {row[1]}"
