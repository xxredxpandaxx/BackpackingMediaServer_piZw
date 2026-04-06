#!/usr/bin/env bash

set -Eeuo pipefail

SERVICE_NAME="${NOMADSCREEN_SERVICE_NAME:-nomadscreen}"
NETWORK_SERVICE_NAME="${NOMADSCREEN_NETWORK_SERVICE_NAME:-nomadscreen-network}"
INSTALL_DIR="${NOMADSCREEN_INSTALL_DIR:-/opt/nomadscreen}"
STORAGE_ROOT="${NOMADSCREEN_STORAGE_ROOT:-}"
MEDIA_ROOT="${NOMADSCREEN_MEDIA_ROOT:-}"
HTTP_PORT="${NOMADSCREEN_PORT:-}"
TMP_DIR="${NOMADSCREEN_TMP_DIR:-/var/tmp/nomadscreen-install}"
UPLOAD_TMP_DIR="${NOMADSCREEN_UPLOAD_TMP_DIR:-}"
REPO_REF="${NOMADSCREEN_REPO_REF:-}"
RESTART_NETWORK=0

usage() {
  cat <<'EOF'
Nomad Screen updater

Usage:
  update.sh
  update.sh [options]

Options:
  --install-dir PATH      App install path (default: /opt/nomadscreen)
  --service-name NAME     systemd service name (default: nomadscreen)
  --network-service NAME  Wi-Fi fallback service name (default: nomadscreen-network)
  --storage-root PATH     Runtime storage root (default: preserved from installed service)
  --media-root PATH       Media library path (default: preserved from installed service)
  --port PORT             HTTP port for the service (default: preserved from installed service)
  --ref REF               Branch or ref to update to (default: current checked-out branch)
  --tmp-dir PATH          Temp build dir for pip work (default: /var/tmp/nomadscreen-install)
  --upload-tmp-dir PATH   Temp dir for large web uploads (default: preserved from installed service or /var/tmp/nomadscreen-upload)
  --restart-network       Restart the Wi-Fi fallback service too
  -h, --help              Show this help
EOF
}

log() {
  printf '[nomadscreen-update] %s\n' "$*"
}

die() {
  printf '[nomadscreen-update] Error: %s\n' "$*" >&2
  exit 1
}

run_root() {
  if [[ "${EUID}" -eq 0 ]]; then
    "$@"
  else
    sudo "$@"
  fi
}

run_as_install_user() {
  if [[ "$(id -un)" == "${INSTALL_USER}" ]]; then
    "$@"
  else
    sudo -u "${INSTALL_USER}" "$@"
  fi
}

read_unit_value() {
  local file_path="$1"
  local key="$2"

  [[ -f "${file_path}" ]] || return 1
  grep -E "^${key}=" "${file_path}" | tail -n 1 | cut -d= -f2-
}

read_unit_environment() {
  local file_path="$1"
  local key="$2"

  [[ -f "${file_path}" ]] || return 1
  grep -E "^Environment=${key}=" "${file_path}" | tail -n 1 | sed "s/^Environment=${key}=//"
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --install-dir)
        [[ $# -ge 2 ]] || die "--install-dir requires a value"
        INSTALL_DIR="$2"
        shift 2
        ;;
      --service-name)
        [[ $# -ge 2 ]] || die "--service-name requires a value"
        SERVICE_NAME="$2"
        shift 2
        ;;
      --network-service)
        [[ $# -ge 2 ]] || die "--network-service requires a value"
        NETWORK_SERVICE_NAME="$2"
        shift 2
        ;;
      --storage-root)
        [[ $# -ge 2 ]] || die "--storage-root requires a value"
        STORAGE_ROOT="$2"
        shift 2
        ;;
      --media-root)
        [[ $# -ge 2 ]] || die "--media-root requires a value"
        MEDIA_ROOT="$2"
        shift 2
        ;;
      --port)
        [[ $# -ge 2 ]] || die "--port requires a value"
        HTTP_PORT="$2"
        shift 2
        ;;
      --ref)
        [[ $# -ge 2 ]] || die "--ref requires a value"
        REPO_REF="$2"
        shift 2
        ;;
      --tmp-dir)
        [[ $# -ge 2 ]] || die "--tmp-dir requires a value"
        TMP_DIR="$2"
        shift 2
        ;;
      --upload-tmp-dir)
        [[ $# -ge 2 ]] || die "--upload-tmp-dir requires a value"
        UPLOAD_TMP_DIR="$2"
        shift 2
        ;;
      --restart-network)
        RESTART_NETWORK=1
        shift
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        die "Unknown argument: $1"
        ;;
    esac
  done
}

ensure_install_context() {
  local service_path
  local unit_user
  local unit_group

  service_path="/etc/systemd/system/${SERVICE_NAME}.service"
  unit_user="$(read_unit_value "${service_path}" "User" || true)"
  unit_group="$(read_unit_value "${service_path}" "Group" || true)"

  if [[ -n "${unit_user}" ]]; then
    INSTALL_USER="${unit_user}"
  elif [[ -n "${SUDO_USER:-}" && "${SUDO_USER}" != "root" ]]; then
    INSTALL_USER="${SUDO_USER}"
  else
    INSTALL_USER="$(id -un)"
  fi

  if [[ -n "${unit_group}" ]]; then
    INSTALL_GROUP="${unit_group}"
  else
    INSTALL_GROUP="$(id -gn "${INSTALL_USER}")"
  fi

  id "${INSTALL_USER}" >/dev/null 2>&1 || die "Install user '${INSTALL_USER}' does not exist"
  INSTALL_HOME="$(getent passwd "${INSTALL_USER}" | cut -d: -f6)"
  [[ -n "${INSTALL_HOME}" ]] || INSTALL_HOME="$(eval echo "~${INSTALL_USER}")"

  [[ -n "${STORAGE_ROOT}" ]] || STORAGE_ROOT="$(read_unit_environment "${service_path}" "NOMADSCREEN_STORAGE_ROOT" || true)"
  [[ -n "${MEDIA_ROOT}" ]] || MEDIA_ROOT="$(read_unit_environment "${service_path}" "NOMADSCREEN_MEDIA_ROOT" || true)"
  [[ -n "${UPLOAD_TMP_DIR}" ]] || UPLOAD_TMP_DIR="$(read_unit_environment "${service_path}" "NOMADSCREEN_UPLOAD_TMP_DIR" || true)"
  [[ -n "${HTTP_PORT}" ]] || HTTP_PORT="$(read_unit_environment "${service_path}" "NOMADSCREEN_PORT" || true)"

  [[ -n "${STORAGE_ROOT}" ]] || STORAGE_ROOT="/srv/nomadscreen"
  [[ -n "${MEDIA_ROOT}" ]] || MEDIA_ROOT="${INSTALL_HOME}/media"
  [[ -n "${UPLOAD_TMP_DIR}" ]] || UPLOAD_TMP_DIR="/var/tmp/nomadscreen-upload"
  [[ -n "${HTTP_PORT}" ]] || HTTP_PORT="80"
}

prepare_tmp_dir() {
  log "Preparing temp build directory at ${TMP_DIR}"
  run_root mkdir -p "${TMP_DIR}"
  run_root chown -R "${INSTALL_USER}:${INSTALL_GROUP}" "${TMP_DIR}"
  log "Preparing upload temp directory at ${UPLOAD_TMP_DIR}"
  run_root mkdir -p "${UPLOAD_TMP_DIR}"
  run_root chown -R "${INSTALL_USER}:${INSTALL_GROUP}" "${UPLOAD_TMP_DIR}"
}

ensure_repo() {
  [[ -d "${INSTALL_DIR}/.git" ]] || die "No git checkout found at ${INSTALL_DIR}. Run install.sh first."
  run_root chown -R "${INSTALL_USER}:${INSTALL_GROUP}" "${INSTALL_DIR}"
  if run_as_install_user git -C "${INSTALL_DIR}" status --porcelain | grep -q .; then
    die "Existing checkout has local changes. Commit or discard them before running the updater."
  fi
}

update_repo() {
  if [[ -z "${REPO_REF}" ]]; then
    REPO_REF="$(run_as_install_user git -C "${INSTALL_DIR}" rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
    if [[ -z "${REPO_REF}" || "${REPO_REF}" == "HEAD" ]]; then
      REPO_REF="main"
    fi
  fi

  log "Fetching ${REPO_REF} from origin"
  run_as_install_user git -C "${INSTALL_DIR}" fetch --depth 1 origin "${REPO_REF}"
  run_as_install_user git -C "${INSTALL_DIR}" checkout -B "${REPO_REF}" FETCH_HEAD

  if [[ -f "${INSTALL_DIR}/install.sh" ]]; then
    run_root chmod 0755 "${INSTALL_DIR}/install.sh"
  fi
  if [[ -f "${INSTALL_DIR}/update.sh" ]]; then
    run_root chmod 0755 "${INSTALL_DIR}/update.sh"
  fi
  run_root chmod 0755 "${INSTALL_DIR}/deploy/network/nomadscreen-network.sh"
}

install_python_deps() {
  if [[ ! -x "${INSTALL_DIR}/.venv/bin/python" ]]; then
    log "Creating virtual environment"
    run_as_install_user env TMPDIR="${TMP_DIR}" python3 -m venv "${INSTALL_DIR}/.venv"
  fi

  log "Installing Python dependencies"
  run_as_install_user env TMPDIR="${TMP_DIR}" PIP_DISABLE_PIP_VERSION_CHECK=1 \
    "${INSTALL_DIR}/.venv/bin/pip" install --no-cache-dir -r "${INSTALL_DIR}/requirements.txt"
}

write_network_service() {
  local tmp_service
  local service_path

  service_path="/etc/systemd/system/${NETWORK_SERVICE_NAME}.service"
  tmp_service="$(mktemp)"

  cat >"${tmp_service}" <<EOF
[Unit]
Description=Nomad Screen Wi-Fi fallback
Wants=NetworkManager.service
After=NetworkManager.service
Before=${SERVICE_NAME}.service

[Service]
Type=oneshot
Environment=NOMADSCREEN_STORAGE_ROOT=${STORAGE_ROOT}
ExecStart=${INSTALL_DIR}/deploy/network/nomadscreen-network.sh
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF

  log "Refreshing ${NETWORK_SERVICE_NAME}.service"
  run_root install -m 0644 "${tmp_service}" "${service_path}"
  rm -f "${tmp_service}"
}

write_service() {
  local tmp_service
  local service_path

  service_path="/etc/systemd/system/${SERVICE_NAME}.service"
  tmp_service="$(mktemp)"

  cat >"${tmp_service}" <<EOF
[Unit]
Description=Nomad Screen media server
After=network.target ${NETWORK_SERVICE_NAME}.service
Wants=${NETWORK_SERVICE_NAME}.service

[Service]
Type=simple
User=${INSTALL_USER}
Group=${INSTALL_GROUP}
WorkingDirectory=${INSTALL_DIR}
Environment=PYTHONUNBUFFERED=1
Environment=NOMADSCREEN_STORAGE_ROOT=${STORAGE_ROOT}
Environment=NOMADSCREEN_MEDIA_ROOT=${MEDIA_ROOT}
Environment=NOMADSCREEN_UPLOAD_TMP_DIR=${UPLOAD_TMP_DIR}
Environment=NOMADSCREEN_PORT=${HTTP_PORT}
Environment=TMPDIR=${UPLOAD_TMP_DIR}
ExecStart=${INSTALL_DIR}/.venv/bin/python ${INSTALL_DIR}/src/main.py
Restart=on-failure
RestartSec=5
AmbientCapabilities=CAP_NET_BIND_SERVICE
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
EOF

  log "Refreshing ${SERVICE_NAME}.service"
  run_root install -m 0644 "${tmp_service}" "${service_path}"
  rm -f "${tmp_service}"
}

restart_services() {
  log "Reloading systemd"
  run_root systemctl daemon-reload
  run_root systemctl enable "${SERVICE_NAME}.service" >/dev/null
  run_root systemctl enable "${NETWORK_SERVICE_NAME}.service" >/dev/null

  if [[ "${RESTART_NETWORK}" == "1" ]]; then
    log "Restarting ${NETWORK_SERVICE_NAME}.service"
    run_root systemctl restart "${NETWORK_SERVICE_NAME}.service"
  else
    log "Leaving ${NETWORK_SERVICE_NAME}.service running to avoid interrupting active Wi-Fi sessions"
  fi

  log "Restarting ${SERVICE_NAME}.service"
  run_root systemctl restart "${SERVICE_NAME}.service"
}

print_success() {
  local head_commit
  head_commit="$(run_as_install_user git -C "${INSTALL_DIR}" rev-parse --short HEAD 2>/dev/null || true)"

  log "Update complete"
  [[ -n "${head_commit}" ]] && log "Current commit: ${head_commit}"
  log "App directory: ${INSTALL_DIR}"
  log "Storage root: ${STORAGE_ROOT}"
  log "Media library: ${MEDIA_ROOT}"
  log "Upload temp dir: ${UPLOAD_TMP_DIR}"
  log "Service restarted: ${SERVICE_NAME}.service"
  if [[ "${RESTART_NETWORK}" != "1" ]]; then
    log "Run 'sudo systemctl restart ${NETWORK_SERVICE_NAME}.service' later if you need hotspot/network script changes applied immediately."
  fi
}

parse_args "$@"
ensure_install_context
prepare_tmp_dir
ensure_repo
update_repo
install_python_deps
write_network_service
write_service
restart_services
print_success
