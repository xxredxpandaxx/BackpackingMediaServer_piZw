#!/usr/bin/env bash

set -Eeuo pipefail

SERVICE_NAME="${NOMADSCREEN_SERVICE_NAME:-nomadscreen}"
NETWORK_SERVICE_NAME="${NOMADSCREEN_NETWORK_SERVICE_NAME:-nomadscreen-network}"
INSTALL_DIR="${NOMADSCREEN_INSTALL_DIR:-/opt/nomadscreen}"
STORAGE_ROOT="${NOMADSCREEN_STORAGE_ROOT:-/srv/nomadscreen}"
MEDIA_ROOT="${NOMADSCREEN_MEDIA_ROOT:-}"
REPO_URL="${NOMADSCREEN_REPO_URL:-https://github.com/xxredxpandaxx/BackpackingMediaServer_piZw.git}"
REPO_REF="${NOMADSCREEN_REPO_REF:-main}"
GITHUB_SLUG="${NOMADSCREEN_GITHUB_SLUG:-xxredxpandaxx/BackpackingMediaServer_piZw}"
HTTP_PORT="${NOMADSCREEN_PORT:-80}"
TMP_DIR="${NOMADSCREEN_TMP_DIR:-/var/tmp/nomadscreen-install}"

usage() {
  cat <<'EOF'
Nomad Screen installer

Usage:
  install.sh
  install.sh --repo https://github.com/owner/repo.git [options]
  install.sh --github owner/repo [options]

Options:
  --repo URL              Public git clone URL to install from
  --github owner/repo     GitHub repo shorthand for public repos
  --ref REF               Branch, tag, or ref to install (default: main)
  --install-dir PATH      App install path (default: /opt/nomadscreen)
  --storage-root PATH     Runtime storage root (default: /srv/nomadscreen)
  --media-root PATH       Media library path (default: ~/media for the install user)
  --port PORT             HTTP port for the service (default: 80)
  --tmp-dir PATH          Temp build dir for venv/pip work (default: /var/tmp/nomadscreen-install)
  -h, --help              Show this help

If no repo is provided, the installer uses:
  https://github.com/xxredxpandaxx/BackpackingMediaServer_piZw.git
EOF
}

log() {
  printf '[nomadscreen-install] %s\n' "$*"
}

die() {
  printf '[nomadscreen-install] Error: %s\n' "$*" >&2
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

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --repo)
        [[ $# -ge 2 ]] || die "--repo requires a value"
        REPO_URL="$2"
        shift 2
        ;;
      --github)
        [[ $# -ge 2 ]] || die "--github requires a value"
        GITHUB_SLUG="$2"
        shift 2
        ;;
      --ref)
        [[ $# -ge 2 ]] || die "--ref requires a value"
        REPO_REF="$2"
        shift 2
        ;;
      --install-dir)
        [[ $# -ge 2 ]] || die "--install-dir requires a value"
        INSTALL_DIR="$2"
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
      --tmp-dir)
        [[ $# -ge 2 ]] || die "--tmp-dir requires a value"
        TMP_DIR="$2"
        shift 2
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

ensure_install_user() {
  if [[ -n "${SUDO_USER:-}" && "${SUDO_USER}" != "root" ]]; then
    INSTALL_USER="${SUDO_USER}"
  else
    INSTALL_USER="$(id -un)"
  fi

  [[ -n "${INSTALL_USER}" ]] || die "Could not determine the install user"
  id "${INSTALL_USER}" >/dev/null 2>&1 || die "Install user '${INSTALL_USER}' does not exist"
  INSTALL_GROUP="$(id -gn "${INSTALL_USER}")"
  INSTALL_HOME="$(getent passwd "${INSTALL_USER}" | cut -d: -f6)"
  [[ -n "${INSTALL_HOME}" ]] || INSTALL_HOME="$(eval echo "~${INSTALL_USER}")"
  [[ -n "${MEDIA_ROOT}" ]] || MEDIA_ROOT="${INSTALL_HOME}/media"
}

install_packages() {
  log "Installing required packages"
  run_root apt-get update
  run_root apt-get install -y git python3 python3-venv ca-certificates network-manager
}

clean_generated_checkout_files() {
  local generated_path

  log "Cleaning generated files from the existing checkout"
  for generated_path in ".venv" ".tmp" ".pytest_cache"; do
    if [[ -e "${INSTALL_DIR}/${generated_path}" ]]; then
      run_root rm -rf "${INSTALL_DIR}/${generated_path}"
    fi
  done

  run_root find "${INSTALL_DIR}" \
    -type d \
    \( -name "__pycache__" -o -name ".pytest_cache" \) \
    -prune \
    -exec rm -rf {} +

  run_as_install_user git -C "${INSTALL_DIR}" clean -fdX >/dev/null 2>&1 || true
  run_root chown -R "${INSTALL_USER}:${INSTALL_GROUP}" "${INSTALL_DIR}"
}

prepare_repo() {
  local install_parent

  install_parent="$(dirname "${INSTALL_DIR}")"
  run_root mkdir -p "${install_parent}"

  if [[ -e "${INSTALL_DIR}" && ! -d "${INSTALL_DIR}" ]]; then
    die "Install path exists and is not a directory: ${INSTALL_DIR}"
  fi

  if [[ ! -d "${INSTALL_DIR}" ]]; then
    run_root mkdir -p "${INSTALL_DIR}"
  fi

  run_root chown -R "${INSTALL_USER}:${INSTALL_GROUP}" "${INSTALL_DIR}"

  if [[ -d "${INSTALL_DIR}/.git" ]]; then
    log "Updating existing checkout in ${INSTALL_DIR}"
    clean_generated_checkout_files
    if run_as_install_user git -C "${INSTALL_DIR}" status --porcelain | grep -q .; then
      die "Existing checkout has local changes. Commit or discard them before rerunning the installer."
    fi

    run_as_install_user git -C "${INSTALL_DIR}" remote set-url origin "${REPO_URL}"
    run_as_install_user git -C "${INSTALL_DIR}" fetch --depth 1 origin "${REPO_REF}"
    run_as_install_user git -C "${INSTALL_DIR}" checkout -B "${REPO_REF}" FETCH_HEAD
  else
    if find "${INSTALL_DIR}" -mindepth 1 -maxdepth 1 -print -quit | grep -q .; then
      die "Install directory is not empty and is not a git checkout: ${INSTALL_DIR}"
    fi

    log "Cloning ${REPO_URL} into ${INSTALL_DIR}"
    run_as_install_user git clone --depth 1 --branch "${REPO_REF}" "${REPO_URL}" "${INSTALL_DIR}"
  fi

  if [[ -f "${INSTALL_DIR}/install.sh" ]]; then
    run_root chmod 0755 "${INSTALL_DIR}/install.sh"
  fi
  if [[ -f "${INSTALL_DIR}/update.sh" ]]; then
    run_root chmod 0755 "${INSTALL_DIR}/update.sh"
  fi
  run_root chmod 0755 "${INSTALL_DIR}/deploy/network/nomadscreen-network.sh"
}

seed_storage() {
  log "Preparing runtime storage at ${STORAGE_ROOT}"
  run_root mkdir -p "${STORAGE_ROOT}"
  if [[ -f "${INSTALL_DIR}/sdcard-template/nomadscreen.config.json" ]]; then
    run_root cp -a -n "${INSTALL_DIR}/sdcard-template/nomadscreen.config.json" "${STORAGE_ROOT}/"
  fi
  if [[ -d "${INSTALL_DIR}/sdcard-template/tools" ]]; then
    run_root mkdir -p "${STORAGE_ROOT}/tools"
    run_root cp -a -n "${INSTALL_DIR}/sdcard-template/tools/." "${STORAGE_ROOT}/tools/"
  fi
  log "Preparing media library at ${MEDIA_ROOT}"
  run_root mkdir -p "${MEDIA_ROOT}"
  if [[ -d "${INSTALL_DIR}/sdcard-template/media" ]]; then
    run_root cp -a -n "${INSTALL_DIR}/sdcard-template/media/." "${MEDIA_ROOT}/"
  fi
  run_root chown -R "${INSTALL_USER}:${INSTALL_GROUP}" "${STORAGE_ROOT}"
  run_root chown -R "${INSTALL_USER}:${INSTALL_GROUP}" "${MEDIA_ROOT}"
}

prepare_tmp_dir() {
  log "Preparing temp build directory at ${TMP_DIR}"
  run_root mkdir -p "${TMP_DIR}"
  run_root chown -R "${INSTALL_USER}:${INSTALL_GROUP}" "${TMP_DIR}"
}

install_python_deps() {
  log "Creating virtual environment"
  run_as_install_user env TMPDIR="${TMP_DIR}" python3 -m venv "${INSTALL_DIR}/.venv"

  log "Installing Python dependencies"
  run_as_install_user env TMPDIR="${TMP_DIR}" PIP_DISABLE_PIP_VERSION_CHECK=1 \
    "${INSTALL_DIR}/.venv/bin/pip" install --no-cache-dir --upgrade pip
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

  log "Writing systemd service to ${service_path}"
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
Environment=NOMADSCREEN_PORT=${HTTP_PORT}
ExecStart=${INSTALL_DIR}/.venv/bin/python ${INSTALL_DIR}/src/main.py
Restart=on-failure
RestartSec=5
AmbientCapabilities=CAP_NET_BIND_SERVICE
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
EOF

  log "Writing systemd service to ${service_path}"
  run_root install -m 0644 "${tmp_service}" "${service_path}"
  rm -f "${tmp_service}"
}

restart_service_unit() {
  local service_name="$1"

  log "Enabling ${service_name}.service"
  run_root systemctl daemon-reload
  run_root systemctl enable "${service_name}.service"

  if run_root systemctl is-active --quiet "${service_name}.service"; then
    log "Restarting ${service_name}.service to apply updates"
    run_root systemctl restart "${service_name}.service"
  else
    log "Starting ${service_name}.service"
    run_root systemctl start "${service_name}.service"
  fi

  if ! run_root systemctl is-active --quiet "${service_name}.service"; then
    run_root systemctl --no-pager --full status "${service_name}.service" || true
    die "Service failed to start"
  fi
}

start_service() {
  log "Ensuring NetworkManager is active"
  run_root systemctl enable --now NetworkManager.service
  restart_service_unit "${NETWORK_SERVICE_NAME}"
  restart_service_unit "${SERVICE_NAME}"
}

print_success() {
  local host_ip
  host_ip="$(hostname -I 2>/dev/null | awk '{print $1}')"

  log "Install complete"
  log "App directory: ${INSTALL_DIR}"
  log "Storage root: ${STORAGE_ROOT}"
  log "Media library: ${MEDIA_ROOT}"
  log "Network service: ${NETWORK_SERVICE_NAME}.service"
  log "Service name: ${SERVICE_NAME}.service"
  log "Copy your media into ${MEDIA_ROOT} and then use the Device page to rescan."

  if [[ -n "${host_ip}" ]]; then
    log "Open http://${host_ip}/app"
  fi
}

parse_args "$@"

if [[ -z "${REPO_URL}" && -n "${GITHUB_SLUG}" ]]; then
  REPO_URL="https://github.com/${GITHUB_SLUG}.git"
fi

[[ -n "${REPO_URL}" ]] || die "No repository URL is configured"

command -v sudo >/dev/null 2>&1 || die "sudo is required"
command -v python3 >/dev/null 2>&1 || die "python3 is required"

ensure_install_user
install_packages
prepare_repo
seed_storage
prepare_tmp_dir
install_python_deps
write_network_service
write_service
start_service
print_success
