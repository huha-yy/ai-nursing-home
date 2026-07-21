#!/usr/bin/env bash

set -euo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
INFRA_DIR="$ROOT_DIR/infra"
ENV_FILE="$INFRA_DIR/.env"
ENV_EXAMPLE="$INFRA_DIR/.env.example"
DEFAULT_DATA_DIR="${HOME}/.local/share/dato"
DATA_DIR="${DATO_DATA_ROOT:-$DEFAULT_DATA_DIR}"
INSTALL_DIR="$ROOT_DIR/install"
COMPOSE="docker compose --project-name dato --project-directory $INFRA_DIR --env-file $INSTALL_DIR/dato-ota-defaults.env --env-file $ENV_FILE"
CONTAINER_UID=1000
CONTAINER_GID=1000
ENV_CREATED=0

LIFECYCLE_LOCK_PRIMARY="/run/lock/dato-lifecycle.lock"
LIFECYCLE_LOCK_FALLBACK="/tmp/dato-lifecycle.lock"

BUNDLE_MANIFEST_PATH=""
BUNDLE_TAR_PATH=""
BUNDLE_PAYLOAD=""
BUNDLE_VERSION=""

DATO_LLM_DEVICE_EFFECTIVE=""

# ---------------------------------------------------------------------------
# Core output helpers
# ---------------------------------------------------------------------------

log() {
  printf '==> %s\n' "$*"
}

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "missing required command: $1"
}

# ---------------------------------------------------------------------------
# User identity
# ---------------------------------------------------------------------------

require_container_compatible_user() {
  local uid
  uid="$(id -u)"
  if [[ "$uid" != "0" && "$uid" != "$CONTAINER_UID" ]]; then
    die "run lifecycle scripts as UID $CONTAINER_UID or root so generated secrets are readable by service containers"
  fi
}

# ---------------------------------------------------------------------------
# Random secret generation
# ---------------------------------------------------------------------------

random_secret() {
  openssl rand -hex "$1"
}

random_fernet_key() {
  openssl rand -base64 32 | tr '+/' '-_'
}

# ---------------------------------------------------------------------------
# Filesystem helpers
# ---------------------------------------------------------------------------

dir_has_contents() {
  local dir="$1"
  [[ -d "$dir" ]] || return 1
  [[ -n "$(find "$dir" -mindepth 1 -print -quit 2>/dev/null)" ]]
}

# ---------------------------------------------------------------------------
# Compose wrappers
# ---------------------------------------------------------------------------

compose() {
  $COMPOSE "$@"
}

compose_with_env() {
  local env_file="$1"
  shift
  docker compose --project-name dato \
    --project-directory "$INFRA_DIR" \
    --env-file "$INSTALL_DIR/dato-ota-defaults.env" \
    --env-file "$env_file" \
    "$@"
}

# ---------------------------------------------------------------------------
# Env-file management
# ---------------------------------------------------------------------------

ensure_env_file() {
  if [[ ! -f "$ENV_FILE" ]]; then
    cp "$ENV_EXAMPLE" "$ENV_FILE"
    secure_env_file
    ENV_CREATED=1
    log "created infra/.env"
  fi
}

env_value() {
  local key="$1"
  local file="${2:-$ENV_FILE}"
  grep -E "^${key}=" "$file" 2>/dev/null | tail -n 1 | cut -d= -f2- || true
}

set_env_file() {
  local file="$1"
  local key="$2"
  local value="$3"
  local tmp
  tmp="$(mktemp)"
  awk -v key="$key" -v value="$value" '
    BEGIN { done = 0 }
    $0 ~ "^[[:space:]]*" key "=" {
      print key "=" value
      done = 1
      next
    }
    { print }
    END {
      if (!done) {
        print key "=" value
      }
    }
  ' "$file" > "$tmp"
  mv "$tmp" "$file"
}

set_env() {
  local key="$1"
  local value="$2"
  set_env_file "$ENV_FILE" "$key" "$value"
  secure_env_file
}

secure_env_file() {
  if [[ "$(id -u)" == "0" ]]; then
    chown "$CONTAINER_UID:$CONTAINER_GID" "$ENV_FILE" 2>/dev/null || true
  fi
  chmod 600 "$ENV_FILE"
}

looks_placeholder() {
  local value="$1"
  case "$value" in
    ""|dev_*|change_me*|change-*|ci_*|sample-*|test-*)
      return 0
      ;;
  esac
  return 1
}

ensure_secret_env() {
  local key="$1"
  local bytes="${2:-32}"
  local current
  current="$(env_value "$key")"
  if [[ -z "$current" ]]; then
    set_env "$key" "$(random_secret "$bytes")"
    log "generated $key"
  elif looks_placeholder "$current"; then
    set_env "$key" "$(random_secret "$bytes")"
    log "generated $key"
  fi
}

# ---------------------------------------------------------------------------
# Container-owned path helpers
# ---------------------------------------------------------------------------

ensure_container_owned_path() {
  local path="$1"
  if [[ "$(id -u)" == "0" ]]; then
    chown "$CONTAINER_UID:$CONTAINER_GID" "$path"
  fi
}

ensure_container_owned_dirs() {
  local dir
  for dir in "$@"; do
    ensure_container_owned_path "$dir"
  done
}

ensure_container_secret_file() {
  local path="$1"
  ensure_container_owned_path "$path"
  chmod 600 "$path"
}

# ---------------------------------------------------------------------------
# Wipe helpers — agent container + network removal
# ---------------------------------------------------------------------------

# Remove all per-agent containers (label dato.agent.id). Containers only —
# volumes and ${DATO_DATA_ROOT} are untouched. Shared by reset
# --recreate-agents and wipe.
remove_agent_containers() {
  require_cmd docker
  local ids
  ids="$(docker ps -aq --filter "label=dato.agent.id")"
  [[ -n "$ids" ]] || { log "no agent containers to remove"; return 0; }
  log "removing provisioned agent containers"
  # shellcheck disable=SC2086  # ids is newline-separated container hashes
  docker rm -f $ids >/dev/null || die "failed to remove provisioned agent containers"
}

remove_agent_containers_for_wipe() {
  remove_agent_containers
}

remove_ota_test_containers_for_wipe() {
  require_cmd docker
  log "removing OTA test containers (registry + ota-server)"
  docker rm -f dato-registry dato-ota-server 2>/dev/null || true
}

remove_agent_networks_for_wipe() {
  require_cmd docker
  local network
  for network in dato_net dato_internal_only dato_llm_backend; do
    if docker network inspect "$network" >/dev/null 2>&1; then
      docker network rm "$network" >/dev/null 2>&1 || die "failed to remove agent network: $network"
    fi
  done
}

# ---------------------------------------------------------------------------
# Compose validation + wipe helpers
# ---------------------------------------------------------------------------

validate_compose() {
  require_cmd docker
  log "validating compose configuration"
  compose config --quiet
}

compose_down_for_wipe() {
  require_cmd docker
  local tmp_env
  tmp_env="$(mktemp)"
  cp "$ENV_EXAMPLE" "$tmp_env"
  set_env_file "$tmp_env" "DATO_DATA_ROOT" "$DATA_DIR"
  set_env_file "$tmp_env" "POSTGRES_PASSWORD" "wipe-pg-pass"
  set_env_file "$tmp_env" "DL_CONTROL_APP_PASSWORD" "wipe-ctrl-pass"
  set_env_file "$tmp_env" "DL_CONTROL_SECRET_KEY" "wipe-secret"
  set_env_file "$tmp_env" "DL_INTERNAL_API_KEY" "wipe-internal-key"
  set_env_file "$tmp_env" "DL_COGNEE_ADMIN_TOKEN" "wipe-cognee-token"
  set_env_file "$tmp_env" "DL_COGNEE_PG_PASSWORD" "wipe-cognee-pg"
  set_env_file "$tmp_env" "DL_OTA_WATCHER_APP_PASSWORD" "wipe-ota-pass"
  set_env_file "$tmp_env" "DEEPSEEK_API_KEY" "wipe-deepseek-key"
  set_env_file "$tmp_env" "DATO_OTA_LICENCE_KEY_HOST_PATH" "/dev/null"
  set_env_file "$tmp_env" "DATO_OTA_DEVICE_SECRET_HOST_PATH" "/dev/null"
  local rc=0
  compose_with_env "$tmp_env" down -v --remove-orphans || rc=$?
  rm -f "$tmp_env"
  if [[ "$rc" != "0" ]]; then
    die "docker compose down -v failed; local data was not removed"
  fi
}

# ---------------------------------------------------------------------------
# Container health probes
# ---------------------------------------------------------------------------

container_state() {
  local name="$1"
  docker inspect --format '{{.State.Status}}' "$name" 2>/dev/null || true
}

container_health() {
  local name="$1"
  docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$name" 2>/dev/null || true
}

wait_for_container() {
  local name="$1"
  local expected="$2"
  local timeout="${3:-120}"
  local deadline=$((SECONDS + timeout))
  local state

  while (( SECONDS < deadline )); do
    state="$(container_health "$name")"
    if [[ "$state" == "$expected" ]]; then
      return 0
    fi
    sleep 2
  done

  die "$name did not reach $expected; current state: ${state:-missing}"
}

wait_for_core_stack() {
  wait_for_container dato-postgres healthy 120
  wait_for_container dato-redis healthy 60

  local caddy_state
  caddy_state="$(container_state dato-caddy)"
  [[ "$caddy_state" == "running" ]] || die "dato-caddy is not running; current state: ${caddy_state:-missing}"

  wait_for_container dato-control healthy 180
  wait_for_container dl-ota-watcher healthy 300
}

# ---------------------------------------------------------------------------
# Confirmation prompt
# ---------------------------------------------------------------------------

confirm_phrase() {
  local phrase="$1"
  local message="$2"
  local answer

  if [[ "${ASSUME_YES:-0}" == "1" ]]; then
    return 0
  fi

  printf '%s\n' "$message"
  printf 'Type %s to continue: ' "$phrase"
  read -r answer
  [[ "$answer" == "$phrase" ]] || die "confirmation failed"
}

# ---------------------------------------------------------------------------
# Env sourcing helpers
# ---------------------------------------------------------------------------

source_env_file() {
  local file="$1"
  if [[ -f "$file" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$file"
    set +a
  fi
}

read_ota_defaults() {
  local ota_file="$INSTALL_DIR/dato-ota-defaults.env"
  if [[ -f "$ota_file" ]]; then
    source_env_file "$ota_file"
  fi
}

discover_env() {
  local key
  for key in "$@"; do
    local value=""
    if [[ -n "${!key:-}" ]]; then
      value="${!key}"
    elif [[ -f "$ENV_FILE" ]]; then
      value="$(env_value "$key" "$ENV_FILE")"
    fi
    if [[ -z "$value" ]]; then
      local ota_file="$INSTALL_DIR/dato-ota-defaults.env"
      if [[ -f "$ota_file" ]]; then
        value="$(env_value "$key" "$ota_file")"
      fi
    fi
    if [[ -z "$value" && -f "$ENV_EXAMPLE" ]]; then
      value="$(env_value "$key" "$ENV_EXAMPLE")"
    fi
    if [[ -n "$value" ]]; then
      printf -v "$key" '%s' "$value"
      export "$key"
    fi
  done
}

# ---------------------------------------------------------------------------
# Lifecycle lock
# ---------------------------------------------------------------------------

acquire_lifecycle_lock() {
  local lock_path="$LIFECYCLE_LOCK_PRIMARY"
  if [[ ! -w "/run/lock" ]]; then
    lock_path="$LIFECYCLE_LOCK_FALLBACK"
  fi
  exec {LOCK_FD}>"$lock_path"
  if ! flock -n "$LOCK_FD"; then
    die "another lifecycle script (init/reset/wipe) is in flight; try again later"
  fi
}

# ---------------------------------------------------------------------------
# dl_agents legacy guard
# ---------------------------------------------------------------------------

assert_no_install_generated() {
  local gen_dir="$ROOT_DIR/install/generated"
  if [[ -d "$gen_dir" ]]; then
    die "install/generated/ exists; this is a dl_agents legacy directory incompatible with dato"
  fi
}

# ---------------------------------------------------------------------------
# DATA_ROOT helpers
# ---------------------------------------------------------------------------

ensure_data_root_layout() {
  mkdir -p "$DATA_DIR/secrets" "$DATA_DIR/agents"
  chmod 0750 "$DATA_DIR"
  chmod 0700 "$DATA_DIR/secrets"
  chmod 0750 "$DATA_DIR/agents"
  ensure_container_owned_path "$DATA_DIR"
  ensure_container_owned_path "$DATA_DIR/secrets"
  ensure_container_owned_path "$DATA_DIR/agents"

  local denylist="$DATA_DIR/llm_denylist.txt"
  if [[ ! -f "$denylist" ]]; then
    touch "$denylist"
    chmod 0644 "$denylist"
    ensure_container_owned_path "$denylist"
  fi
}

assert_data_dir_removable() {
  local dir="$1"
  [[ -d "$dir" ]] || return 0
  local files
  files="$(find "$dir" -type f ! -writable -print -quit 2>/dev/null)"
  if [[ -n "$files" ]]; then
    die "files in $dir are not removable by current user (uid=$(id -u)); rerun as root or fix ownership"
  fi
}

handle_env_file_for_wipe() {
  local keep_config="$1"

  if [[ "$keep_config" == "0" ]]; then
    rm -f "$ENV_FILE"
    log "removed infra/.env"
    return 0
  fi

  if [[ ! -f "$ENV_FILE" ]]; then
    return 0
  fi

  local whitelist="CADDY_DOMAIN CADDY_ACME_EMAIL CADDY_HTTPS_PORT TIMEZONE DATO_DATA_ROOT DATO_LLM_DEVICE DEEPSEEK_API_KEY LOCAL_LLM_BASE_URL LOCAL_LLM_API_KEY DL_EGRESS_DNS_EXTRA_DENY DL_EGRESS_DNS_DISABLE COMPOSE_PROFILES"
  local tmp_env saved_key value
  tmp_env="$(mktemp)"
  cp "$ENV_EXAMPLE" "$tmp_env"
  for saved_key in $whitelist; do
    value="$(env_value "$saved_key")"
    if [[ -n "$value" ]]; then
      set_env_file "$tmp_env" "$saved_key" "$value"
    fi
  done
  mv "$tmp_env" "$ENV_FILE"
  secure_env_file
  log "preserved operator-facing config keys in infra/.env"
}

# ---------------------------------------------------------------------------
# Stack running detection
# ---------------------------------------------------------------------------

is_dato_stack_running() {
  docker ps --filter "label=com.docker.compose.project=dato" \
    --filter "status=running" \
    --format '{{.Names}}' 2>/dev/null | grep -q '^dato-caddy$'
}

# ---------------------------------------------------------------------------
# External networks
# ---------------------------------------------------------------------------

ensure_external_networks() {
  require_cmd docker

  if ! docker network inspect dato_net >/dev/null 2>&1; then
    docker network create dato_net >/dev/null || die "failed to create network: dato_net"
    log "created network: dato_net"
  fi

  if ! docker network inspect dato_internal_only >/dev/null 2>&1; then
    docker network create --internal dato_internal_only >/dev/null || die "failed to create network: dato_internal_only"
    log "created network: dato_internal_only"
  fi

  if ! docker network inspect dato_llm_backend >/dev/null 2>&1; then
    docker network create --internal dato_llm_backend >/dev/null || die "failed to create network: dato_llm_backend"
    log "created network: dato_llm_backend"
  fi
}

# ---------------------------------------------------------------------------
# Preflight — individual sub-checks
# ---------------------------------------------------------------------------

_preflight_disk() {
  local path="$1"
  local min_kb="$2"
  local label="$3"
  local avail
  avail="$(df --output=avail "$path" 2>/dev/null | tail -n 1)"
  if [[ -z "$avail" || "$avail" -lt "$min_kb" ]]; then
    die "$label: insufficient disk space (${avail:-unknown} KB available, need ${min_kb} KB)"
  fi
}

_preflight_mem() {
  local min_kb="$1"
  local total
  total="$(awk '/MemTotal/ {print $2}' /proc/meminfo 2>/dev/null)"
  if [[ -z "$total" || "$total" -lt "$min_kb" ]]; then
    die "insufficient memory (${total:-unknown} KB, need ${min_kb} KB)"
  fi
}

_preflight_python_version() {
  local ver
  ver="$(python3 -c 'import sys; print(sys.version_info[:2])' 2>/dev/null)" || die "python3 not found"
  local major minor
  major="${ver#[\(]}"; major="${major%%,*}"
  minor="${ver##*, }"; minor="${minor%\)}"
  # Host floor is 3.10 (not 3.12 like the containers): scripts/init runs the
  # standalone verifier dl_shared/manifest_verify.py via PYTHONPATH on the
  # HOST, and that module is kept 3.10-compatible. Permissive host floor avoids
  # forcing a Python upgrade on common LTS hosts (Ubuntu 22.04 ships 3.10).
  if [[ "$major" -lt 3 || ( "$major" -eq 3 && "$minor" -lt 10 ) ]]; then
    die "python3 >= 3.10 required (found ${major}.${minor})"
  fi
}

_preflight_fs_type() {
  local path="$1"
  local fstype
  fstype="$(stat --file-system --format=%T "$path" 2>/dev/null)" || die "cannot stat filesystem for $path"
  case "$fstype" in
    ext2|ext3|ext4|ext2/ext3|ext2/ext4|xfs|btrfs|zfs) return 0 ;;
    # NFS/CIFS/SMB/fuseblk are rejected: the P3 reconciler relies on flock,
    # which is unreliable over network filesystems (spec §7.3).
    *) die "$path is on unsupported filesystem type: $fstype (need a local POSIX FS: ext4, xfs, btrfs, or zfs; NFS/CIFS unsupported — spec §7.3)" ;;
  esac
}

# ---------------------------------------------------------------------------
# Preflight — Phase A (host-only; runs BEFORE .env exists)
# ---------------------------------------------------------------------------

preflight_phase_a() {
  local mode="$1"

  require_cmd docker
  require_cmd openssl
  require_cmd sha256sum

  _preflight_python_version
  if ! python3 -c "import cryptography" 2>/dev/null; then
    die "python3 cryptography library not found; install python3-cryptography"
  fi

  if ! docker info >/dev/null 2>&1; then
    die "docker daemon is not reachable"
  fi

  local docker_ver
  docker_ver="$(docker info --format '{{.ServerVersion}}' 2>/dev/null)"
  local docker_major; docker_major="${docker_ver%%.*}"
  if [[ -z "$docker_major" || "$docker_major" -lt 24 ]]; then
    die "docker >= 24.0 required (found ${docker_ver:-unknown})"
  fi

  local compose_ver
  compose_ver="$(docker compose version --short 2>/dev/null)"
  local compose_major compose_minor
  compose_major="${compose_ver%%.*}"; compose_minor="${compose_ver#*.}"; compose_minor="${compose_minor%%.*}"
  if [[ -z "$compose_major" || "$compose_major" -lt 2 || ( "$compose_major" -eq 2 && "${compose_minor:-0}" -lt 20 ) ]]; then
    die "docker compose >= 2.20 required (found ${compose_ver:-unknown})"
  fi

  local parent_dir
  parent_dir="$(dirname "$DATA_DIR")"
  if [[ ! -d "$parent_dir" ]]; then
    mkdir -p "$parent_dir" || die "cannot create parent directory of DATO_DATA_ROOT: $parent_dir"
  fi
  if [[ ! -w "$parent_dir" ]]; then
    die "parent directory of DATO_DATA_ROOT is not writable: $parent_dir"
  fi

  _preflight_fs_type "$parent_dir"

  if [[ ! -r "/proc/sys/net/ipv4/tcp_tw_reuse" ]]; then
    die "/proc/sys/net/ipv4/tcp_tw_reuse is not readable"
  fi

  local licence="$DATA_DIR/licence.key"
  if [[ ! -f "$licence" ]]; then
    log "no licence.key found — creating dev licence (replace before production use)"
    mkdir -p "$DATA_DIR"
    cat > "$licence" <<'DEWLIC'
{"customer_id":"dev","device_id":"dev-001","issued_at":"2026-01-01T00:00:00Z","expires_at":"2099-01-01T00:00:00Z","manifest_token":"dev-token","registry_user":"dev","registry_password":"dev"}
DEWLIC
    chmod 600 "$licence"
  fi
  if [[ ! -r "$licence" ]]; then
    die "licence.key is not readable: $licence"
  fi

  local manifests
  manifests="$(find "$INSTALL_DIR" -maxdepth 1 -name 'dato-image-bundle-*.manifest.json' -print -quit 2>/dev/null)"
  if [[ -z "$manifests" ]]; then
    die "no bundle manifest found in install/ (expected dato-image-bundle-<VERSION>.manifest.json)"
  fi
  local tars
  tars="$(find "$INSTALL_DIR" -maxdepth 1 -name 'dato-image-bundle-*.tar' -print -quit 2>/dev/null)"
  if [[ -z "$tars" ]]; then
    die "no bundle tarball found in install/ (expected dato-image-bundle-<VERSION>.tar)"
  fi

  if [[ ! -f "$INSTALL_DIR/dato-ota-defaults.env" ]]; then
    die "install/dato-ota-defaults.env missing"
  fi
  if [[ ! -f "$INSTALL_DIR/dato-ota-minisign.pub" ]]; then
    die "install/dato-ota-minisign.pub missing"
  fi

  if [[ "$mode" == "install" ]]; then
    _preflight_disk "$parent_dir" 10485760 "DATO_DATA_ROOT partition"
    _preflight_disk "/var/lib/docker" 10485760 "/var/lib/docker partition"
    _preflight_mem 8388608
    require_container_compatible_user
  fi
}

# ---------------------------------------------------------------------------
# Preflight — Phase B (env-dependent; runs AFTER .env exists)
# ---------------------------------------------------------------------------

preflight_phase_b() {
  local mode="$1"

  local deepseek
  deepseek="${DEEPSEEK_API_KEY:-}"
  if [[ -z "$deepseek" ]] || looks_placeholder "$deepseek"; then
    if [[ -t 0 ]]; then
      # Interactive operator install: prompt for the vendor LLM key instead of
      # aborting. Re-ask until a non-empty, non-placeholder value is entered.
      local entered=""
      while [[ -z "$entered" ]] || looks_placeholder "$entered"; do
        read -rsp "Enter DEEPSEEK_API_KEY (vendor LLM key): " entered </dev/tty
        printf '\n' >&2
      done
      set_env "DEEPSEEK_API_KEY" "$entered"
      export DEEPSEEK_API_KEY="$entered"
      deepseek="$entered"
      log "DEEPSEEK_API_KEY captured"
    else
      # Non-interactive (CI/automation): fail closed — never run prod on a
      # missing/placeholder vendor credential.
      die "DEEPSEEK_API_KEY is missing or still a placeholder; set DEEPSEEK_API_KEY=<your-key> in infra/.env"
    fi
  fi

  if [[ "$mode" != "recreate" ]] && ! is_dato_stack_running; then
    local caddy_port="${CADDY_HTTPS_PORT:-9443}"
    if ss -tlnp 2>/dev/null | grep -q ":$caddy_port "; then
      die "port $caddy_port is already in use; free it or set CADDY_HTTPS_PORT in infra/.env"
    fi
  fi

  if [[ "${DATO_LLM_DEVICE:-auto}" != "cpu" ]]; then
    detect_gpu
  else
    DATO_LLM_DEVICE_EFFECTIVE=cpu
  fi
}

# ---------------------------------------------------------------------------
# Pubkey cross-check
# ---------------------------------------------------------------------------

assert_pubkey_files_match() {
  local pub_file="$INSTALL_DIR/dato-ota-minisign.pub"
  local ota_file="$INSTALL_DIR/dato-ota-defaults.env"

  local pub_b64
  pub_b64="$(grep -v '^untrusted comment:' "$pub_file" | grep -v '^$' | head -n 1 | tr -d '[:space:]')"

  local ota_b64
  ota_b64="$(env_value "DATO_OTA_MINISIGN_PUBKEY" "$ota_file")"

  if [[ "$pub_b64" != "$ota_b64" ]]; then
    die "pubkey mismatch: install/dato-ota-minisign.pub differs from install/dato-ota-defaults.env DATO_OTA_MINISIGN_PUBKEY"
  fi
}

# ---------------------------------------------------------------------------
# Bundle verification (read-only; spec §5.4 steps 1–7)
# ---------------------------------------------------------------------------

verify_install_bundle() {
  log "verifying install bundle"

  local manifest_file
  manifest_file="$(find "$INSTALL_DIR" -maxdepth 1 -name 'dato-image-bundle-*.manifest.json' -print -quit 2>/dev/null)"
  local tar_file
  tar_file="$(find "$INSTALL_DIR" -maxdepth 1 -name 'dato-image-bundle-*.tar' -print -quit 2>/dev/null)"

  if [[ -z "$manifest_file" ]]; then
    die "no bundle manifest found in install/"
  fi
  if [[ -z "$tar_file" ]]; then
    die "no bundle tarball found in install/"
  fi

  local pubkey_path="$INSTALL_DIR/dato-ota-minisign.pub"

  PYTHONPATH="$ROOT_DIR" python3 -c "
from dl_shared.manifest_verify import verify_manifest_file, ManifestVerifyError
import json, sys

with open('$manifest_file') as f:
    raw = json.load(f)

# Placeholder bundles skip signature verification (pubkey is garbage)
if raw.get('payload', {}).get('placeholder') is True:
    print('WARNING: install bundle is a vendor placeholder — OTA will not function until a real bundle is injected', file=sys.stderr)
    payload = raw['payload']
else:
    try:
        vm = verify_manifest_file('$manifest_file', '$pubkey_path')
        payload = vm.payload
    except ManifestVerifyError as e:
        print(f'ERROR: install bundle signature verification failed: {e}', file=sys.stderr)
        sys.exit(2)

if payload.get('manifest_format') != 1 or payload.get('bundle_format') != 1:
    print('ERROR: unsupported manifest_format or bundle_format', file=sys.stderr)
    sys.exit(2)

if payload.get('min_appliance_version') is not None:
    print('ERROR: bundle manifest min_appliance_version must be null for P10 bundles', file=sys.stderr)
    sys.exit(2)

print(json.dumps(payload))
" || exit $?

  BUNDLE_MANIFEST_PATH="$manifest_file"
  BUNDLE_TAR_PATH="$tar_file"
  BUNDLE_PAYLOAD="$(PYTHONPATH="$ROOT_DIR" python3 -c "
import json
with open('$manifest_file') as f:
    raw = json.load(f)
payload = raw['payload']
if not payload.get('placeholder'):
    from dl_shared.manifest_verify import verify_manifest_file
    vm = verify_manifest_file('$manifest_file', '$pubkey_path')
    payload = vm.payload
print(json.dumps(payload))
")"
  BUNDLE_VERSION="$(printf '%s' "$BUNDLE_PAYLOAD" | python3 -c "import json,sys; print(json.load(sys.stdin)['version'])")"
  local is_placeholder
  is_placeholder="$(printf '%s' "$BUNDLE_PAYLOAD" | python3 -c "import json,sys; print('1' if json.load(sys.stdin).get('placeholder') else '')")"
  IS_PLACEHOLDER_BUNDLE="$is_placeholder"

  if [[ -z "$is_placeholder" ]]; then
    local expected_sha256
    expected_sha256="$(printf '%s' "$BUNDLE_PAYLOAD" | python3 -c "import json,sys; print(json.load(sys.stdin)['tarball_sha256'])")"
    local actual_sha256
    actual_sha256="sha256:$(sha256sum "$tar_file" | cut -d' ' -f1)"
    if [[ "$expected_sha256" != "$actual_sha256" ]]; then
      die "install bundle tarball sha256 mismatch: expected $expected_sha256, got $actual_sha256"
    fi
  fi

  if [[ -z "$is_placeholder" ]]; then
    local prod_version_file="$ROOT_DIR/VERSION"
    if [[ -f "$prod_version_file" ]]; then
      local source_commit
      source_commit="$(printf '%s' "$BUNDLE_PAYLOAD" | python3 -c "import json,sys; print(json.load(sys.stdin)['source_commit'])")"
      local version_commit
      version_commit="$(head -c 40 "$prod_version_file" 2>/dev/null)"
      if [[ "$source_commit" != "$version_commit" ]]; then
        die "install bundle source_commit ($source_commit) does not match VERSION file ($version_commit)"
      fi
    fi

    local services_count
    services_count="$(printf '%s' "$BUNDLE_PAYLOAD" | python3 -c "import json,sys; print(len(json.load(sys.stdin).get('services',{})))")"
    if [[ "$services_count" == "0" ]]; then
      die "install bundle manifest has no service entries"
    fi
  fi

  log "install bundle verified: version=$BUNDLE_VERSION"
}

# ---------------------------------------------------------------------------
# Bundle load + cross-check + retag (spec §5.4 steps 8–11)
# ---------------------------------------------------------------------------

load_install_bundle() {
  if [[ -n "${IS_PLACEHOLDER_BUNDLE:-}" ]]; then
    log "skipping bundle load (placeholder bundle)"
    mkdir -p "$DATA_DIR/secrets"
    printf '{"payload":{"manifest_format":1,"bundle_format":1,"version":"0.0.0","source_commit":"0000000000000000000000000000000000000000","placeholder":true,"min_appliance_version":null,"tarball_sha256":"sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855","target_data_schema":0,"services":{},"third_party":{}},"signature":""}\n' \
      > "$DATA_DIR/secrets/.install-bundle.json"
    chmod 600 "$DATA_DIR/secrets/.install-bundle.json"
    ensure_container_owned_path "$DATA_DIR/secrets/.install-bundle.json"
    log "wrote placeholder install-bundle snapshot"
    return 0
  fi
  log "loading install bundle image tarball"
  docker load -i "$BUNDLE_TAR_PATH"

  log "cross-checking loaded image IDs"
  local entries_json
  entries_json="$(printf '%s' "$BUNDLE_PAYLOAD" | python3 -c "
import json, sys
p = json.load(sys.stdin)
entries = {}
for k, v in p.get('services', {}).items():
    entries[k] = {'image': v['image'], 'image_id': v['image_id'], 'compose_ref': v.get('compose_ref', '')}
for k, v in p.get('third_party', {}).items():
    entries[k] = {'image': v['image'], 'image_id': v['image_id'], 'compose_ref': ''}
print(json.dumps(entries))
")"

  local name image image_id compose_ref actual_id
  local rc=0
  for name in $(printf '%s' "$entries_json" | python3 -c "import json,sys; print(' '.join(json.load(sys.stdin).keys()))"); do
    image="$(printf '%s' "$entries_json" | python3 -c "import json,sys; print(json.load(sys.stdin)['$name']['image'])")"
    image_id="$(printf '%s' "$entries_json" | python3 -c "import json,sys; print(json.load(sys.stdin)['$name']['image_id'])")"
    compose_ref="$(printf '%s' "$entries_json" | python3 -c "import json,sys; print(json.load(sys.stdin)['$name']['compose_ref'])")"

    actual_id="$(docker image inspect "$image" --format '{{.Id}}' 2>/dev/null)" || {
      log "WARNING: image $image not found after docker load"
      rc=1
      continue
    }
    if [[ "$actual_id" != "$image_id" ]]; then
      log "ERROR: image_id mismatch for $image: expected $image_id, got $actual_id"
      rc=1
    fi
  done

  if [[ "$rc" != "0" ]]; then
    die "image_id cross-check failed; loaded images remain in the daemon and will be overwritten by a subsequent bundle load"
  fi

  log "retagging compose refs (forward-only)"
  local snapshot_file="$DATA_DIR/secrets/.install-bundle.json"
  local snapshot_version=""
  if [[ -f "$snapshot_file" ]]; then
    snapshot_version="$(python3 -c "
import json
try:
    with open('$snapshot_file') as f:
        s = json.load(f)
    print(s.get('payload', {}).get('version', ''))
except Exception:
    print('')
")"
  fi

  for name in $(printf '%s' "$entries_json" | python3 -c "import json,sys; print(' '.join(json.load(sys.stdin).keys()))"); do
    image="$(printf '%s' "$entries_json" | python3 -c "import json,sys; print(json.load(sys.stdin)['$name']['image'])")"
    image_id="$(printf '%s' "$entries_json" | python3 -c "import json,sys; print(json.load(sys.stdin)['$name']['image_id'])")"
    compose_ref="$(printf '%s' "$entries_json" | python3 -c "import json,sys; print(json.load(sys.stdin)['$name']['compose_ref'])")"

    [[ -n "$compose_ref" ]] || continue

    local current_compose_id=""
    current_compose_id="$(docker image inspect "$compose_ref" --format '{{.Id}}' 2>/dev/null)" || true

    if [[ -z "$snapshot_version" ]]; then
      docker tag "$image" "$compose_ref"
      log "  tagged $compose_ref (fresh install)"
    elif [[ "$BUNDLE_VERSION" != "$snapshot_version" ]] && printf '%s\n%s\n' "$BUNDLE_VERSION" "$snapshot_version" | sort -V 2>/dev/null | tail -n 1 | grep -qFx "$BUNDLE_VERSION"; then
      docker tag "$image" "$compose_ref"
      log "  tagged $compose_ref (bundle upgrade $snapshot_version -> $BUNDLE_VERSION)"
    elif [[ "$current_compose_id" == "$image_id" ]]; then
      log "  $compose_ref already correct (no-op)"
    elif [[ -z "$current_compose_id" ]]; then
      docker tag "$image" "$compose_ref"
      log "  tagged $compose_ref (image missing in store)"
    else
      log "  $compose_ref already at a different image (OTA-advanced); skipping"
    fi
  done

  local openclaw_compose_ref
  openclaw_compose_ref="$(printf '%s' "$entries_json" | python3 -c "import json,sys; print(json.load(sys.stdin).get('openclaw', {}).get('compose_ref', ''))")"
  if [[ -n "$openclaw_compose_ref" ]]; then
    set_env "DATO_OPENCLAW_IMAGE" "$openclaw_compose_ref"
  fi

  mkdir -p "$DATA_DIR/secrets"
  printf '%s' "$BUNDLE_PAYLOAD" | python3 -c "
import json, sys
payload = json.load(sys.stdin)
publish = json.load(open('$BUNDLE_MANIFEST_PATH'))
envelope = {'payload': payload, 'signature': publish.get('signature', '')}
with open('$snapshot_file', 'w') as f:
    json.dump(envelope, f, indent=2)
"
  chmod 600 "$snapshot_file"
  ensure_container_owned_path "$snapshot_file"

  log "persisted install-bundle snapshot to $snapshot_file"
}

# ---------------------------------------------------------------------------
# GPU detection (spec §13)
# ---------------------------------------------------------------------------

detect_gpu() {
  local device="${DATO_LLM_DEVICE:-auto}"

  if [[ "$device" == "cpu" ]]; then
    DATO_LLM_DEVICE_EFFECTIVE=cpu
    return 0
  fi

  local host_gpu_ok=0 docker_runtime_ok=0 nvidia_toolkit_ok=0
  local diag_parts=()

  if command -v nvidia-smi >/dev/null 2>&1; then
    if nvidia-smi --list-gpus 2>/dev/null | grep -q "^GPU "; then
      host_gpu_ok=1
      diag_parts+=("  [OK]   nvidia-smi --list-gpus")
    else
      diag_parts+=("  [FAIL] nvidia-smi --list-gpus (no GPUs listed)")
    fi
  else
    diag_parts+=("  [FAIL] nvidia-smi not found")
  fi

  if docker info --format '{{json .Runtimes}}' 2>/dev/null | python3 -c "import json,sys; d=json.load(sys.stdin); sys.exit(0 if 'nvidia' in d else 1)" 2>/dev/null; then
    docker_runtime_ok=1
    diag_parts+=("  [OK]   docker nvidia runtime registered")
  else
    diag_parts+=("  [FAIL] docker runtime 'nvidia' is not registered")
  fi

  if command -v nvidia-container-cli >/dev/null 2>&1; then
    if nvidia-container-cli info >/dev/null 2>&1; then
      nvidia_toolkit_ok=1
      diag_parts+=("  [OK]   nvidia-container-cli info")
    else
      diag_parts+=("  [FAIL] nvidia-container-cli info -> non-zero exit")
    fi
  else
    diag_parts+=("  [FAIL] nvidia-container-cli not found")
  fi

  if [[ "$device" == "gpu" ]]; then
    if [[ "$host_gpu_ok" == "1" && "$docker_runtime_ok" == "1" && "$nvidia_toolkit_ok" == "1" ]]; then
      DATO_LLM_DEVICE_EFFECTIVE=gpu
      return 0
    fi
    printf 'ERROR: GPU mode requested but Docker cannot use the GPU.\n  Probes:\n%s\n' "${diag_parts[*]}"
    printf '\n  Install the NVIDIA Container Toolkit:\n    https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html\n  Or set DATO_LLM_DEVICE=cpu in infra/.env.\n' >&2
    exit 1
  fi

  # auto mode
  if [[ "$host_gpu_ok" == "0" ]]; then
    DATO_LLM_DEVICE_EFFECTIVE=cpu
    log "no GPU detected; using CPU for LLM"
  elif [[ "$docker_runtime_ok" == "1" && "$nvidia_toolkit_ok" == "1" ]]; then
    DATO_LLM_DEVICE_EFFECTIVE=gpu
    log "GPU detected and configured"
  else
    printf 'ERROR: GPU hardware detected but Docker cannot use it.\n  Probes:\n%s\n' "${diag_parts[*]}"
    printf '\n  Install the NVIDIA Container Toolkit:\n    https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html\n  Or set DATO_LLM_DEVICE=cpu in infra/.env.\n' >&2
    exit 1
  fi
}

# ---------------------------------------------------------------------------
# GPU-gated LLM wait
# ---------------------------------------------------------------------------

wait_for_llm_local_with_gpu_diagnostic() {
  if wait_for_container dl-llm-local healthy 180; then
    return 0
  fi
  printf 'WARNING: dl-llm-local failed to reach healthy state within 180s.\n' >&2
  printf 'Check GPU availability with: nvidia-smi\n' >&2
  printf 'To run without GPU: set DATO_LLM_DEVICE=cpu in infra/.env, remove gpu from COMPOSE_PROFILES, and re-run make init.\n' >&2
  return 3
}

# ---------------------------------------------------------------------------
# Discovery / env resolve for bundle manifest in preflight pubkey check
# ---------------------------------------------------------------------------

resolve_bundle_pubkey_env() {
  local env_val
  env_val="${DATO_OTA_MINISIGN_PUBKEY:-}"
  if [[ -z "$env_val" ]]; then
    env_val="$(env_value "DATO_OTA_MINISIGN_PUBKEY" "$INSTALL_DIR/dato-ota-defaults.env")"
  fi
  printf '%s' "$env_val"
}

# ---------------------------------------------------------------------------
# Convenience wrappers for preflight sub-checks that init/reset call
# ---------------------------------------------------------------------------

preflight_disk() { _preflight_disk "$@"; }
preflight_mem()  { _preflight_mem "$@"; }
preflight_python_version() { _preflight_python_version "$@"; }
preflight_fs_type() { _preflight_fs_type "$@"; }

# ---------------------------------------------------------------------------
# Usage messages
# ---------------------------------------------------------------------------

print_usage_init() {
  printf 'Usage: scripts/init [--no-start]\n'
  printf '\n'
  printf '  Non-interactive, idempotent first-install. Verifies the install\n'
  printf '  bundle, writes per-PC secrets, loads images, starts the stack.\n'
  printf '\n'
  printf '  --no-start   Run preflight + image-load but do not start the stack.\n'
}

print_usage_reset() {
  printf 'Usage: scripts/reset [--no-start] [--yes] [--recreate-agents]\n'
  printf '\n'
  printf '  Non-destructive in-place re-init. Stops and recreates compose\n'
  printf '  services without touching agent containers or state.\n'
  printf '\n'
  printf '  --no-start         Stop services but do not restart.\n'
  printf '  --yes (-y)         Skip the confirmation prompt.\n'
  printf '  --recreate-agents  Also tear down agent containers; dl-control\n'
  printf '                     recreates them from the registry on startup.\n'
}

print_usage_wipe() {
  printf 'Usage: scripts/wipe [--keep-config] [--yes] [-a|--all]\n'
  printf '\n'
  printf '  Destructive factory reset. Removes all local data, named volumes,\n'
  printf '  agent containers, networks, and the .env file.\n'
  printf '\n'
  printf '  --keep-config   Preserve operator-facing .env keys (CADDY_DOMAIN, etc).\n'
  printf '  --yes (-y)      Skip the confirmation prompt.\n'
  printf '  -a, --all       Also remove per-target OTA test containers\n'
  printf '                  (dato-registry, dato-ota-server) before network teardown.\n'
}
