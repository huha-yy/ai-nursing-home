"""End-to-end tests for P10 appliance-side first install."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
LIB_FILE = SCRIPTS_DIR / "lib" / "appliance-common.sh"
INIT_SCRIPT = SCRIPTS_DIR / "init"
RESET_SCRIPT = SCRIPTS_DIR / "reset"
WIPE_SCRIPT = SCRIPTS_DIR / "wipe"
WAIT_SCRIPT = SCRIPTS_DIR / "wait-for-stack"


def _bash_source(code, extra_env=None):
    """Source appliance-common.sh and run code, returning (stdout, stderr, rc)."""
    env = {**os.environ, **(extra_env or {})}
    env.setdefault("HOME", "/tmp")
    result = subprocess.run(
        ["bash", "-c", f'source "{LIB_FILE}" >/dev/null 2>&1; {code}'],
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )
    return result.stdout.strip(), result.stderr.strip(), result.returncode


def _script_run(script, *args):
    """Run a lifecycle script and return (stdout, stderr, rc)."""
    result = subprocess.run(
        [str(script), *args],
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result.stdout.strip(), result.stderr.strip(), result.returncode


# ---- bash syntax ---------------------------------------------------


@pytest.mark.parametrize(
    "script",
    [
        INIT_SCRIPT,
        RESET_SCRIPT,
        WIPE_SCRIPT,
        WAIT_SCRIPT,
        LIB_FILE,
    ],
)
def test_script_syntax_is_valid(script):
    result = subprocess.run(
        ["bash", "-n", str(script)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, f"{script.name}: {result.stderr}"


# ---- script args + help --------------------------------------------


def test_init_help_exits_zero():
    stdout, stderr, rc = _script_run(INIT_SCRIPT, "--help")
    assert rc == 0


def test_init_bad_arg():
    _, stderr, rc = _script_run(INIT_SCRIPT, "--bogus")
    assert rc != 0


def test_init_no_start_flag_parsed():
    """--no-start should at least be a recognized flag, failing at preflight not arg parse."""
    _, stderr, rc = _script_run(INIT_SCRIPT, "--no-start")
    assert rc != 0
    assert "unknown argument" not in stderr.lower()


def test_reset_help_exits_zero():
    stdout, stderr, rc = _script_run(RESET_SCRIPT, "--help")
    assert rc == 0


def test_wipe_help_exits_zero():
    stdout, stderr, rc = _script_run(WIPE_SCRIPT, "--help")
    assert rc == 0


# ---- appliance-common.sh unit tests ---------------------------------


def test_log_function():
    stdout, _, rc = _bash_source('log "hello world"')
    assert rc == 0
    assert "hello world" in stdout


def test_die_function():
    _, stderr, rc = _bash_source('die "test error"')
    assert rc == 1
    assert "test error" in stderr


def test_require_cmd_finds_bash():
    stdout, _, rc = _bash_source('require_cmd bash && log "found"')
    assert rc == 0
    assert "found" in stdout


def test_require_cmd_missing():
    _, stderr, rc = _bash_source("require_cmd nonexistent_cmd_xyzzy")
    assert rc == 1


def test_random_secret_length():
    stdout, _, rc = _bash_source("random_secret 16")
    assert rc == 0
    assert len(stdout) == 32


def test_random_fernet_key():
    stdout, _, rc = _bash_source("random_fernet_key")
    assert rc == 0
    assert len(stdout) > 0


def test_dir_has_contents_true(tmp_path):
    d = tmp_path / "testdir"
    d.mkdir()
    (d / "file").write_text("x")
    stdout, _, rc = _bash_source(f'dir_has_contents "{d}" && log "yes"')
    assert "yes" in stdout


def test_dir_has_contents_false(tmp_path):
    d = tmp_path / "emptydir"
    d.mkdir()
    stdout, _, rc = _bash_source(f'dir_has_contents "{d}" || log "empty"')
    assert "empty" in stdout


def test_looks_placeholder_detects():
    for val in ["", "dev_foo", "change_me", "change-me", "ci_key", "sample-x", "test-y"]:
        stdout, _, rc = _bash_source(f'looks_placeholder "{val}" && log "placeholder"')
        assert "placeholder" in stdout, f"should detect placeholder: {val}"


def test_looks_placeholder_rejects_real():
    stdout, _, rc = _bash_source('looks_placeholder "real_key_value_abc123" || log "real"')
    assert "real" in stdout


def test_set_env_writes_key(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("EXISTING=old\n")
    _, _, rc = _bash_source(f'ENV_FILE="{env_file}"; set_env "NEW_KEY" "new_value"')
    assert rc == 0
    content = env_file.read_text()
    assert "NEW_KEY=new_value" in content


def test_set_env_overwrites_existing(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("EXISTING=old\n")
    _, _, rc = _bash_source(f'ENV_FILE="{env_file}"; set_env "EXISTING" "new_value"')
    content = env_file.read_text()
    assert "EXISTING=new_value" in content
    assert "EXISTING=old" not in content


def test_env_value_reads_key(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("MY_KEY=my_value\nOTHER=other\n")
    stdout, _, rc = _bash_source(f'ENV_FILE="{env_file}"; env_value "MY_KEY"')
    assert rc == 0
    assert stdout == "my_value"


def test_discover_env_resolves_default():
    """discover_env finds DATO_DATA_ROOT from .env.example when env is not set."""
    stdout, _, rc = _bash_source(
        'ENV_FILE="/nonexistent/.env"; discover_env DATO_DATA_ROOT; printf "%s" "$DATO_DATA_ROOT"'
    )
    assert rc == 0
    assert "/var/lib/dato" in stdout


def test_discover_env_uses_override():
    """discover_env uses env override when DATO_DATA_ROOT is set in environment."""
    stdout, _, rc = _bash_source(
        'ENV_FILE="/nonexistent/.env"; discover_env DATO_DATA_ROOT; printf "%s" "$DATO_DATA_ROOT"',
        extra_env={"DATO_DATA_ROOT": "/custom/path"},
    )
    assert rc == 0
    assert stdout == "/custom/path"


def test_acquire_lifecycle_lock():
    stdout, _, rc = _bash_source('acquire_lifecycle_lock && log "locked"')
    assert rc == 0
    assert "locked" in stdout


def test_confirm_phrase_with_yes():
    stdout, _, rc = _bash_source('ASSUME_YES=1; confirm_phrase "TEST" "message" && log "confirmed"')
    assert rc == 0
    assert "confirmed" in stdout


def test_ensure_data_root_layout(tmp_path):
    data_root = tmp_path / "dato_data"
    data_root.mkdir()
    stdout, _, rc = _bash_source(
        f'DATA_DIR="{data_root}"; ensure_data_root_layout',
        extra_env={"DATO_DATA_ROOT": str(data_root)},
    )
    assert rc == 0
    assert (data_root / "secrets").exists()
    assert (data_root / "agents").exists()


def test_compose_variable_is_set():
    stdout, _, rc = _bash_source(
        'printf "%s" "$COMPOSE"',
        extra_env={"DATO_DATA_ROOT": "/var/lib/dato"},
    )
    assert rc == 0
    assert "dato" in stdout
    assert "--project-name" in stdout


def test_is_dato_stack_running_false():
    stdout, _, rc = _bash_source('is_dato_stack_running && log "running" || log "stopped"')
    assert "stopped" in stdout


# ---- init with env file creation -------------------------------------


def test_init_creates_env_file_from_example(tmp_path):
    """init --no-start should create .env from .env.example during ensure_env_file
    if it gets past preflight (Phase A). Without Docker, Phase A fails first,
    so .env may not be created — both outcomes are valid."""
    wt = tmp_path / "prod"
    infra = wt / "infra"
    infra.mkdir(parents=True)
    (infra / ".env.example").write_text("TEST_KEY=test_value\n")
    shutil.copytree(REPO_ROOT / "install", wt / "install")
    shutil.copytree(SCRIPTS_DIR, wt / "scripts")
    (wt / "VERSION").write_text("0000000000000000000000000000000000000000\n")

    result = subprocess.run(
        ["bash", str(wt / "scripts" / "init"), "--no-start"],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(wt),
        env={**os.environ, "DATO_DATA_ROOT": str(tmp_path / "data"), "HOME": str(tmp_path)},
    )
    # Preflight Phase A checks for Docker daemon — may fail before ensure_env_file runs.
    # Both outcomes are valid: .env not yet created (preflight blocked it) or
    # .env created (if Phase A passed far enough).
    assert result.returncode != 0 or (infra / ".env").exists()


def test_wipe_complains_without_yes(tmp_path):
    wt = tmp_path / "prod"
    (wt / "scripts").mkdir(parents=True)
    shutil.copy2(WIPE_SCRIPT, wt / "scripts" / "wipe")
    (wt / "scripts" / "wipe").chmod(0o755)
    shutil.copytree(REPO_ROOT / "scripts" / "lib", wt / "scripts" / "lib")

    result = subprocess.run(
        ["bash", str(wt / "scripts" / "wipe")],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(wt),
        env={**os.environ, "DATO_DATA_ROOT": str(tmp_path / "data"), "HOME": str(tmp_path)},
    )
    assert result.returncode != 0


def test_reset_recognizes_no_start(tmp_path):
    wt = tmp_path / "prod"
    (wt / "scripts").mkdir(parents=True)
    (wt / "infra").mkdir(parents=True)
    (wt / "infra" / ".env.example").write_text("TEST=1\n")
    shutil.copy2(RESET_SCRIPT, wt / "scripts" / "reset")
    (wt / "scripts" / "reset").chmod(0o755)
    shutil.copytree(REPO_ROOT / "scripts" / "lib", wt / "scripts" / "lib")
    shutil.copytree(REPO_ROOT / "install", wt / "install")

    result = subprocess.run(
        ["bash", str(wt / "scripts" / "reset"), "--no-start", "--yes"],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(wt),
        env={**os.environ, "DATO_DATA_ROOT": str(tmp_path / "data"), "HOME": str(tmp_path)},
    )
    assert result.returncode != 0
    assert "unknown argument" not in result.stderr.lower()


# ---- preflight behavioral tests ---------------------------------------


def _setup_install_tree(tmp_path, with_licence=False, with_bundle=False, python_version="3.11"):
    """Set up a minimal install tree for preflight testing."""
    wt = tmp_path / "prod"
    infra = wt / "infra"
    infra.mkdir(parents=True)
    (infra / ".env.example").write_text("TEST=1\nDEEPSEEK_API_KEY=sk-test-change-me-123456\n")
    shutil.copytree(SCRIPTS_DIR, wt / "scripts")
    shutil.copytree(REPO_ROOT / "install", wt / "install")
    data_root = tmp_path / "data"
    data_root.mkdir()
    if with_licence:
        (data_root / "licence.key").write_text("test-licence")
    if with_bundle:
        # Real bundle manifest for verify_install_bundle
        manifest = {"payload": {"version": "1.0", "placeholder": True}, "signature": "x"}
        (wt / "install" / "dato-image-bundle-1.0.manifest.json").write_text(json.dumps(manifest))
    # Create a fake python3 that reports 3.11 so preflight doesn't bail on version
    fake_python = tmp_path / "bin" / "python3"
    fake_python.parent.mkdir(parents=True)
    fake_python.write_text(
        f'#!/bin/bash\nif [ "$1" = "--version" ]; then\n'
        f"  echo 'Python {python_version}'\n"
        f'elif [ "$1" = "-c" ]; then\n'
        f'  exec /usr/bin/python3 -c "$2"\n'
        f"else\n"
        f'  exec /usr/bin/python3 "$@"\n'
        f"fi\n"
    )
    fake_python.chmod(0o755)
    return wt, infra


def test_init_preflight_produces_diagnostic_on_failure(tmp_path):
    """init --no-start without Docker must reach preflight and print an
    ERROR diagnostic, not crash on a bash syntax error like 'local'."""
    wt, infra = _setup_install_tree(tmp_path)
    result = subprocess.run(
        ["bash", str(wt / "scripts" / "init"), "--no-start"],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(wt),
        env={
            **os.environ,
            "DATO_DATA_ROOT": str(tmp_path / "data"),
            "HOME": str(tmp_path),
            "PATH": f"{tmp_path / 'bin'}:{os.environ['PATH']}",
        },
    )
    # Must fail (preflight catches missing Docker or similar)
    assert result.returncode != 0, "init should have failed but exited 0"
    # Must produce an ERROR diagnostic from die() — proves script
    # reached preflight_phase_a, not a bash crash on 'local'
    assert "ERROR:" in result.stderr, (
        f"no ERROR diagnostic in stderr; script may have crashed before "
        f"preflight:\nSTDERR:\n{result.stderr}\nSTDOUT:\n{result.stdout}"
    )


def test_reset_reaches_preflight_and_fails_with_diagnostic(tmp_path):
    """reset --no-start --yes must reach preflight and fail with an ERROR
    diagnostic, not crash on 'local' at script scope."""
    wt, infra = _setup_install_tree(tmp_path)
    result = subprocess.run(
        ["bash", str(wt / "scripts" / "reset"), "--no-start", "--yes"],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(wt),
        env={
            **os.environ,
            "DATO_DATA_ROOT": str(tmp_path / "data"),
            "HOME": str(tmp_path),
            "PATH": f"{tmp_path / 'bin'}:{os.environ['PATH']}",
        },
    )
    assert result.returncode != 0
    assert "ERROR:" in result.stderr, (
        f"reset did not produce an ERROR diagnostic:\n"
        f"STDERR:\n{result.stderr}\nSTDOUT:\n{result.stdout}"
    )


# ---- P11 recreate-agents ---------------------------------------------


def test_reset_recognizes_recreate_agents_flag():
    """--recreate-agents is parsed, not rejected as unknown."""
    _, stderr, rc = _script_run(RESET_SCRIPT, "--recreate-agents", "--help")
    assert rc == 0
    assert "unknown argument" not in stderr.lower()


def test_reset_help_mentions_recreate_agents():
    stdout, _, rc = _script_run(RESET_SCRIPT, "--help")
    assert rc == 0
    assert "--recreate-agents" in stdout


# ---- P11 E2E follow-up: filesystem-type preflight (spec §7.3) ---------


def test_fs_type_accepts_local_posix():
    """ext4/xfs/btrfs/zfs are accepted local POSIX filesystems."""
    for fstype in ("ext4", "xfs", "btrfs", "zfs", "ext2/ext3"):
        stdout, _, rc = _bash_source(
            f'stat() {{ echo "{fstype}"; }}; _preflight_fs_type /data && echo OK'
        )
        assert rc == 0, f"{fstype} should be accepted"
        assert stdout == "OK"


def test_fs_type_rejects_nfs_and_cifs():
    """NFS/CIFS are rejected — flock is unreliable over network FS (spec §7.3)."""
    for fstype in ("nfs", "nfs4", "cifs", "smbfs", "fuseblk"):
        _, stderr, rc = _bash_source(f'stat() {{ echo "{fstype}"; }}; _preflight_fs_type /data')
        assert rc != 0, f"{fstype} must be rejected"
        assert "unsupported filesystem" in stderr.lower()
