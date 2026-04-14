#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_INSTALL_DIR="/opt/backcountry-broadcast"
LEGACY_INSTALL_DIR="/opt/nomadscreen"
LEGACY_SERVICE_NAME="nomadscreen"
LEGACY_NETWORK_SERVICE_NAME="nomadscreen-network"
LEGACY_FILEBROWSER_SERVICE_NAME="nomadscreen-filebrowser"
SERVICE_NAME="${NOMADSCREEN_SERVICE_NAME:-backcountry-broadcast}"
NETWORK_SERVICE_NAME="${NOMADSCREEN_NETWORK_SERVICE_NAME:-backcountry-broadcast-network}"
FILEBROWSER_SERVICE_NAME="${NOMADSCREEN_FILEBROWSER_SERVICE_NAME:-backcountry-broadcast-filebrowser}"
INSTALL_DIR="${NOMADSCREEN_INSTALL_DIR:-}"
STORAGE_ROOT="${NOMADSCREEN_STORAGE_ROOT:-}"
MEDIA_ROOT="${NOMADSCREEN_MEDIA_ROOT:-}"
HTTP_PORT="${NOMADSCREEN_PORT:-}"
FILEBROWSER_PORT="${NOMADSCREEN_FILEBROWSER_PORT:-}"
TMP_DIR="${NOMADSCREEN_TMP_DIR:-/var/tmp/backcountry-broadcast-install}"
UPLOAD_TMP_DIR="${NOMADSCREEN_UPLOAD_TMP_DIR:-}"
REPO_REF="${NOMADSCREEN_REPO_REF:-}"
RESTART_NETWORK=0

usage() {
  cat <<'EOF'
Backcountry Broadcast updater

Usage:
  update.sh
  update.sh [options]

Options:
  --install-dir PATH      App install path (default: detected install or /opt/backcountry-broadcast)
  --service-name NAME     systemd service name (default: backcountry-broadcast)
  --network-service NAME  Wi-Fi fallback service name (default: backcountry-broadcast-network)
  --storage-root PATH     Runtime storage root (default: preserved from installed service)
  --media-root PATH       Media library path (default: preserved from installed service)
  --port PORT             HTTP port for the service (default: preserved from installed service)
  --ref REF               Branch or ref to update to (default: current checked-out branch)
  --tmp-dir PATH          Temp build dir for pip work (default: /var/tmp/backcountry-broadcast-install)
  --upload-tmp-dir PATH   Temp dir for large web uploads (default: preserved from installed service or /var/tmp/backcountry-broadcast-upload)
  --restart-network       Restart the Wi-Fi fallback service too
  -h, --help              Show this help
EOF
}

log() {
  printf '[backcountry-broadcast-update] %s\n' "$*"
}

die() {
  printf '[backcountry-broadcast-update] Error: %s\n' "$*" >&2
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

configure_checkout_git() {
  run_as_install_user git -C "${INSTALL_DIR}" config core.fileMode false >/dev/null 2>&1 || true
}

clean_generated_checkout_files() {
  local generated_path

  log "Cleaning generated files from the existing checkout"
  for generated_path in ".tmp" ".pytest_cache"; do
    if [[ -e "${INSTALL_DIR}/${generated_path}" ]]; then
      run_root rm -rf "${INSTALL_DIR}/${generated_path}"
    fi
  done

  run_root find "${INSTALL_DIR}" \
    -type d \
    \( -name "__pycache__" -o -name ".pytest_cache" \) \
    -prune \
    -exec rm -rf {} +

  run_root chown -R "${INSTALL_USER}:${INSTALL_GROUP}" "${INSTALL_DIR}"
}

print_checkout_status() {
  run_as_install_user git -C "${INSTALL_DIR}" status --short || true
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

resolve_existing_service_path() {
  local preferred_name="$1"
  local legacy_name="$2"

  if [[ -f "/etc/systemd/system/${preferred_name}.service" ]]; then
    printf '/etc/systemd/system/%s.service\n' "${preferred_name}"
    return
  fi
  if [[ -f "/etc/systemd/system/${legacy_name}.service" ]]; then
    printf '/etc/systemd/system/%s.service\n' "${legacy_name}"
    return
  fi
  printf '/etc/systemd/system/%s.service\n' "${preferred_name}"
}

resolve_install_dir() {
  local service_path
  local working_directory

  if [[ -n "${INSTALL_DIR}" ]]; then
    return
  fi

  if [[ -d "${SCRIPT_DIR}/.git" && -f "${SCRIPT_DIR}/src/main.py" ]]; then
    INSTALL_DIR="${SCRIPT_DIR}"
    return
  fi

  service_path="$(resolve_existing_service_path "${SERVICE_NAME}" "${LEGACY_SERVICE_NAME}")"
  working_directory="$(read_unit_value "${service_path}" "WorkingDirectory" || true)"
  if [[ -n "${working_directory}" && -d "${working_directory}/.git" ]]; then
    INSTALL_DIR="${working_directory}"
    return
  fi

  if [[ -d "${DEFAULT_INSTALL_DIR}/.git" ]]; then
    INSTALL_DIR="${DEFAULT_INSTALL_DIR}"
    return
  fi

  if [[ -d "${LEGACY_INSTALL_DIR}/.git" ]]; then
    INSTALL_DIR="${LEGACY_INSTALL_DIR}"
    return
  fi

  INSTALL_DIR="${DEFAULT_INSTALL_DIR}"
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

  service_path="$(resolve_existing_service_path "${SERVICE_NAME}" "${LEGACY_SERVICE_NAME}")"
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
  [[ -n "${FILEBROWSER_PORT}" ]] || FILEBROWSER_PORT="$(read_unit_environment "${service_path}" "NOMADSCREEN_FILEBROWSER_PORT" || true)"

  [[ -n "${STORAGE_ROOT}" ]] || STORAGE_ROOT="/srv/backcountry-broadcast"
  [[ -n "${MEDIA_ROOT}" ]] || MEDIA_ROOT="${INSTALL_HOME}/media"
  [[ -n "${UPLOAD_TMP_DIR}" ]] || UPLOAD_TMP_DIR="/var/tmp/backcountry-broadcast-upload"
  [[ -n "${HTTP_PORT}" ]] || HTTP_PORT="80"
  [[ -n "${FILEBROWSER_PORT}" ]] || FILEBROWSER_PORT="8081"
}

prepare_tmp_dir() {
  log "Preparing temp build directory at ${TMP_DIR}"
  run_root mkdir -p "${TMP_DIR}"
  run_root chown -R "${INSTALL_USER}:${INSTALL_GROUP}" "${TMP_DIR}"
  log "Preparing upload temp directory at ${UPLOAD_TMP_DIR}"
  run_root mkdir -p "${UPLOAD_TMP_DIR}"
  run_root chown -R "${INSTALL_USER}:${INSTALL_GROUP}" "${UPLOAD_TMP_DIR}"
}

prepare_filebrowser_storage() {
  local state_dir
  state_dir="${STORAGE_ROOT}/filebrowser"
  log "Preparing File Browser state directory at ${state_dir}"
  run_root mkdir -p "${state_dir}"
  run_root chown -R "${INSTALL_USER}:${INSTALL_GROUP}" "${state_dir}"
}

configure_filebrowser_branding() {
  local state_dir
  local database_path
  local branding_dir

  state_dir="${STORAGE_ROOT}/filebrowser"
  database_path="${state_dir}/filebrowser.db"
  branding_dir="${INSTALL_DIR}/deploy/filebrowser-branding"

  if [[ ! -d "${branding_dir}" ]]; then
    log "Skipping File Browser branding: ${branding_dir} not found"
    return
  fi

  if [[ ! -f "${database_path}" ]]; then
    log "Skipping File Browser branding: ${database_path} not found yet"
    return
  fi

  log "Applying File Browser branding from ${branding_dir}"
  run_as_install_user /usr/local/bin/filebrowser config set \
    --database "${database_path}" \
    --branding.name "Backcountry Broadcast" \
    --branding.files "${branding_dir}" >/dev/null
}

migrate_legacy_runtime_names() {
  if [[ -f "${STORAGE_ROOT}/nomadscreen.config.json" && ! -f "${STORAGE_ROOT}/backcountry-broadcast.config.json" ]]; then
    log "Renaming runtime config to backcountry-broadcast.config.json"
    run_root mv "${STORAGE_ROOT}/nomadscreen.config.json" "${STORAGE_ROOT}/backcountry-broadcast.config.json"
  fi
  if [[ -f "${STORAGE_ROOT}/nomadscreen.user.json" && ! -f "${STORAGE_ROOT}/backcountry-broadcast.user.json" ]]; then
    log "Renaming retained user settings to backcountry-broadcast.user.json"
    run_root mv "${STORAGE_ROOT}/nomadscreen.user.json" "${STORAGE_ROOT}/backcountry-broadcast.user.json"
  fi
  if [[ ! -f "${STORAGE_ROOT}/backcountry-broadcast.user.json" ]]; then
    log "Creating retained user settings file"
    printf '{}\n' | run_root tee "${STORAGE_ROOT}/backcountry-broadcast.user.json" >/dev/null
  fi
  run_root chown "${INSTALL_USER}:${INSTALL_GROUP}" "${STORAGE_ROOT}/backcountry-broadcast.config.json" >/dev/null 2>&1 || true
  run_root chown "${INSTALL_USER}:${INSTALL_GROUP}" "${STORAGE_ROOT}/backcountry-broadcast.user.json" >/dev/null 2>&1 || true
}

ensure_repo() {
  [[ -d "${INSTALL_DIR}/.git" ]] || die "No git checkout found at ${INSTALL_DIR}. Run install.sh first."
  run_root chown -R "${INSTALL_USER}:${INSTALL_GROUP}" "${INSTALL_DIR}"
  configure_checkout_git
  clean_generated_checkout_files
  if print_checkout_status | grep -q .; then
    log "Existing checkout still has local changes:"
    print_checkout_status | sed 's/^/[backcountry-broadcast-update]   /'
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
  configure_checkout_git

  if [[ -f "${INSTALL_DIR}/install.sh" ]]; then
    run_root chmod 0755 "${INSTALL_DIR}/install.sh"
  fi
  if [[ -f "${INSTALL_DIR}/update.sh" ]]; then
    run_root chmod 0755 "${INSTALL_DIR}/update.sh"
  fi
  run_root chmod 0755 "${INSTALL_DIR}/deploy/network/backcountry-broadcast-network.sh"
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

ensure_filebrowser_prereqs() {
  if command -v curl >/dev/null 2>&1; then
    return
  fi
  log "Installing curl for File Browser downloads"
  run_root apt-get update
  run_root apt-get install -y curl ca-certificates
}

install_filebrowser_binary() {
  local binary_path
  local download_dir
  local installer_path

  binary_path="$(command -v filebrowser || true)"
  if [[ -n "${binary_path}" ]]; then
    log "File Browser already installed at ${binary_path}"
    return
  fi

  ensure_filebrowser_prereqs
  log "Installing File Browser"
  download_dir="${TMP_DIR}/filebrowser-download"
  installer_path="${TMP_DIR}/filebrowser-get.sh"
  run_root rm -rf "${download_dir}"
  run_root mkdir -p "${download_dir}"
  run_root chown -R "${INSTALL_USER}:${INSTALL_GROUP}" "${download_dir}"
  run_as_install_user curl -fsSL https://raw.githubusercontent.com/filebrowser/get/master/get.sh -o "${installer_path}"
  run_root chmod 0755 "${installer_path}"
  (
    cd "${download_dir}"
    run_as_install_user env TMPDIR="${TMP_DIR}" bash "${installer_path}"
  )
  [[ -x "${download_dir}/filebrowser" ]] || die "File Browser install did not produce the expected binary"
  run_root install -m 0755 "${download_dir}/filebrowser" /usr/local/bin/filebrowser
}

write_network_service() {
  local tmp_service
  local service_path

  service_path="/etc/systemd/system/${NETWORK_SERVICE_NAME}.service"
  tmp_service="$(mktemp)"

  cat >"${tmp_service}" <<EOF
[Unit]
Description=Backcountry Broadcast Wi-Fi fallback
Wants=NetworkManager.service
After=NetworkManager.service
Before=${SERVICE_NAME}.service

[Service]
Type=oneshot
Environment=NOMADSCREEN_STORAGE_ROOT=${STORAGE_ROOT}
ExecStart=${INSTALL_DIR}/deploy/network/backcountry-broadcast-network.sh
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
Description=Backcountry Broadcast media server
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
Environment=NOMADSCREEN_FILEBROWSER_PORT=${FILEBROWSER_PORT}
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

write_filebrowser_service() {
  local tmp_service
  local service_path
  local state_dir
  local database_path

  state_dir="${STORAGE_ROOT}/filebrowser"
  database_path="${state_dir}/filebrowser.db"
  service_path="/etc/systemd/system/${FILEBROWSER_SERVICE_NAME}.service"
  tmp_service="$(mktemp)"

  cat >"${tmp_service}" <<EOF
[Unit]
Description=Backcountry Broadcast File Browser
After=network.target
Wants=network.target

[Service]
Type=simple
User=${INSTALL_USER}
Group=${INSTALL_GROUP}
WorkingDirectory=${state_dir}
ExecStart=/usr/local/bin/filebrowser --address 0.0.0.0 --port ${FILEBROWSER_PORT} --root ${MEDIA_ROOT} --database ${database_path}
Restart=on-failure
RestartSec=5
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
EOF

  log "Refreshing ${FILEBROWSER_SERVICE_NAME}.service"
  run_root install -m 0644 "${tmp_service}" "${service_path}"
  rm -f "${tmp_service}"
}

cleanup_legacy_service_units() {
  local legacy_service

  for legacy_service in \
    "${LEGACY_SERVICE_NAME}" \
    "${LEGACY_NETWORK_SERVICE_NAME}" \
    "${LEGACY_FILEBROWSER_SERVICE_NAME}"; do
    if [[ "${legacy_service}" == "${SERVICE_NAME}" || "${legacy_service}" == "${NETWORK_SERVICE_NAME}" || "${legacy_service}" == "${FILEBROWSER_SERVICE_NAME}" ]]; then
      continue
    fi
    if [[ "${legacy_service}" == "${LEGACY_NETWORK_SERVICE_NAME}" && "${RESTART_NETWORK}" != "1" ]]; then
      continue
    fi
    if [[ -f "/etc/systemd/system/${legacy_service}.service" ]]; then
      log "Removing legacy service alias ${legacy_service}.service"
      run_root systemctl disable --now "${legacy_service}.service" >/dev/null 2>&1 || true
      run_root rm -f "/etc/systemd/system/${legacy_service}.service"
    fi
  done
}

capture_filebrowser_password() {
  local state_dir
  local password_file
  local password
  local tmp_password
  local attempt

  state_dir="${STORAGE_ROOT}/filebrowser"
  password_file="${state_dir}/admin-password.txt"
  if [[ -s "${password_file}" ]]; then
    return
  fi

  log "Capturing the initial File Browser password from ${FILEBROWSER_SERVICE_NAME}.service logs"
  for attempt in 1 2 3 4 5 6; do
    password="$(
      run_root journalctl -u "${FILEBROWSER_SERVICE_NAME}.service" -n 80 --no-pager 2>/dev/null \
        | sed -n -E "s/.*randomly generated password: ([^[:space:]]+).*/\\1/p" \
        | tail -n 1
    )"
    if [[ -n "${password}" ]]; then
      tmp_password="$(mktemp)"
      printf '%s\n' "${password}" >"${tmp_password}"
      run_root install -m 0600 -o "${INSTALL_USER}" -g "${INSTALL_GROUP}" "${tmp_password}" "${password_file}"
      rm -f "${tmp_password}"
      log "Saved the initial File Browser password to ${password_file}"
      return
    fi
    sleep 1
  done

  log "Could not capture the initial File Browser password from the journal yet"
}

restart_services() {
  local defer_network_migration=0

  if [[ "${RESTART_NETWORK}" != "1" && "${NETWORK_SERVICE_NAME}" != "${LEGACY_NETWORK_SERVICE_NAME}" && -f "/etc/systemd/system/${LEGACY_NETWORK_SERVICE_NAME}.service" ]]; then
    defer_network_migration=1
  fi

  log "Reloading systemd"
  run_root systemctl daemon-reload
  run_root systemctl enable "${SERVICE_NAME}.service" >/dev/null
  run_root systemctl enable "${FILEBROWSER_SERVICE_NAME}.service" >/dev/null
  if [[ "${defer_network_migration}" != "1" ]]; then
    run_root systemctl enable "${NETWORK_SERVICE_NAME}.service" >/dev/null
  fi

  if [[ "${RESTART_NETWORK}" == "1" ]]; then
    log "Restarting ${NETWORK_SERVICE_NAME}.service"
    run_root systemctl restart "${NETWORK_SERVICE_NAME}.service"
  elif [[ "${defer_network_migration}" == "1" ]]; then
    log "Leaving ${LEGACY_NETWORK_SERVICE_NAME}.service enabled for now to avoid interrupting active Wi-Fi sessions"
  else
    log "Leaving ${NETWORK_SERVICE_NAME}.service running to avoid interrupting active Wi-Fi sessions"
  fi

  log "Restarting ${SERVICE_NAME}.service"
  run_root systemctl restart "${SERVICE_NAME}.service"
  log "Restarting ${FILEBROWSER_SERVICE_NAME}.service"
  run_root systemctl restart "${FILEBROWSER_SERVICE_NAME}.service"
  capture_filebrowser_password
  configure_filebrowser_branding
  log "Restarting ${FILEBROWSER_SERVICE_NAME}.service to apply branding"
  run_root systemctl restart "${FILEBROWSER_SERVICE_NAME}.service"
}

print_success() {
  local head_commit
  local legacy_network_pending=0
  head_commit="$(run_as_install_user git -C "${INSTALL_DIR}" rev-parse --short HEAD 2>/dev/null || true)"
  if [[ "${RESTART_NETWORK}" != "1" && "${NETWORK_SERVICE_NAME}" != "${LEGACY_NETWORK_SERVICE_NAME}" && -f "/etc/systemd/system/${LEGACY_NETWORK_SERVICE_NAME}.service" ]]; then
    legacy_network_pending=1
  fi

  log "Update complete"
  [[ -n "${head_commit}" ]] && log "Current commit: ${head_commit}"
  log "App directory: ${INSTALL_DIR}"
  log "Storage root: ${STORAGE_ROOT}"
  log "Media library: ${MEDIA_ROOT}"
  log "Upload temp dir: ${UPLOAD_TMP_DIR}"
  log "Service restarted: ${SERVICE_NAME}.service"
  log "File Browser service: ${FILEBROWSER_SERVICE_NAME}.service"
  log "File Browser password file: ${STORAGE_ROOT}/filebrowser/admin-password.txt"
  if [[ "${RESTART_NETWORK}" != "1" ]]; then
    if [[ "${legacy_network_pending}" == "1" ]]; then
      log "Run 'sudo ./update.sh --restart-network' later to finish renaming the Wi-Fi fallback service without breaking the current session."
    else
      log "Run 'sudo systemctl restart ${NETWORK_SERVICE_NAME}.service' later if you need hotspot/network script changes applied immediately."
    fi
  fi
}

parse_args "$@"
resolve_install_dir
ensure_install_context
prepare_tmp_dir
prepare_filebrowser_storage
migrate_legacy_runtime_names
ensure_repo
update_repo
install_python_deps
install_filebrowser_binary
write_network_service
write_service
write_filebrowser_service
cleanup_legacy_service_units
restart_services
print_success
