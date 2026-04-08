#!/usr/bin/env bash

set -Eeuo pipefail

DEFAULT_STORAGE_ROOT="/srv/backcountry-broadcast"
LEGACY_STORAGE_ROOT="/srv/nomadscreen"
DEFAULT_CONFIG_NAME="backcountry-broadcast.config.json"
LEGACY_CONFIG_NAME="nomadscreen.config.json"
DEFAULT_USER_CONFIG_NAME="backcountry-broadcast.user.json"
LEGACY_USER_CONFIG_NAME="nomadscreen.user.json"
DEFAULT_WIFI_INTERFACE="wlan0"
DEFAULT_CONNECT_TIMEOUT_SECONDS="20"
DEFAULT_ACCESS_POINT_PASSWORD="backpackingmedia"
DEFAULT_ACCESS_POINT_CONNECTION_NAME="Backcountry Broadcast Hotspot"
DEFAULT_ACCESS_POINT_ADDRESS="10.0.0.1/24"

STORAGE_ROOT="${NOMADSCREEN_STORAGE_ROOT:-${DEFAULT_STORAGE_ROOT}}"
if [[ -z "${NOMADSCREEN_STORAGE_ROOT:-}" && ! -e "${STORAGE_ROOT}" && -e "${LEGACY_STORAGE_ROOT}" ]]; then
  STORAGE_ROOT="${LEGACY_STORAGE_ROOT}"
fi
DEFAULT_CONFIG_PATH="${STORAGE_ROOT}/${DEFAULT_CONFIG_NAME}"
LEGACY_CONFIG_PATH="${STORAGE_ROOT}/${LEGACY_CONFIG_NAME}"
DEFAULT_USER_CONFIG_PATH="${STORAGE_ROOT}/${DEFAULT_USER_CONFIG_NAME}"
LEGACY_USER_CONFIG_PATH="${STORAGE_ROOT}/${LEGACY_USER_CONFIG_NAME}"
CONFIG_PATH="${NOMADSCREEN_CONFIG_PATH:-${DEFAULT_CONFIG_PATH}}"
if [[ -z "${NOMADSCREEN_CONFIG_PATH:-}" && ! -f "${CONFIG_PATH}" && -f "${LEGACY_CONFIG_PATH}" ]]; then
  CONFIG_PATH="${LEGACY_CONFIG_PATH}"
fi
USER_CONFIG_PATH="${NOMADSCREEN_USER_CONFIG_PATH:-${DEFAULT_USER_CONFIG_PATH}}"
if [[ -z "${NOMADSCREEN_USER_CONFIG_PATH:-}" && ! -f "${USER_CONFIG_PATH}" && -f "${LEGACY_USER_CONFIG_PATH}" ]]; then
  USER_CONFIG_PATH="${LEGACY_USER_CONFIG_PATH}"
fi

log() {
  printf '[backcountry-broadcast-network] %s\n' "$*"
}

die() {
  printf '[backcountry-broadcast-network] Error: %s\n' "$*" >&2
  exit 1
}

read_runtime_config() {
  mapfile -t config_values < <(
    python3 - "${CONFIG_PATH}" "${USER_CONFIG_PATH}" <<'PY'
import json
import sys
from pathlib import Path

DEFAULT_DEVICE_NAME = "Backcountry Broadcast"
DEFAULT_WIFI_INTERFACE = "wlan0"
DEFAULT_CONNECT_TIMEOUT_SECONDS = 20
DEFAULT_ACCESS_POINT_PASSWORD = "backpackingmedia"
DEFAULT_ACCESS_POINT_CONNECTION_NAME = "Backcountry Broadcast Hotspot"


def normalize_device_name(value: str) -> str:
    return " ".join(str(value or "").split()).strip()


def derive_compact_device_token(device_name: str) -> str:
    normalized = normalize_device_name(device_name)
    output = []
    capitalize_next = True
    for character in normalized:
        if character.isalnum():
            output.append(character.upper() if capitalize_next else character.lower())
            capitalize_next = False
        elif output:
            capitalize_next = True
    return "".join(output)


def merge_config_values(base, override):
    if isinstance(base, dict) and isinstance(override, dict):
        merged = dict(base)
        for key, value in override.items():
            if key in merged:
                merged[key] = merge_config_values(merged[key], value)
            else:
                merged[key] = value
        return merged
    return override


config_path = Path(sys.argv[1])
user_config_path = Path(sys.argv[2])
raw = {}
if config_path.exists():
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raw = {}
if user_config_path.exists():
    try:
        user_raw = json.loads(user_config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        user_raw = {}
    raw = merge_config_values(raw, user_raw)

wifi_block = raw.get("wifi") if isinstance(raw.get("wifi"), dict) else {}
device_name = normalize_device_name(str(raw.get("deviceName") or raw.get("serverName") or DEFAULT_DEVICE_NAME)) or DEFAULT_DEVICE_NAME
ssid = str(raw.get("hotspotSsid") or raw.get("accessPointSsid") or wifi_block.get("ssid") or "").strip()
ssid = " ".join(ssid.split())[:32] or derive_compact_device_token(device_name) or "BackcountryBroadcast"
password = str(raw.get("wifiPassword") or wifi_block.get("password") or DEFAULT_ACCESS_POINT_PASSWORD)
if len(password) < 8 or len(password) > 63:
    password = DEFAULT_ACCESS_POINT_PASSWORD
wifi_interface = str(raw.get("wifiInterface") or wifi_block.get("interface") or DEFAULT_WIFI_INTERFACE).strip() or DEFAULT_WIFI_INTERFACE
connect_timeout = raw.get("knownWifiTimeoutSeconds") or raw.get("wifiConnectTimeoutSeconds") or DEFAULT_CONNECT_TIMEOUT_SECONDS
try:
    connect_timeout = max(int(connect_timeout), 5)
except (TypeError, ValueError):
    connect_timeout = DEFAULT_CONNECT_TIMEOUT_SECONDS

fallback_enabled = raw.get("fallbackAccessPointEnabled")
if fallback_enabled is None:
    fallback_enabled = raw.get("accessPointEnabled")
if fallback_enabled is None:
    fallback_enabled = True
if isinstance(fallback_enabled, str):
    fallback_enabled = fallback_enabled.strip().lower() in {"1", "true", "yes", "on"}

connection_name = str(raw.get("fallbackAccessPointConnectionName") or f"{device_name} Hotspot" or DEFAULT_ACCESS_POINT_CONNECTION_NAME).strip()
if not connection_name:
    connection_name = DEFAULT_ACCESS_POINT_CONNECTION_NAME

print(ssid)
print(password)
print(wifi_interface)
print(connect_timeout)
print("1" if bool(fallback_enabled) else "0")
print(connection_name)
PY
  )

  [[ "${#config_values[@]}" -ge 6 ]] || die "Could not read hotspot settings from ${CONFIG_PATH}"

  ACCESS_POINT_SSID="${config_values[0]}"
  ACCESS_POINT_PASSWORD="${config_values[1]}"
  WIFI_INTERFACE="${config_values[2]}"
  CONNECT_TIMEOUT_SECONDS="${config_values[3]}"
  FALLBACK_ACCESS_POINT_ENABLED="${config_values[4]}"
  ACCESS_POINT_CONNECTION_NAME="${config_values[5]}"
}

wait_for_network_manager() {
  local attempt
  for attempt in $(seq 1 15); do
    if systemctl is-active --quiet NetworkManager.service && nmcli general status >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  die "NetworkManager is not ready"
}

wait_for_wifi_interface() {
  local attempt
  for attempt in $(seq 1 15); do
    if nmcli -t -g DEVICE device status 2>/dev/null | grep -Fxq "${WIFI_INTERFACE}"; then
      return 0
    fi
    sleep 2
  done
  die "Wi-Fi interface ${WIFI_INTERFACE} is not available"
}

current_connection_details() {
  local line
  line="$(nmcli -t -g DEVICE,TYPE,STATE,CONNECTION device status | awk -F: -v iface="${WIFI_INTERFACE}" '$1 == iface { print; exit }')"
  if [[ -z "${line}" ]]; then
    return 1
  fi

  IFS=: read -r CURRENT_DEVICE CURRENT_TYPE CURRENT_STATE CURRENT_CONNECTION <<<"${line}"
  return 0
}

current_connection_mode() {
  if [[ -z "${CURRENT_CONNECTION:-}" || "${CURRENT_CONNECTION}" == "--" ]]; then
    printf '\n'
    return 0
  fi
  nmcli -t -g 802-11-wireless.mode connection show "${CURRENT_CONNECTION}" 2>/dev/null || true
}

ensure_hotspot_profile() {
  if ! nmcli -t -g NAME connection show | grep -Fxq "${ACCESS_POINT_CONNECTION_NAME}"; then
    nmcli connection add \
      type wifi \
      ifname "${WIFI_INTERFACE}" \
      con-name "${ACCESS_POINT_CONNECTION_NAME}" \
      ssid "${ACCESS_POINT_SSID}" \
      autoconnect no >/dev/null
  fi

  nmcli connection modify "${ACCESS_POINT_CONNECTION_NAME}" \
    connection.interface-name "${WIFI_INTERFACE}" \
    connection.autoconnect no \
    wifi.mode ap \
    wifi.band bg \
    wifi.ssid "${ACCESS_POINT_SSID}" \
    wifi-sec.key-mgmt wpa-psk \
    wifi-sec.psk "${ACCESS_POINT_PASSWORD}" \
    ipv4.addresses "${DEFAULT_ACCESS_POINT_ADDRESS}" \
    ipv4.method shared \
    ipv6.method ignore >/dev/null
}

try_known_networks() {
  log "Trying known Wi-Fi networks on ${WIFI_INTERFACE} for up to ${CONNECT_TIMEOUT_SECONDS}s"
  nmcli radio wifi on >/dev/null 2>&1 || true
  nmcli device set "${WIFI_INTERFACE}" managed yes >/dev/null 2>&1 || true
  nmcli --wait "${CONNECT_TIMEOUT_SECONDS}" device up "${WIFI_INTERFACE}" >/dev/null 2>&1 || true

  if ! current_connection_details; then
    return 1
  fi

  if [[ "${CURRENT_TYPE}" == "wifi" && "${CURRENT_STATE}" == "connected" ]]; then
    if [[ "$(current_connection_mode)" != "ap" ]]; then
      log "Connected to known Wi-Fi network: ${CURRENT_CONNECTION}"
      return 0
    fi
  fi

  return 1
}

start_fallback_hotspot() {
  [[ "${FALLBACK_ACCESS_POINT_ENABLED}" == "1" ]] || {
    log "Fallback hotspot is disabled in config"
    return 0
  }

  ensure_hotspot_profile
  log "Starting fallback hotspot ${ACCESS_POINT_SSID}"
  nmcli connection up "${ACCESS_POINT_CONNECTION_NAME}" ifname "${WIFI_INTERFACE}" >/dev/null
}

main() {
  command -v nmcli >/dev/null 2>&1 || die "nmcli is required"
  command -v python3 >/dev/null 2>&1 || die "python3 is required"

  read_runtime_config
  wait_for_network_manager
  wait_for_wifi_interface

  if try_known_networks; then
    exit 0
  fi

  start_fallback_hotspot
}

main "$@"
