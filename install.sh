#!/usr/bin/env bash

set -Eeuo pipefail

LEGACY_SERVICE_NAME="nomadscreen"
LEGACY_NETWORK_SERVICE_NAME="nomadscreen-network"
LEGACY_FILEBROWSER_SERVICE_NAME="nomadscreen-filebrowser"
SERVICE_NAME="${NOMADSCREEN_SERVICE_NAME:-backcountry-broadcast}"
NETWORK_SERVICE_NAME="${NOMADSCREEN_NETWORK_SERVICE_NAME:-backcountry-broadcast-network}"
FILEBROWSER_SERVICE_NAME="${NOMADSCREEN_FILEBROWSER_SERVICE_NAME:-backcountry-broadcast-filebrowser}"
SCREEN_SERVICE_NAME="${NOMADSCREEN_SCREEN_SERVICE_NAME:-backcountry-broadcast-screen}"
INSTALL_DIR="${NOMADSCREEN_INSTALL_DIR:-/opt/backcountry-broadcast}"
STORAGE_ROOT="${NOMADSCREEN_STORAGE_ROOT:-/srv/backcountry-broadcast}"
MEDIA_ROOT="${NOMADSCREEN_MEDIA_ROOT:-}"
REPO_URL="${NOMADSCREEN_REPO_URL:-https://github.com/xxredxpandaxx/BackpackingMediaServer_piZw.git}"
REPO_REF="${NOMADSCREEN_REPO_REF:-main}"
GITHUB_SLUG="${NOMADSCREEN_GITHUB_SLUG:-xxredxpandaxx/BackpackingMediaServer_piZw}"
HTTP_PORT="${NOMADSCREEN_PORT:-80}"
FILEBROWSER_PORT="${NOMADSCREEN_FILEBROWSER_PORT:-8081}"
TMP_DIR="${NOMADSCREEN_TMP_DIR:-/var/tmp/backcountry-broadcast-install}"
UPLOAD_TMP_DIR="${NOMADSCREEN_UPLOAD_TMP_DIR:-/var/tmp/backcountry-broadcast-upload}"
BOOT_CONFIG_PATH="${NOMADSCREEN_BOOT_CONFIG_PATH:-}"
WAVESHARE_FBCP_URL="${NOMADSCREEN_WAVESHARE_FBCP_URL:-https://files.waveshare.com/upload/1/18/Waveshare_fbcp.zip}"
DISPLAY_BOOT_CONFIG_CHANGED=0
DISPLAY_CONSOLE_KMS_WARNING=0
DISPLAY_CONSOLE_UNSUPPORTED=0

usage() {
  cat <<'EOF'
Backcountry Broadcast installer

Usage:
  install.sh
  install.sh --repo https://github.com/owner/repo.git [options]
  install.sh --github owner/repo [options]

Options:
  --repo URL              Public git clone URL to install from
  --github owner/repo     GitHub repo shorthand for public repos
  --ref REF               Branch, tag, or ref to install (default: main)
  --install-dir PATH      App install path (default: /opt/backcountry-broadcast)
  --storage-root PATH     Runtime storage root (default: /srv/backcountry-broadcast)
  --media-root PATH       Media library path (default: ~/media for the install user)
  --port PORT             HTTP port for the service (default: 80)
  --tmp-dir PATH          Temp build dir for venv/pip work (default: /var/tmp/backcountry-broadcast-install)
  --upload-tmp-dir PATH   Temp dir for large web uploads (default: /var/tmp/backcountry-broadcast-upload)
  -h, --help              Show this help

If no repo is provided, the installer uses:
  https://github.com/xxredxpandaxx/BackpackingMediaServer_piZw.git
EOF
}

log() {
  printf '[backcountry-broadcast-install] %s\n' "$*"
}

die() {
  printf '[backcountry-broadcast-install] Error: %s\n' "$*" >&2
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

print_checkout_status() {
  run_as_install_user git -C "${INSTALL_DIR}" status --short || true
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
      --upload-tmp-dir)
        [[ $# -ge 2 ]] || die "--upload-tmp-dir requires a value"
        UPLOAD_TMP_DIR="$2"
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

resolve_boot_config_path() {
  if [[ -n "${BOOT_CONFIG_PATH}" ]]; then
    return
  fi
  if [[ -f "/boot/firmware/config.txt" ]]; then
    BOOT_CONFIG_PATH="/boot/firmware/config.txt"
  else
    BOOT_CONFIG_PATH="/boot/config.txt"
  fi
}

read_display_runtime_settings() {
  python3 - "${STORAGE_ROOT}" <<'PY'
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
DEFAULTS = {
    "displayEnabled": False,
    "displayBackend": "userspace",
    "displayModel": "waveshare-1.69",
}

def read_json(path: Path) -> dict:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}

def merge(base, override):
    if isinstance(base, dict) and isinstance(override, dict):
        merged = dict(base)
        for key, value in override.items():
            merged[key] = merge(merged[key], value) if key in merged else value
        return merged
    return override

config = {}
for name in ("backcountry-broadcast.config.json", "nomadscreen.config.json"):
    path = root / name
    if path.exists():
        config = read_json(path)
        break

for name in ("backcountry-broadcast.user.json", "nomadscreen.user.json"):
    path = root / name
    if path.exists():
        config = merge(config, read_json(path))
        break

display = config.get("display") if isinstance(config.get("display"), dict) else {}

def as_bool(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "on"}

enabled = as_bool(config.get("displayEnabled", display.get("enabled")), DEFAULTS["displayEnabled"])
backend = str(config.get("displayBackend") or display.get("backend") or DEFAULTS["displayBackend"]).strip().lower() or DEFAULTS["displayBackend"]
model = str(config.get("displayModel") or display.get("model") or DEFAULTS["displayModel"]).strip().lower() or DEFAULTS["displayModel"]
print(f"{int(enabled)}|{backend}|{model}")
PY
}

console_hdmi_cvt() {
  case "$1" in
    waveshare-1.9)
      printf '640 340 60 6 0 0 0\n'
      ;;
    *)
      printf '560 480 60 6 0 0 0\n'
      ;;
  esac
}

console_cmake_flag() {
  case "$1" in
    waveshare-1.9)
      printf 'WAVESHARE_1INCH9_LCD\n'
      ;;
    *)
      printf 'WAVESHARE_1INCH69_LCD\n'
      ;;
  esac
}

install_waveshare_fbcp() {
  local download_root
  local archive_path
  local source_root
  local cmake_flag
  local build_dir
  local model
  local display_enabled
  local display_backend
  local display_model

  IFS='|' read -r display_enabled display_backend display_model < <(read_display_runtime_settings)
  if [[ "${display_enabled}" != "1" || "${display_backend}" != "console" ]]; then
    log "Skipping Waveshare console-mirror build because TFT console mode is not enabled"
    return
  fi

  log "Building Waveshare console-mirror binaries for console display mode"
  run_root apt-get update
  if ! run_root apt-get install -y libraspberrypi-dev; then
    DISPLAY_CONSOLE_UNSUPPORTED=1
    log "Skipping console backend build because libraspberrypi-dev is unavailable on this OS image. The TFT can still run in userspace mode."
    return
  fi
  download_root="${TMP_DIR}/waveshare-fbcp"
  archive_path="${download_root}/Waveshare_fbcp.zip"
  run_root rm -rf "${download_root}"
  run_root mkdir -p "${download_root}"
  run_root chown -R "${INSTALL_USER}:${INSTALL_GROUP}" "${download_root}"
  run_root mkdir -p "${INSTALL_DIR}/bin"
  run_root chown -R "${INSTALL_USER}:${INSTALL_GROUP}" "${INSTALL_DIR}/bin"

  run_as_install_user curl -fsSL "${WAVESHARE_FBCP_URL}" -o "${archive_path}"
  run_as_install_user unzip -oq "${archive_path}" -d "${download_root}"
  source_root="$(
    find "${download_root}" -mindepth 1 -maxdepth 3 -type f -name CMakeLists.txt \
      | head -n 1 \
      | xargs -r dirname
  )"
  [[ -n "${source_root}" && -f "${source_root}/CMakeLists.txt" ]] || die "Could not locate Waveshare fbcp source after download"

  for model in "waveshare-1.69" "waveshare-1.9"; do
    cmake_flag="$(console_cmake_flag "${model}")"
    build_dir="${download_root}/build-${model}"
    run_root rm -rf "${build_dir}"
    run_root mkdir -p "${build_dir}"
    run_root chown -R "${INSTALL_USER}:${INSTALL_GROUP}" "${build_dir}"
    (
      cd "${build_dir}"
      run_as_install_user cmake \
        -DSPI_BUS_CLOCK_DIVISOR=20 \
        "-D${cmake_flag}=ON" \
        -DBACKLIGHT_CONTROL=ON \
        -DSTATISTICS=0 \
        "${source_root}"
      run_as_install_user cmake --build . --parallel
    )
    [[ -x "${build_dir}/fbcp" ]] || die "Waveshare fbcp build did not produce fbcp for ${model}"
    run_root install -D -m 0755 "${build_dir}/fbcp" "${INSTALL_DIR}/bin/fbcp-${model}"
  done
}

apply_display_boot_config() {
  local display_enabled
  local display_backend
  local display_model
  local tmp_config
  local hdmi_cvt
  local binary_path

  resolve_boot_config_path
  if [[ ! -f "${BOOT_CONFIG_PATH}" ]]; then
    log "Skipping console display boot config because ${BOOT_CONFIG_PATH} does not exist yet"
    return
  fi

  IFS='|' read -r display_enabled display_backend display_model < <(read_display_runtime_settings)
  tmp_config="$(mktemp)"
  awk '
    BEGIN { skipping = 0 }
    /^# BEGIN BACKCOUNTRY BROADCAST CONSOLE DISPLAY$/ { skipping = 1; next }
    /^# END BACKCOUNTRY BROADCAST CONSOLE DISPLAY$/ { skipping = 0; next }
    !skipping { print }
  ' "${BOOT_CONFIG_PATH}" >"${tmp_config}"

  if [[ "${display_enabled}" == "1" && "${display_backend}" == "console" ]]; then
    binary_path="${INSTALL_DIR}/bin/fbcp-${display_model}"
    if [[ ! -x "${binary_path}" ]]; then
      DISPLAY_CONSOLE_UNSUPPORTED=1
      log "Skipping TFT boot-console config because the console backend binary is unavailable for ${display_model}. The screen launcher will continue using userspace mode."
      rm -f "${tmp_config}"
      return
    fi
    hdmi_cvt="$(console_hdmi_cvt "${display_model}")"
    cat >>"${tmp_config}" <<EOF
# BEGIN BACKCOUNTRY BROADCAST CONSOLE DISPLAY
dtparam=spi=on
hdmi_force_hotplug=1
hdmi_group=2
hdmi_mode=87
display_rotate=0
hdmi_cvt=${hdmi_cvt}
# END BACKCOUNTRY BROADCAST CONSOLE DISPLAY
EOF
    if grep -Eq '^[[:space:]]*dtoverlay=vc4-kms-v3d' "${BOOT_CONFIG_PATH}"; then
      DISPLAY_CONSOLE_KMS_WARNING=1
      log "Console display mode is selected, but ${BOOT_CONFIG_PATH} still enables vc4-kms-v3d. If the TFT stays blank at boot, switch that line to vc4-fkms-v3d or comment it out per the Waveshare fbcp instructions."
    fi
  fi

  if ! cmp -s "${tmp_config}" "${BOOT_CONFIG_PATH}"; then
    log "Updating ${BOOT_CONFIG_PATH} for the selected display mode"
    run_root install -m 0644 "${tmp_config}" "${BOOT_CONFIG_PATH}"
    DISPLAY_BOOT_CONFIG_CHANGED=1
  fi
  rm -f "${tmp_config}"
}

install_packages() {
  log "Installing required packages"
  run_root apt-get update
  run_root apt-get install -y curl git python3 python3-venv build-essential ca-certificates cmake network-manager unzip
}

python_dev_package() {
  local python_bin="${1:-python3}"
  local python_version

  python_version="$("${python_bin}" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || true)"
  if [[ -n "${python_version}" ]]; then
    printf 'python%s-dev\n' "${python_version}"
  fi
}

ensure_python_build_prereqs() {
  local python_bin="${1:-python3}"
  local python_dev_pkg

  log "Ensuring Python build prerequisites are installed"
  run_root apt-get update
  python_dev_pkg="$(python_dev_package "${python_bin}")"
  if [[ -n "${python_dev_pkg}" ]]; then
    if run_root apt-get install -y build-essential "${python_dev_pkg}"; then
      return
    fi
    log "Falling back to generic python3-dev because ${python_dev_pkg} was not available"
  fi
  run_root apt-get install -y build-essential python3-dev
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
    configure_checkout_git
    clean_generated_checkout_files
    if print_checkout_status | grep -q .; then
      log "Existing checkout still has local changes:"
      print_checkout_status | sed 's/^/[backcountry-broadcast-install]   /'
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

  configure_checkout_git

  if [[ -f "${INSTALL_DIR}/install.sh" ]]; then
    run_root chmod 0755 "${INSTALL_DIR}/install.sh"
  fi
  if [[ -f "${INSTALL_DIR}/update.sh" ]]; then
    run_root chmod 0755 "${INSTALL_DIR}/update.sh"
  fi
  run_root chmod 0755 "${INSTALL_DIR}/deploy/network/backcountry-broadcast-network.sh"
  if [[ -f "${INSTALL_DIR}/tools/backcountry_broadcast_screen.py" ]]; then
    run_root chmod 0755 "${INSTALL_DIR}/tools/backcountry_broadcast_screen.py"
  fi
}

seed_storage() {
  local retained_config_path
  log "Preparing runtime storage at ${STORAGE_ROOT}"
  run_root mkdir -p "${STORAGE_ROOT}"
  retained_config_path="${STORAGE_ROOT}/backcountry-broadcast.user.json"
  if [[ -f "${STORAGE_ROOT}/nomadscreen.config.json" && ! -f "${STORAGE_ROOT}/backcountry-broadcast.config.json" ]]; then
    run_root mv "${STORAGE_ROOT}/nomadscreen.config.json" "${STORAGE_ROOT}/backcountry-broadcast.config.json"
  fi
  if [[ -f "${INSTALL_DIR}/backcountry-broadcast.config.example.json" ]]; then
    run_root cp -a -n "${INSTALL_DIR}/backcountry-broadcast.config.example.json" "${STORAGE_ROOT}/backcountry-broadcast.config.json"
  fi
  if [[ -f "${STORAGE_ROOT}/nomadscreen.user.json" && ! -f "${retained_config_path}" ]]; then
    run_root mv "${STORAGE_ROOT}/nomadscreen.user.json" "${retained_config_path}"
  fi
  if [[ ! -f "${retained_config_path}" ]]; then
    printf '{}\n' | run_root tee "${retained_config_path}" >/dev/null
  fi
  log "Preparing media library at ${MEDIA_ROOT}"
  run_root mkdir -p \
    "${MEDIA_ROOT}" \
    "${MEDIA_ROOT}/movies" \
    "${MEDIA_ROOT}/tv" \
    "${MEDIA_ROOT}/music" \
    "${MEDIA_ROOT}/audiobooks" \
    "${MEDIA_ROOT}/documents"
  run_root chown -R "${INSTALL_USER}:${INSTALL_GROUP}" "${STORAGE_ROOT}"
  run_root chown -R "${INSTALL_USER}:${INSTALL_GROUP}" "${MEDIA_ROOT}"
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

install_python_deps() {
  log "Creating virtual environment"
  run_as_install_user env TMPDIR="${TMP_DIR}" python3 -m venv "${INSTALL_DIR}/.venv"

  ensure_python_build_prereqs "${INSTALL_DIR}/.venv/bin/python"

  log "Installing Python dependencies"
  run_as_install_user env TMPDIR="${TMP_DIR}" PIP_DISABLE_PIP_VERSION_CHECK=1 \
    "${INSTALL_DIR}/.venv/bin/pip" install --no-cache-dir --upgrade pip
  run_as_install_user env TMPDIR="${TMP_DIR}" PIP_DISABLE_PIP_VERSION_CHECK=1 \
    "${INSTALL_DIR}/.venv/bin/pip" install --no-cache-dir -r "${INSTALL_DIR}/requirements.txt"
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
Description=Backcountry Broadcast media server
After=network.target ${NETWORK_SERVICE_NAME}.service
Wants=${NETWORK_SERVICE_NAME}.service

[Service]
Type=simple
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

  log "Writing systemd service to ${service_path}"
  run_root install -m 0644 "${tmp_service}" "${service_path}"
  rm -f "${tmp_service}"
}

write_screen_service() {
  local tmp_service
  local service_path

  service_path="/etc/systemd/system/${SCREEN_SERVICE_NAME}.service"
  tmp_service="$(mktemp)"

  cat >"${tmp_service}" <<EOF
[Unit]
Description=Backcountry Broadcast display launcher
After=local-fs.target
Before=${SERVICE_NAME}.service

[Service]
Type=simple
User=${INSTALL_USER}
Group=${INSTALL_GROUP}
WorkingDirectory=${INSTALL_DIR}
Environment=PYTHONUNBUFFERED=1
Environment=NOMADSCREEN_STORAGE_ROOT=${STORAGE_ROOT}
Environment=NOMADSCREEN_MEDIA_ROOT=${MEDIA_ROOT}
Environment=NOMADSCREEN_PORT=${HTTP_PORT}
ExecStart=${INSTALL_DIR}/.venv/bin/python ${INSTALL_DIR}/tools/backcountry_broadcast_screen.py
Restart=always
RestartSec=5
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
EOF

  log "Writing systemd service to ${service_path}"
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

  log "Writing systemd service to ${service_path}"
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
    if [[ -f "/etc/systemd/system/${legacy_service}.service" ]]; then
      log "Removing legacy service alias ${legacy_service}.service"
      run_root systemctl disable --now "${legacy_service}.service" >/dev/null 2>&1 || true
      run_root rm -f "/etc/systemd/system/${legacy_service}.service"
    fi
  done
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

start_service() {
  log "Ensuring NetworkManager is active"
  run_root systemctl enable --now NetworkManager.service
  restart_service_unit "${NETWORK_SERVICE_NAME}"
  restart_service_unit "${SCREEN_SERVICE_NAME}"
  restart_service_unit "${SERVICE_NAME}"
  restart_service_unit "${FILEBROWSER_SERVICE_NAME}"
  capture_filebrowser_password
  configure_filebrowser_branding
  restart_service_unit "${FILEBROWSER_SERVICE_NAME}"
}

print_success() {
  local host_ip
  host_ip="$(hostname -I 2>/dev/null | awk '{print $1}')"

  log "Install complete"
  log "App directory: ${INSTALL_DIR}"
  log "Storage root: ${STORAGE_ROOT}"
  log "Media library: ${MEDIA_ROOT}"
  log "Upload temp dir: ${UPLOAD_TMP_DIR}"
  log "Network service: ${NETWORK_SERVICE_NAME}.service"
  log "Service name: ${SERVICE_NAME}.service"
  log "Screen service: ${SCREEN_SERVICE_NAME}.service"
  log "File Browser service: ${FILEBROWSER_SERVICE_NAME}.service"
  log "File Browser password file: ${STORAGE_ROOT}/filebrowser/admin-password.txt"
  log "Copy your media into ${MEDIA_ROOT} and then use the Device page to rescan."
  if [[ "${DISPLAY_BOOT_CONFIG_CHANGED}" == "1" ]]; then
    log "Reboot the Pi to apply the new TFT boot-console settings."
  fi
  if [[ "${DISPLAY_CONSOLE_KMS_WARNING}" == "1" ]]; then
    log "If the TFT stays blank in console mode, update ${BOOT_CONFIG_PATH} to stop using vc4-kms-v3d as Waveshare recommends for fbcp."
  fi
  if [[ "${DISPLAY_CONSOLE_UNSUPPORTED}" == "1" ]]; then
    log "This OS image does not support the Waveshare fbcp console backend. The physical screen will keep working in app-driven userspace mode instead."
  fi

  if [[ -n "${host_ip}" ]]; then
    log "Open http://${host_ip}/app"
    log "Open File Browser at http://${host_ip}:${FILEBROWSER_PORT}"
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
prepare_filebrowser_storage
install_python_deps
install_waveshare_fbcp
install_filebrowser_binary
apply_display_boot_config
write_network_service
write_service
write_screen_service
write_filebrowser_service
cleanup_legacy_service_units
start_service
print_success
