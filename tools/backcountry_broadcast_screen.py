#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import qrcode
import requests
from PIL import Image, ImageDraw, ImageFont


LOG_PREFIX = "[backcountry-broadcast-screen]"
APP_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_STORAGE_ROOT = Path("/srv/backcountry-broadcast")
DEFAULT_RUNTIME_CONFIG_NAME = "backcountry-broadcast.config.json"
DEFAULT_RUNTIME_USER_CONFIG_NAME = "backcountry-broadcast.user.json"
LEGACY_RUNTIME_CONFIG_NAME = "nomadscreen.config.json"
LEGACY_RUNTIME_USER_CONFIG_NAME = "nomadscreen.user.json"
DEFAULT_DEVICE_NAME = "Backcountry Broadcast"
DEFAULT_ACCESS_POINT_SSID = "BackcountryBroadcast"
DEFAULT_ACCESS_POINT_PASSWORD = "backpackingmedia"
DEFAULT_BIND_ADDRESS = "0.0.0.0"
DEFAULT_HTTP_PORT = 80
DEFAULT_MDNS_HOST = "backcountrybroadcast"
DEFAULT_DISPLAY_ENABLED = False
DEFAULT_DISPLAY_BACKEND = "userspace"
DEFAULT_DISPLAY_MODEL = "waveshare-1.69"
DEFAULT_DISPLAY_VIEW = "auto"
DEFAULT_DISPLAY_STATUS_POLL_SECONDS = 1.0
DEFAULT_DISPLAY_BRIGHTNESS = 100
DEFAULT_CONFIG_CHECK_SECONDS = 2.0
DEFAULT_DISABLED_REFRESH_SECONDS = 5.0
DISPLAY_BUTTON_POLL_SECONDS = 0.05
DISPLAY_BUTTON_HEADLESS_POLL_SECONDS = 0.35
DISPLAY_BUTTON_BOUNCE_MS = 140
DISPLAY_BUTTON_LONG_PRESS_SECONDS = 0.65
SUPPORTED_DISPLAY_BACKENDS = {"userspace", "console"}
DISPLAY_BUTTON_VIEW_ORDER = ("boot", "wifi", "status")
SETTINGS_VIEW_KEY = "settings"
SETTINGS_MENU_ITEM_IDS = ("wifi", "brightness", "reboot", "poweroff", "exit")
DEFAULT_DISPLAY_BUTTON_PINS = {
    "next": "D16",
    "previous": "D6",
    "action": "D26",
}

DISPLAY_PROFILES = {
    "waveshare-1.69": {
        "label": 'Waveshare 1.69"',
        "width": 240,
        "height": 280,
        "x_offset": 0,
        "y_offset": 20,
        "rotation": 0,
        "baudrate": 64_000_000,
        "pins": {
            "cs": "CE0",
            "dc": "D25",
            "reset": "D27",
            "backlight": "D18",
        },
    },
    "waveshare-1.9": {
        "label": 'Waveshare 1.9"',
        "width": 170,
        "height": 320,
        "x_offset": 35,
        "y_offset": 0,
        "rotation": 0,
        "baudrate": 64_000_000,
        "pins": {
            "cs": "CE0",
            "dc": "D25",
            "reset": "D27",
            "backlight": "D18",
        },
    },
}

SUPPORTED_DISPLAY_VIEWS = {"auto", "boot", "wifi", "status"}
FBCP_BINARY_NAMES = {
    "waveshare-1.69": "fbcp-waveshare-1.69",
    "waveshare-1.9": "fbcp-waveshare-1.9",
}


def log(message: str) -> None:
    print(f"{LOG_PREFIX} {message}", flush=True)


def config_bool(value: object, default: bool = False) -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def normalize_device_name(value: object) -> str:
    return " ".join(str(value or "").split()).strip() or DEFAULT_DEVICE_NAME


def normalize_hotspot_ssid(value: object) -> str:
    return " ".join(str(value or "").split()).strip()[:32] or DEFAULT_ACCESS_POINT_SSID


def normalize_hotspot_password(value: object) -> str:
    password = str(value or "").strip()
    if 8 <= len(password) <= 63:
        return password
    return DEFAULT_ACCESS_POINT_PASSWORD


def normalize_display_model(value: object) -> str:
    safe_value = str(value or "").strip().lower()
    return safe_value if safe_value in DISPLAY_PROFILES else DEFAULT_DISPLAY_MODEL


def normalize_display_backend(value: object) -> str:
    safe_value = str(value or "").strip().lower()
    return safe_value if safe_value in SUPPORTED_DISPLAY_BACKENDS else DEFAULT_DISPLAY_BACKEND


def normalize_display_view(value: object) -> str:
    safe_value = str(value or "").strip().lower()
    return safe_value if safe_value in SUPPORTED_DISPLAY_VIEWS else DEFAULT_DISPLAY_VIEW


def normalize_button_pin(value: object) -> str:
    return str(value or "").strip()


def pin_name_to_bcm(value: object) -> int | None:
    text = str(value or "").strip().upper()
    if not text:
        return None
    if text.startswith("GPIO"):
        text = text[4:]
    elif text.startswith("D"):
        text = text[1:]
    try:
        pin = int(text, 10)
    except ValueError:
        return None
    return pin if 0 <= pin <= 27 else None


def normalize_display_button_pins(value: object) -> dict[str, str]:
    output = dict(DEFAULT_DISPLAY_BUTTON_PINS)
    if not isinstance(value, dict):
        return output
    for key in output:
        configured = normalize_button_pin(value.get(key))
        if configured:
            output[key] = configured
    return output


def normalize_display_status_poll_seconds(value: object) -> float:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return DEFAULT_DISPLAY_STATUS_POLL_SECONDS
    return min(30.0, max(0.1, seconds))


def normalize_display_brightness(value: object) -> int:
    try:
        brightness = int(value)
    except (TypeError, ValueError):
        return DEFAULT_DISPLAY_BRIGHTNESS
    return min(100, max(5, brightness))


def legacy_refresh_fps_to_poll_seconds(value: object) -> float | None:
    try:
        fps = float(value)
    except (TypeError, ValueError):
        return None
    if fps <= 0:
        return None
    return 1.0 / max(1.0, fps)


def cycle_display_view(current_view: str) -> str:
    safe_current = str(current_view or "").strip().lower()
    if safe_current not in DISPLAY_BUTTON_VIEW_ORDER:
        return DISPLAY_BUTTON_VIEW_ORDER[0]
    current_index = DISPLAY_BUTTON_VIEW_ORDER.index(safe_current)
    return DISPLAY_BUTTON_VIEW_ORDER[(current_index + 1) % len(DISPLAY_BUTTON_VIEW_ORDER)]


def previous_display_view(current_view: str) -> str:
    safe_current = str(current_view or "").strip().lower()
    if safe_current not in DISPLAY_BUTTON_VIEW_ORDER:
        return DISPLAY_BUTTON_VIEW_ORDER[-1]
    current_index = DISPLAY_BUTTON_VIEW_ORDER.index(safe_current)
    return DISPLAY_BUTTON_VIEW_ORDER[(current_index - 1) % len(DISPLAY_BUTTON_VIEW_ORDER)]


def sanitize_mdns_host(value: object) -> str:
    output = []
    previous_dash = False
    for character in str(value or ""):
        if character.isalnum():
            output.append(character.lower())
            previous_dash = False
        elif character in {" ", "-", "_", "."} and output and not previous_dash:
            output.append("-")
            previous_dash = True
    return "".join(output)[:63].rstrip("-")


def derived_mdns_host(device_name: str) -> str:
    return sanitize_mdns_host(device_name) or DEFAULT_MDNS_HOST


def derived_access_point_connection_name(device_name: str) -> str:
    return f"{normalize_device_name(device_name) or DEFAULT_DEVICE_NAME} Hotspot"


def merge_config_values(base: object, override: object) -> object:
    if isinstance(base, dict) and isinstance(override, dict):
        merged = dict(base)
        for key, value in override.items():
            if key in merged:
                merged[key] = merge_config_values(merged[key], value)
            else:
                merged[key] = value
        return merged
    return override


def read_runtime_config_file(path: Path) -> dict[str, object]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def runtime_storage_root() -> Path:
    root_value = os.environ.get("NOMADSCREEN_STORAGE_ROOT", "").strip()
    if root_value:
        return Path(root_value).expanduser()
    return DEFAULT_STORAGE_ROOT


def runtime_user_config_candidates(storage_root: Path) -> list[Path]:
    return [
        storage_root / DEFAULT_RUNTIME_USER_CONFIG_NAME,
        storage_root / LEGACY_RUNTIME_USER_CONFIG_NAME,
    ]


def first_existing_path(candidates: list[Path]) -> Path | None:
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def fsync_directory(path: Path) -> None:
    if os.name != "posix":
        return
    flags = getattr(os, "O_RDONLY", 0)
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    try:
        descriptor = os.open(str(path), flags)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def atomic_write_text(path: Path, content: str, encoding: str = "utf-8") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "w", encoding=encoding, newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        fsync_directory(path.parent)
    except Exception:
        try:
            temp_path.unlink()
        except OSError:
            pass
        raise


def runtime_config_values(storage_root: Path) -> dict[str, object]:
    base_config = {}
    user_config = {}
    for path in (
        storage_root / DEFAULT_RUNTIME_CONFIG_NAME,
        storage_root / LEGACY_RUNTIME_CONFIG_NAME,
    ):
        if path.exists():
            base_config = read_runtime_config_file(path)
            break
    for path in (
        storage_root / DEFAULT_RUNTIME_USER_CONFIG_NAME,
        storage_root / LEGACY_RUNTIME_USER_CONFIG_NAME,
    ):
        if path.exists():
            user_config = read_runtime_config_file(path)
            break
    return merge_config_values(base_config, user_config) if user_config else base_config


def load_screen_settings() -> dict[str, object]:
    storage_root = runtime_storage_root()
    raw_config = runtime_config_values(storage_root)
    raw_display = raw_config.get("display") if isinstance(raw_config.get("display"), dict) else {}
    raw_display_buttons = raw_display.get("buttons") if isinstance(raw_display.get("buttons"), dict) else {}
    raw_wifi = raw_config.get("wifi") if isinstance(raw_config.get("wifi"), dict) else {}
    device_name = normalize_device_name(raw_config.get("deviceName") or raw_config.get("serverName"))
    hotspot_ssid = normalize_hotspot_ssid(
        raw_config.get("hotspotSsid")
        or raw_config.get("accessPointSsid")
        or raw_wifi.get("ssid")
    )
    wifi_password = normalize_hotspot_password(
        raw_config.get("wifiPassword")
        or raw_wifi.get("password")
    )
    bind_address = os.environ.get("NOMADSCREEN_BIND", "").strip() or str(raw_config.get("bindAddress") or DEFAULT_BIND_ADDRESS)
    http_port = raw_config.get("httpPort") or raw_config.get("port") or DEFAULT_HTTP_PORT
    try:
        http_port = max(int(http_port), 1)
    except (TypeError, ValueError):
        http_port = DEFAULT_HTTP_PORT
    display_enabled = config_bool(
        os.environ.get("NOMADSCREEN_DISPLAY_ENABLED"),
        config_bool(raw_config.get("displayEnabled", raw_display.get("enabled")), DEFAULT_DISPLAY_ENABLED),
    )
    display_backend = normalize_display_backend(
        os.environ.get("NOMADSCREEN_DISPLAY_BACKEND") or raw_config.get("displayBackend") or raw_display.get("backend")
    )
    display_model = normalize_display_model(
        os.environ.get("NOMADSCREEN_DISPLAY_MODEL") or raw_config.get("displayModel") or raw_display.get("model")
    )
    display_view = normalize_display_view(
        os.environ.get("NOMADSCREEN_DISPLAY_VIEW") or raw_config.get("displayView") or raw_display.get("view")
    )
    display_status_poll_seconds = normalize_display_status_poll_seconds(
        os.environ.get("NOMADSCREEN_DISPLAY_STATUS_POLL_SECONDS")
        or raw_config.get("displayStatusPollSeconds")
        or raw_display.get("statusPollSeconds")
        or legacy_refresh_fps_to_poll_seconds(raw_config.get("displayRefreshFps") or raw_display.get("refreshFps"))
    )
    display_brightness = normalize_display_brightness(
        os.environ.get("NOMADSCREEN_DISPLAY_BRIGHTNESS")
        or raw_config.get("displayBrightness")
        or raw_display.get("brightness")
    )
    display_button_pins = normalize_display_button_pins(
        raw_config.get("displayButtons") if isinstance(raw_config.get("displayButtons"), dict) else raw_display_buttons
    )
    fallback_ap_enabled = config_bool(
        raw_config.get("fallbackAccessPointEnabled", raw_config.get("accessPointEnabled")),
        True,
    )
    wifi_interface = str(raw_config.get("wifiInterface") or raw_wifi.get("interface") or "wlan0").strip() or "wlan0"
    access_point_connection_name = str(
        raw_config.get("fallbackAccessPointConnectionName") or derived_access_point_connection_name(device_name)
    ).strip() or derived_access_point_connection_name(DEFAULT_DEVICE_NAME)
    mdns_enabled = config_bool(os.environ.get("NOMADSCREEN_MDNS"), config_bool(raw_config.get("mdnsEnabled"), False))
    mdns_host = sanitize_mdns_host(raw_config.get("mdnsHost")) or derived_mdns_host(device_name)
    media_root = (
        os.environ.get("NOMADSCREEN_MEDIA_ROOT", "").strip()
        or str(raw_config.get("mediaPath") or raw_config.get("mediaDirectory") or "")
    )
    if media_root:
        media_directory = Path(media_root).expanduser()
        if not media_directory.is_absolute():
            media_directory = storage_root / media_directory
    else:
        media_directory = storage_root / "media"
    return {
        "storage_root": storage_root,
        "device_name": device_name,
        "hotspot_ssid": hotspot_ssid,
        "wifi_password": wifi_password,
        "bind_address": bind_address,
        "http_port": int(http_port),
        "mdns_enabled": mdns_enabled,
        "mdns_host": mdns_host,
        "media_directory": media_directory.expanduser(),
        "display_enabled": display_enabled,
        "display_backend": display_backend,
        "display_model": display_model,
        "display_view": display_view,
        "display_status_poll_seconds": display_status_poll_seconds,
        "display_brightness": display_brightness,
        "display_button_pins": display_button_pins,
        "fallback_ap_enabled": fallback_ap_enabled,
        "wifi_interface": wifi_interface,
        "access_point_connection_name": access_point_connection_name,
    }


def save_runtime_overrides(
    storage_root: Path,
    *,
    fallback_ap_enabled: bool | None = None,
    display_brightness: int | None = None,
) -> None:
    user_config_candidates = runtime_user_config_candidates(storage_root)
    user_config_path = first_existing_path(user_config_candidates) or user_config_candidates[0]
    raw_config = read_runtime_config_file(user_config_path) if user_config_path.exists() else {}

    if fallback_ap_enabled is not None:
        raw_config["fallbackAccessPointEnabled"] = bool(fallback_ap_enabled)

    if display_brightness is not None:
        safe_display_brightness = normalize_display_brightness(display_brightness)
        raw_config["displayBrightness"] = safe_display_brightness
        display_block = dict(raw_config.get("display") or {}) if isinstance(raw_config.get("display"), dict) else {}
        display_block["brightness"] = safe_display_brightness
        raw_config["display"] = display_block

    atomic_write_text(user_config_path, json.dumps(raw_config, indent=2, ensure_ascii=False) + "\n")


def best_local_ip(bind_address: str) -> str:
    if bind_address and bind_address not in {"0.0.0.0", "::"}:
        return bind_address
    for target in ("192.0.2.1", "8.8.8.8"):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.connect((target, 80))
                candidate = sock.getsockname()[0]
                if candidate and candidate != "127.0.0.1":
                    return candidate
        except OSError:
            continue
    try:
        candidate = socket.gethostbyname(socket.gethostname())
        if candidate:
            return candidate
    except OSError:
        pass
    return "127.0.0.1"


def compose_url(host: str, port: int, suffix: str = "/app") -> str:
    clean_suffix = suffix if suffix.startswith("/") else f"/{suffix}"
    if int(port) == 80:
        return f"http://{host}{clean_suffix}"
    return f"http://{host}:{port}{clean_suffix}"


def build_wifi_qr_payload(ssid: str, password: str, auth_type: str = "WPA") -> str:
    def escape(value: str) -> str:
        return re.sub(r"([\\;,:])", r"\\\1", str(value or ""))

    safe_auth = str(auth_type or "WPA").strip().upper() or "WPA"
    return f"WIFI:T:{safe_auth};S:{escape(ssid)};P:{escape(password)};;"


def network_mode_label(status: dict[str, object] | None) -> str:
    mode = str((status or {}).get("networkMode") or "").strip().lower()
    if mode == "hotspot":
        return "Fallback Hotspot"
    if mode == "client":
        return "Known Wi-Fi"
    if mode == "offline":
        return "Offline"
    return "Starting"


def active_network_name(status: dict[str, object] | None, settings: dict[str, object]) -> str:
    if not status:
        return ""
    return str(status.get("networkName") or status.get("hotspotSsid") or settings["hotspot_ssid"] or "").strip()


def preferred_app_url(settings: dict[str, object], status: dict[str, object] | None) -> str:
    if status:
        app_url = str(status.get("appUrl") or "").strip()
        if app_url:
            return app_url
    host = f"{settings['mdns_host']}.local" if settings["mdns_enabled"] else best_local_ip(str(settings["bind_address"]))
    return compose_url(host, int(settings["http_port"]), "/app")


def fetch_status(settings: dict[str, object]) -> dict[str, object] | None:
    try:
        response = requests.get(
            compose_url("127.0.0.1", int(settings["http_port"]), "/api/status"),
            timeout=1.5,
        )
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else None
    except (requests.RequestException, ValueError):
        return None


def run_command(args: list[str], timeout_seconds: float = 12.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
    )


def nmcli_command(*args: str, timeout_seconds: float = 12.0) -> subprocess.CompletedProcess[str]:
    return run_command(["nmcli", *args], timeout_seconds=timeout_seconds)


def ensure_hotspot_profile(settings: dict[str, object]) -> None:
    connection_name = str(settings.get("access_point_connection_name") or "").strip()
    wifi_interface = str(settings.get("wifi_interface") or "wlan0").strip() or "wlan0"
    ssid = str(settings.get("hotspot_ssid") or DEFAULT_ACCESS_POINT_SSID)
    password = normalize_hotspot_password(settings.get("wifi_password"))

    existing = nmcli_command("-t", "-g", "NAME", "connection", "show", timeout_seconds=4.0)
    if connection_name not in existing.stdout.splitlines():
        created = nmcli_command(
            "connection",
            "add",
            "type",
            "wifi",
            "ifname",
            wifi_interface,
            "con-name",
            connection_name,
            "ssid",
            ssid,
            "autoconnect",
            "no",
        )
        if created.returncode != 0:
            stderr = created.stderr.strip() or created.stdout.strip() or "Could not create the hotspot profile."
            raise RuntimeError(stderr)

    updated = nmcli_command(
        "connection",
        "modify",
        connection_name,
        "connection.interface-name",
        wifi_interface,
        "connection.autoconnect",
        "no",
        "wifi.mode",
        "ap",
        "wifi.band",
        "bg",
        "wifi.ssid",
        ssid,
        "wifi-sec.key-mgmt",
        "wpa-psk",
        "wifi-sec.psk",
        password,
        "ipv4.addresses",
        "10.0.0.1/24",
        "ipv4.method",
        "shared",
        "ipv6.method",
        "ignore",
    )
    if updated.returncode != 0:
        stderr = updated.stderr.strip() or updated.stdout.strip() or "Could not update the hotspot profile."
        raise RuntimeError(stderr)


def set_backcountry_wifi_enabled(settings: dict[str, object], enabled: bool) -> str:
    wifi_interface = str(settings.get("wifi_interface") or "wlan0").strip() or "wlan0"
    connection_name = str(settings.get("access_point_connection_name") or "").strip()

    radio_result = nmcli_command("radio", "wifi", "on", timeout_seconds=4.0)
    if radio_result.returncode != 0:
        stderr = radio_result.stderr.strip() or radio_result.stdout.strip() or "Could not enable Wi-Fi radio."
        raise RuntimeError(stderr)

    nmcli_command("device", "set", wifi_interface, "managed", "yes", timeout_seconds=4.0)

    if enabled:
        ensure_hotspot_profile(settings)
        result = nmcli_command("connection", "up", connection_name, "ifname", wifi_interface)
        if result.returncode != 0:
            stderr = result.stderr.strip() or result.stdout.strip() or "Could not start the Backcountry Wi-Fi hotspot."
            raise RuntimeError(stderr)
        return "Backcountry Wi-Fi turned on."

    if connection_name:
        nmcli_command("connection", "down", connection_name, timeout_seconds=6.0)
    nmcli_command("device", "up", wifi_interface, timeout_seconds=8.0)
    return "Backcountry Wi-Fi turned off."


def perform_power_action(action: str) -> None:
    if action not in {"reboot", "poweroff"}:
        raise ValueError(f"Unsupported power action: {action}")

    if hasattr(os, "geteuid") and os.geteuid() != 0:
        raise RuntimeError(
            "Power controls need the screen service to run as root. Rerun update.sh and restart the screen service."
        )

    command_sets = {
        "reboot": [
            ["systemctl", "start", "reboot.target"],
            ["systemctl", "reboot"],
            ["shutdown", "-r", "now"],
            ["reboot"],
        ],
        "poweroff": [
            ["systemctl", "start", "poweroff.target"],
            ["systemctl", "poweroff"],
            ["shutdown", "-h", "now"],
            ["poweroff"],
        ],
    }

    failures: list[str] = []
    for command in command_sets[action]:
        try:
            result = run_command(command, timeout_seconds=8.0)
        except (OSError, subprocess.SubprocessError) as error:
            failures.append(f"{' '.join(command)}: {error}")
            continue
        if result.returncode == 0:
            return
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        failures.append(f"{' '.join(command)}: {detail}")

    raise RuntimeError(
        f"Could not {action} the Raspberry Pi. " + " | ".join(failures[:3])
    )


def next_display_brightness(current_value: object) -> int:
    current = normalize_display_brightness(current_value)
    next_value = current + 10
    return 10 if next_value > 100 else next_value


@dataclass
class ScreenUiState:
    manual_view_override: str | None = None
    settings_selected_index: int = 0
    settings_return_view: str | None = None
    pending_power_action: str = ""
    notice_text: str = ""
    notice_tone: str = "info"
    notice_until: float = 0.0

    def active_notice(self) -> str:
        if self.notice_text and time.monotonic() < float(self.notice_until):
            return self.notice_text
        return ""


@dataclass
class InteractionResult:
    should_redraw: bool = False
    toggle_backlight: bool = False
    reload_settings: bool = False
    refresh_status: bool = False
    system_action: str | None = None


def set_ui_notice(ui_state: ScreenUiState, message: str, tone: str = "info", seconds: float = 4.0) -> None:
    ui_state.notice_text = str(message or "").strip()
    ui_state.notice_tone = tone
    ui_state.notice_until = time.monotonic() + max(0.5, float(seconds))


def fit_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = []
    if bold:
        candidates.extend(
            [
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
            ]
        )
    candidates.extend(
        [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
        ]
    )
    for candidate in candidates:
        path = Path(candidate)
        if path.exists():
            try:
                return ImageFont.truetype(str(path), size)
            except OSError:
                continue
    return ImageFont.load_default()


def wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, width: int) -> list[str]:
    words = str(text or "").split()
    if not words:
        return []
    lines = []
    current = words[0]
    for word in words[1:]:
        trial = f"{current} {word}"
        if draw.textbbox((0, 0), trial, font=font)[2] <= width:
            current = trial
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def truncate_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, width: int) -> str:
    content = str(text or "").strip()
    if not content:
        return ""
    if draw.textbbox((0, 0), content, font=font)[2] <= width:
        return content
    ellipsis = "..."
    shortened = content
    while shortened:
        shortened = shortened[:-1].rstrip()
        candidate = f"{shortened}{ellipsis}"
        if draw.textbbox((0, 0), candidate, font=font)[2] <= width:
            return candidate
    return ellipsis


def create_canvas(profile: dict[str, object]) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    width = int(profile["width"])
    height = int(profile["height"])
    image = Image.new("RGB", (width, height), "#101510")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, width, height), fill="#141913")
    draw.rectangle((0, 0, width, int(height * 0.28)), fill="#273021")
    return image, draw


def draw_multiline_block(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    fill: str,
    x: int,
    y: int,
    max_width: int,
    line_gap: int,
    max_lines: int | None = None,
) -> int:
    lines = wrap_text(draw, text, font, max_width)
    if max_lines is not None and max_lines > 0 and len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = truncate_text(draw, lines[-1], font, max_width)
    for line in lines:
        draw.text((x, y), line, font=font, fill=fill)
        y += draw.textbbox((0, 0), line, font=font)[3] + line_gap
    return y


def render_boot_screen(profile: dict[str, object], settings: dict[str, object], status: dict[str, object] | None) -> Image.Image:
    image, draw = create_canvas(profile)
    width = int(profile["width"])
    height = int(profile["height"])
    compact = width <= 240 and height <= 280
    pad = max(12, width // 15)
    title_font = fit_font(16 if compact else max(18, width // 10), bold=True)
    body_font = fit_font(9 if compact else max(11, width // 18))
    label_font = fit_font(8 if compact else max(10, width // 20), bold=True)

    draw.rounded_rectangle((pad, pad, width - pad, pad + 28), radius=14, fill="#36452f")
    draw.text((pad + 10, pad + 6), "BOOT", font=label_font, fill="#f3eddf")

    y = pad + 42
    device_name = truncate_text(draw, str(settings["device_name"]), title_font, width - (pad * 2))
    draw.text((pad, y), device_name, font=title_font, fill="#f3eddf")
    y += draw.textbbox((0, 0), device_name, font=title_font)[3] + (5 if compact else 8)
    y = draw_multiline_block(
        draw,
        "Preparing the portable media server and waiting for live status.",
        body_font,
        "#d2cab9",
        pad,
        y,
        width - (pad * 2),
        2 if compact else 3,
        2 if compact else None,
    )
    y += 4 if compact else 6

    rows = [
        ("Display", str(profile["label"])),
        ("Network", network_mode_label(status)),
        ("Storage", "Ready" if Path(settings["media_directory"]).exists() else "Waiting"),
        ("Web UI", "Online" if status else "Starting"),
    ]
    box_height = 26 if compact else max(28, height // 11)
    for label, value in rows:
        draw.rounded_rectangle((pad, y, width - pad, y + box_height), radius=12, outline="#52634a", width=1)
        draw.text((pad + 8, y + 5), label.upper(), font=label_font, fill="#9bb08e")
        draw.text(
            (pad + 8, y + box_height // 2 + (3 if compact else 0)),
            truncate_text(draw, value, body_font, width - (pad * 2) - 16),
            font=body_font,
            fill="#f3eddf",
            anchor="lm",
        )
        y += box_height + (5 if compact else 8)

    footer = preferred_app_url(settings, status)
    draw_multiline_block(
        draw,
        footer,
        body_font,
        "#c6a56b",
        pad,
        height - (28 if compact else 42),
        width - (pad * 2),
        2,
        1 if compact else 2,
    )
    return image


def render_wifi_screen(profile: dict[str, object], settings: dict[str, object], status: dict[str, object] | None) -> Image.Image:
    image, draw = create_canvas(profile)
    width = int(profile["width"])
    height = int(profile["height"])
    compact = width <= 240 and height <= 280
    pad = max(10, width // 16)
    title_font = fit_font(16 if compact else max(18, width // 11), bold=True)
    body_font = fit_font(9 if compact else max(10, width // 19))
    label_font = fit_font(8 if compact else max(9, width // 22), bold=True)

    draw.rounded_rectangle((pad, pad, width - pad, pad + 28), radius=14, fill="#36452f")
    draw.text((pad + 10, pad + 6), "WI-FI QR", font=label_font, fill="#f3eddf")

    y = pad + 40
    join_title = "Join Hotspot" if compact else "Join The Hotspot"
    draw.text((pad, y), join_title, font=title_font, fill="#f3eddf")
    y += draw.textbbox((0, 0), join_title, font=title_font)[3] + (4 if compact else 6)

    qr_size = min(width - (pad * 2), max(84 if compact else 96, int(height * (0.34 if compact else 0.42))))
    qr = qrcode.QRCode(border=1, box_size=8)
    qr.add_data(build_wifi_qr_payload(str(settings["hotspot_ssid"]), str(settings["wifi_password"])))
    qr.make(fit=True)
    qr_image = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    qr_image.thumbnail((qr_size, qr_size), Image.Resampling.NEAREST)
    qr_x = (width - qr_image.width) // 2
    image.paste(qr_image, (qr_x, y))
    y += qr_image.height + 10

    rows = [
        ("SSID", str(settings["hotspot_ssid"])),
        ("Pass", str(settings["wifi_password"])),
        ("Open", preferred_app_url(settings, status)),
    ]
    for label, value in rows:
        draw.text((pad, y), label.upper(), font=label_font, fill="#9bb08e")
        y += draw.textbbox((0, 0), label, font=label_font)[3] + 2
        y = draw_multiline_block(draw, value, body_font, "#f3eddf", pad, y, width - (pad * 2), 2, 1 if compact else 2)
        y += 3 if compact else 4

    return image


def render_status_screen(profile: dict[str, object], settings: dict[str, object], status: dict[str, object] | None) -> Image.Image:
    image, draw = create_canvas(profile)
    width = int(profile["width"])
    height = int(profile["height"])
    compact = width <= 240 and height <= 280
    pad = max(10, width // 16)
    title_font = fit_font(15 if compact else max(18, width // 11), bold=True)
    body_font = fit_font(9 if compact else max(10, width // 19))
    label_font = fit_font(8 if compact else max(9, width // 22), bold=True)
    metric_font = fit_font(13 if compact else max(16, width // 10), bold=True)

    draw.rounded_rectangle((pad, pad, width - pad, pad + 28), radius=14, fill="#36452f")
    draw.text((pad + 10, pad + 6), "STATUS", font=label_font, fill="#f3eddf")

    network_name = active_network_name(status, settings) or str(settings["hotspot_ssid"])
    title_y = pad + 38
    draw.text((pad, title_y), truncate_text(draw, network_mode_label(status), title_font, width - (pad * 2)), font=title_font, fill="#f3eddf")
    name_y = title_y + draw.textbbox((0, 0), network_mode_label(status), font=title_font)[3] + (4 if compact else 8)
    name_end_y = draw_multiline_block(
        draw,
        network_name,
        body_font,
        "#d2cab9",
        pad,
        name_y,
        width - (pad * 2),
        2,
        2 if compact else 3,
    )

    clients = str((status or {}).get("clients") or 0)
    library_count = str((status or {}).get("libraryCount") or 0)
    streams = f"{(status or {}).get('activeStreams') or 0}/{(status or {}).get('maxStreams') or 0}"
    storage = "Ready" if (status or {}).get("sdMounted") else "Check"
    metrics = [
        ("Clients", clients),
        ("Media", library_count),
        ("Streams", streams),
        ("Storage", storage),
    ]

    metric_top = max(name_end_y + (5 if compact else 10), pad + (84 if compact else 102))
    box_gap = 6 if compact else 8
    box_width = (width - (pad * 2) - box_gap) // 2
    box_height = 38 if compact else max(48, height // 7)
    for index, (label, value) in enumerate(metrics):
        col = index % 2
        row = index // 2
        x = pad + (col * (box_width + box_gap))
        y = metric_top + (row * (box_height + box_gap))
        draw.rounded_rectangle((x, y, x + box_width, y + box_height), radius=14, outline="#52634a", width=1)
        draw.text((x + 8, y + 6), label.upper(), font=label_font, fill="#9bb08e")
        metric_value = truncate_text(draw, value, metric_font, box_width - 16)
        draw.text((x + 8, y + box_height - 7), metric_value, font=metric_font, fill="#f3eddf", anchor="ls")

    footer_y = metric_top + (2 * (box_height + box_gap)) + (2 if compact else 4)
    footer_lines = [
        ("Open", preferred_app_url(settings, status)),
        ("IP", str((status or {}).get("ip") or best_local_ip(str(settings["bind_address"])))),
    ]
    for label, value in footer_lines:
        draw.text((pad, footer_y), label.upper(), font=label_font, fill="#9bb08e")
        footer_y += draw.textbbox((0, 0), label, font=label_font)[3] + 2
        footer_y = draw_multiline_block(
            draw,
            value,
            body_font,
            "#f3eddf",
            pad,
            footer_y,
            width - (pad * 2),
            2,
            1 if compact else 2,
        )
        footer_y += 3 if compact else 4
    return image


def settings_menu_rows(settings: dict[str, object], ui_state: ScreenUiState) -> list[tuple[str, str]]:
    wifi_enabled = config_bool(settings.get("fallback_ap_enabled"), True)
    brightness = normalize_display_brightness(settings.get("display_brightness"))
    pending = str(ui_state.pending_power_action or "")
    return [
        ("Backcountry Wi-Fi", "On" if wifi_enabled else "Off"),
        ("Backlight", f"{brightness}%"),
        ("Reboot Pi", "Press again" if pending == "reboot" else ""),
        ("Power Down", "Press again" if pending == "poweroff" else ""),
        ("Exit Settings", ""),
    ]


def render_settings_screen(
    profile: dict[str, object],
    settings: dict[str, object],
    status: dict[str, object] | None,
    ui_state: ScreenUiState,
) -> Image.Image:
    image, draw = create_canvas(profile)
    width = int(profile["width"])
    height = int(profile["height"])
    compact = width <= 240 and height <= 280
    pad = max(10, width // 16)
    title_font = fit_font(15 if compact else max(18, width // 11), bold=True)
    body_font = fit_font(9 if compact else max(10, width // 19))
    label_font = fit_font(8 if compact else max(9, width // 22), bold=True)

    draw.rounded_rectangle((pad, pad, width - pad, pad + 28), radius=14, fill="#36452f")
    draw.text((pad + 10, pad + 6), "SETTINGS", font=label_font, fill="#f3eddf")

    network_mode = network_mode_label(status)
    subtitle = f"{network_mode} | {str(settings.get('device_name') or DEFAULT_DEVICE_NAME)}"
    draw.text((pad, pad + 38), truncate_text(draw, subtitle, body_font, width - (pad * 2)), font=body_font, fill="#d2cab9")

    row_y = pad + (52 if compact else 64)
    row_height = 30 if compact else 40
    row_gap = 4 if compact else 7
    rows = settings_menu_rows(settings, ui_state)
    selected_index = max(0, min(int(ui_state.settings_selected_index), len(rows) - 1))
    for index, (label, value) in enumerate(rows):
        is_selected = index == selected_index
        fill = "#c6a56b" if is_selected else "#1d241c"
        outline = "#f3eddf" if is_selected else "#52634a"
        label_fill = "#141913" if is_selected else "#f3eddf"
        value_fill = "#273021" if is_selected else "#c6d2bf"
        draw.rounded_rectangle((pad, row_y, width - pad, row_y + row_height), radius=12, fill=fill, outline=outline, width=1)
        draw.text((pad + 8, row_y + 7), truncate_text(draw, label, label_font, width - (pad * 2) - 16), font=label_font, fill=label_fill)
        if value:
            draw.text(
                (width - pad - 8, row_y + row_height - 9),
                truncate_text(draw, value, body_font, width - (pad * 2) - 16),
                font=body_font,
                fill=value_fill,
                anchor="rs",
            )
        row_y += row_height + row_gap

    notice = ui_state.active_notice()
    notice_color = "#ffb4a9" if ui_state.notice_tone == "error" else "#d7e8b5"
    footer_lines = [
        notice or "Up/Down move | Select change | Hold Up exit",
        "Hold Action toggles the backlight on or off.",
    ]
    footer_y = height - (30 if compact else 52)
    for line in footer_lines:
        footer_y = draw_multiline_block(
            draw,
            line,
            body_font,
            notice_color if line == footer_lines[0] else "#9bb08e",
            pad,
            footer_y,
            width - (pad * 2),
            2,
            1 if compact else 2,
        )
        footer_y += 2
    return image


def render_self_test_screen(profile: dict[str, object], model_key: str) -> Image.Image:
    image = Image.new("RGB", (int(profile["width"]), int(profile["height"])), "#000000")
    draw = ImageDraw.Draw(image)
    width = int(profile["width"])
    height = int(profile["height"])
    pad = max(10, width // 18)
    title_font = fit_font(max(16, width // 11), bold=True)
    body_font = fit_font(max(10, width // 20))
    label_font = fit_font(max(9, width // 22), bold=True)

    bands = [
        "#ff3b30",
        "#ff9500",
        "#ffd60a",
        "#34c759",
        "#0a84ff",
        "#bf5af2",
    ]
    band_height = max(12, int(height * 0.07))
    for index, color in enumerate(bands):
        top = index * band_height
        draw.rectangle((0, top, width, min(height, top + band_height)), fill=color)

    panel_top = (len(bands) * band_height) + 6
    draw.rounded_rectangle(
        (pad, panel_top, width - pad, height - pad),
        radius=16,
        fill="#101510",
        outline="#d7c9a7",
        width=2,
    )
    text_y = panel_top + pad
    draw.text((pad * 2, text_y), "SCREEN TEST", font=title_font, fill="#f3eddf")
    text_y += draw.textbbox((0, 0), "SCREEN TEST", font=title_font)[3] + 8
    draw.text((pad * 2, text_y), f"Model: {model_key}", font=body_font, fill="#f3eddf")
    text_y += draw.textbbox((0, 0), f"Model: {model_key}", font=body_font)[3] + 5
    draw.text((pad * 2, text_y), f"Size: {width}x{height}", font=body_font, fill="#f3eddf")
    text_y += draw.textbbox((0, 0), f"Size: {width}x{height}", font=body_font)[3] + 8
    draw.text((pad * 2, text_y), "If you can read this,", font=label_font, fill="#c6a56b")
    text_y += draw.textbbox((0, 0), "If you can read this,", font=label_font)[3] + 3
    draw.text((pad * 2, text_y), "SPI + draw path works.", font=body_font, fill="#ffffff")
    text_y += draw.textbbox((0, 0), "SPI + draw path works.", font=body_font)[3] + 10

    box_size = max(18, min(width // 5, 40))
    box_gap = max(6, width // 30)
    swatches = ["#ffffff", "#000000", "#34c759", "#0a84ff"]
    swatch_y = min(height - pad - box_size, text_y)
    swatch_x = pad * 2
    for color in swatches:
        outline = "#d7c9a7" if color == "#000000" else None
        draw.rounded_rectangle(
            (swatch_x, swatch_y, swatch_x + box_size, swatch_y + box_size),
            radius=6,
            fill=color,
            outline=outline,
            width=1 if outline else 0,
        )
        swatch_x += box_size + box_gap

    return image


def console_binary_path(model_key: str) -> Path:
    return APP_ROOT / "bin" / FBCP_BINARY_NAMES[normalize_display_model(model_key)]


def stop_console_process(process: subprocess.Popen[bytes] | None, reason: str = "") -> None:
    if process is None:
        return
    if process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
    except OSError:
        pass
    if reason:
        log(reason)


class ButtonInput:
    def __init__(self, pin_name: str, wake_event: threading.Event | None = None):
        self.pin_name = str(pin_name or "").strip()
        self._button = None
        self._pressed_latch = False
        self._edge_event = None
        self._callback_cleanup = None
        self._wake_event = wake_event

        try:
            import board
            import digitalio
        except ModuleNotFoundError:
            return

        try:
            button = digitalio.DigitalInOut(getattr(board, self.pin_name))
            button.switch_to_input(pull=digitalio.Pull.UP)
            self._button = button
            log(f"Listening for display button presses on GPIO pin {self.pin_name}.")
            self._initialize_edge_detection()
        except AttributeError:
            log(f"Display button pin {self.pin_name} is not available on this board definition.")
        except Exception as error:
            log(f"Could not initialize display button {self.pin_name}: {error}")

    @property
    def ready(self) -> bool:
        return self._button is not None

    @property
    def edge_event(self) -> threading.Event | None:
        return self._edge_event

    def is_pressed(self) -> bool:
        if self._button is None:
            return False
        try:
            return not bool(self._button.value)
        except Exception:
            return False

    def _initialize_edge_detection(self) -> None:
        bcm_pin = pin_name_to_bcm(self.pin_name)
        if bcm_pin is None:
            return
        try:
            from RPi import GPIO  # type: ignore
        except ModuleNotFoundError:
            return
        try:
            GPIO.setwarnings(False)
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(bcm_pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
            edge_event = threading.Event()

            def on_press(channel: int) -> None:
                edge_event.set()
                if self._wake_event is not None:
                    self._wake_event.set()

            GPIO.add_event_detect(bcm_pin, GPIO.FALLING, callback=on_press, bouncetime=DISPLAY_BUTTON_BOUNCE_MS)
            self._edge_event = edge_event
            self._callback_cleanup = lambda: GPIO.remove_event_detect(bcm_pin)
        except Exception as error:
            log(f"Could not enable edge detection for display button {self.pin_name}: {error}")
            self._edge_event = None
            self._callback_cleanup = None

    def poll_pressed(self) -> bool:
        if self._button is None:
            return False
        is_pressed = self.is_pressed()
        if is_pressed and not self._pressed_latch:
            self._pressed_latch = True
            return True
        if not is_pressed:
            self._pressed_latch = False
        return False

    def consume_edge(self) -> bool:
        if self._edge_event is None:
            return False
        if self._edge_event.is_set():
            self._edge_event.clear()
            return True
        return False


class DisplayButtonManager:
    def __init__(self, pin_map: dict[str, str] | None = None):
        pins = normalize_display_button_pins(pin_map or {})
        self._wake_event = threading.Event()
        self.next_button = ButtonInput(pins.get("next") or "", wake_event=self._wake_event)
        self.previous_button = ButtonInput(pins.get("previous") or "", wake_event=self._wake_event)
        self.action_button = ButtonInput(pins.get("action") or "", wake_event=self._wake_event)
        self._signature = self.signature_for(pins)
        self._buttons = (
            ("next", self.next_button),
            ("previous", self.previous_button),
            ("action", self.action_button),
        )

    @staticmethod
    def signature_for(pin_map: dict[str, str] | None = None) -> str:
        pins = normalize_display_button_pins(pin_map or {})
        return json.dumps(pins, sort_keys=True)

    def matches(self, pin_map: dict[str, str] | None = None) -> bool:
        return self._signature == self.signature_for(pin_map)

    def _button_from_action(self, action: str) -> ButtonInput | None:
        for candidate_action, button in self._buttons:
            if candidate_action == action:
                return button
        return None

    def _wait_for_release_or_long(self, button: ButtonInput, timeout_seconds: float) -> str:
        deadline = time.monotonic() + max(0.0, float(timeout_seconds))
        while time.monotonic() < deadline:
            if not button.is_pressed():
                return "released"
            time.sleep(DISPLAY_BUTTON_POLL_SECONDS)
        return "long"

    def _classify_gesture(self, action: str) -> str:
        button = self._button_from_action(action)
        if button is None:
            return action
        result = self._wait_for_release_or_long(button, DISPLAY_BUTTON_LONG_PRESS_SECONDS)
        if result == "long":
            return f"{action}:long"
        return action

    def wait_for_action(self, timeout_seconds: float | None, poll_seconds: float = DISPLAY_BUTTON_POLL_SECONDS) -> str | None:
        timeout = None if timeout_seconds is None else max(0.0, float(timeout_seconds))
        deadline = None if timeout is None else time.monotonic() + timeout
        safe_poll_seconds = max(0.01, float(poll_seconds))
        while True:
            for action, button in self._buttons:
                if button.consume_edge() or button.poll_pressed():
                    return self._classify_gesture(action)
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                poll_sleep = min(safe_poll_seconds, remaining)
            else:
                poll_sleep = safe_poll_seconds
            edge_events = [button.edge_event for _, button in self._buttons if button.edge_event is not None]
            if edge_events:
                self._wake_event.wait(poll_sleep)
                self._wake_event.clear()
            else:
                time.sleep(poll_sleep)


class PhysicalDisplay:
    def __init__(self, model_key: str):
        self.model_key = normalize_display_model(model_key)
        self.profile = dict(DISPLAY_PROFILES[self.model_key])
        self._backlight = None

        try:
            import board
            import adafruit_rgb_display.st7789 as st7789
            import digitalio
        except ModuleNotFoundError as error:
            if str(getattr(error, "name", "")) == "RPi":
                raise RuntimeError(
                    "RPi.GPIO is missing in the virtual environment. "
                    "Run update.sh or install RPi.GPIO into /opt/backcountry-broadcast/.venv."
                ) from error
            raise

        pins = dict(self.profile["pins"])
        spi = board.SPI()
        cs_pin = digitalio.DigitalInOut(getattr(board, pins["cs"]))
        dc_pin = digitalio.DigitalInOut(getattr(board, pins["dc"]))
        reset_pin = digitalio.DigitalInOut(getattr(board, pins["reset"])) if pins.get("reset") else None
        if pins.get("backlight"):
            self._backlight = digitalio.DigitalInOut(getattr(board, pins["backlight"]))
            self._backlight.switch_to_output(value=True)

        # These offsets match the standard ST7789 geometry Waveshare uses for these two SPI panels.
        self._display = st7789.ST7789(
            spi,
            cs=cs_pin,
            dc=dc_pin,
            rst=reset_pin,
            baudrate=int(self.profile["baudrate"]),
            width=int(self.profile["width"]),
            height=int(self.profile["height"]),
            x_offset=int(self.profile["x_offset"]),
            y_offset=int(self.profile["y_offset"]),
            rotation=int(self.profile["rotation"]),
        )

    def set_backlight(self, enabled: bool) -> None:
        if self._backlight is not None:
            self._backlight.value = bool(enabled)

    def show(self, image: Image.Image) -> None:
        width = int(self.profile["width"])
        height = int(self.profile["height"])
        if image.mode != "RGB":
            image = image.convert("RGB")
        if image.size != (width, height):
            image = image.resize((width, height))
        self._display.image(image)

    def blank(self) -> None:
        self.show(Image.new("RGB", (int(self.profile["width"]), int(self.profile["height"])), "black"))


def choose_view(
    settings: dict[str, object],
    status: dict[str, object] | None,
    ui_state: ScreenUiState,
) -> str:
    if ui_state.manual_view_override == SETTINGS_VIEW_KEY:
        return SETTINGS_VIEW_KEY
    if ui_state.manual_view_override in DISPLAY_BUTTON_VIEW_ORDER:
        return str(ui_state.manual_view_override)
    configured = normalize_display_view(settings.get("display_view"))
    if configured != "auto":
        return configured
    if not status:
        return "boot"
    if str(status.get("networkMode") or "").strip().lower() == "hotspot":
        return "wifi"
    return "status"


def state_signature(
    settings: dict[str, object],
    status: dict[str, object] | None,
    view: str,
    ui_state: ScreenUiState,
) -> str:
    payload = {
        "display_enabled": bool(settings["display_enabled"]),
        "display_model": str(settings["display_model"]),
        "display_view": str(view),
        "display_status_poll_seconds": float(
            settings.get("display_status_poll_seconds") or DEFAULT_DISPLAY_STATUS_POLL_SECONDS
        ),
        "display_brightness": int(settings.get("display_brightness") or DEFAULT_DISPLAY_BRIGHTNESS),
        "display_button_pins": dict(settings.get("display_button_pins") or {}),
        "fallback_ap_enabled": bool(settings.get("fallback_ap_enabled")),
        "device_name": str(settings["device_name"]),
        "hotspot_ssid": str(settings["hotspot_ssid"]),
        "wifi_password": str(settings["wifi_password"]),
        "preferred_url": preferred_app_url(settings, status),
        "settings": {
            "selected_index": int(ui_state.settings_selected_index),
            "pending_power_action": str(ui_state.pending_power_action or ""),
            "notice": ui_state.active_notice(),
        },
        "status": {
            "networkMode": (status or {}).get("networkMode"),
            "networkName": (status or {}).get("networkName"),
            "hotspotSsid": (status or {}).get("hotspotSsid"),
            "clients": (status or {}).get("clients"),
            "libraryCount": (status or {}).get("libraryCount"),
            "activeStreams": (status or {}).get("activeStreams"),
            "maxStreams": (status or {}).get("maxStreams"),
            "sdMounted": (status or {}).get("sdMounted"),
            "ip": (status or {}).get("ip"),
            "appUrl": (status or {}).get("appUrl"),
        },
}
    return json.dumps(payload, sort_keys=True, ensure_ascii=True)


def render_for_view(
    view: str,
    settings: dict[str, object],
    status: dict[str, object] | None,
    ui_state: ScreenUiState,
) -> Image.Image:
    profile = DISPLAY_PROFILES[normalize_display_model(settings["display_model"])]
    if view == SETTINGS_VIEW_KEY:
        image = render_settings_screen(profile, settings, status, ui_state)
    elif view == "wifi":
        image = render_wifi_screen(profile, settings, status)
    elif view == "status":
        image = render_status_screen(profile, settings, status)
    else:
        image = render_boot_screen(profile, settings, status)

    brightness = normalize_display_brightness(settings.get("display_brightness"))
    if brightness >= 100:
        return image
    factor = max(0.05, brightness / 100.0)
    return Image.blend(Image.new("RGB", image.size, "black"), image, factor)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Drive the Backcountry Broadcast physical SPI screen.")
    parser.add_argument("--self-test", action="store_true", help="Draw a static test pattern and keep it on screen.")
    parser.add_argument(
        "--model",
        choices=sorted(DISPLAY_PROFILES.keys()),
        help="Override the configured display model for this run.",
    )
    return parser.parse_args()


def run_self_test(model_override: str | None = None) -> int:
    settings = load_screen_settings()
    model_key = normalize_display_model(model_override or settings.get("display_model"))
    display = PhysicalDisplay(model_key)
    display.set_backlight(True)
    display.show(render_self_test_screen(DISPLAY_PROFILES[model_key], model_key))
    log(f"Rendered self-test pattern for {model_key}. Press Ctrl+C when finished.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        return 0


def handle_button_action(
    action: str | None,
    settings: dict[str, object],
    status: dict[str, object] | None,
    ui_state: ScreenUiState,
) -> InteractionResult:
    if not action:
        return InteractionResult()

    current_view = choose_view(settings, status, ui_state)
    if action == "next":
        next_view = cycle_display_view(current_view)
        log(f"Display next button pressed. Switched to {next_view} view.")
        ui_state.manual_view_override = next_view
        return InteractionResult(should_redraw=True)
    if action == "next:long":
        log("Display next long-press detected. Opened the settings menu.")
        ui_state.settings_return_view = ui_state.manual_view_override
        ui_state.manual_view_override = SETTINGS_VIEW_KEY
        ui_state.pending_power_action = ""
        return InteractionResult(should_redraw=True)
    if action == "previous":
        previous_view = previous_display_view(current_view)
        log(f"Display previous button pressed. Switched to {previous_view} view.")
        ui_state.manual_view_override = previous_view
        return InteractionResult(should_redraw=True)
    if action == "previous:long":
        log("Display previous long-press detected. Jumped to boot view.")
        ui_state.manual_view_override = "boot"
        return InteractionResult(should_redraw=True)
    if action == "action":
        if ui_state.manual_view_override is None:
            log(f"Display action button pressed. Locked manual view on {current_view}.")
            ui_state.manual_view_override = current_view
            return InteractionResult(should_redraw=True)
        log("Display action button pressed. Returned to configured auto/manual view selection.")
        ui_state.manual_view_override = None
        return InteractionResult(should_redraw=True)
    if action == "action:long":
        log("Display action long-press detected. Toggling backlight.")
        return InteractionResult(toggle_backlight=True)
    return InteractionResult()


def handle_settings_button_action(
    action: str | None,
    settings: dict[str, object],
    ui_state: ScreenUiState,
) -> InteractionResult:
    if not action:
        return InteractionResult()

    menu_count = len(SETTINGS_MENU_ITEM_IDS)
    if action == "next":
        ui_state.settings_selected_index = (ui_state.settings_selected_index + 1) % menu_count
        ui_state.pending_power_action = ""
        return InteractionResult(should_redraw=True)
    if action == "previous":
        ui_state.settings_selected_index = (ui_state.settings_selected_index - 1) % menu_count
        ui_state.pending_power_action = ""
        return InteractionResult(should_redraw=True)
    if action in {"next:long", "previous:long"}:
        ui_state.manual_view_override = ui_state.settings_return_view
        ui_state.pending_power_action = ""
        set_ui_notice(ui_state, "Closed settings.", "info", 2.5)
        return InteractionResult(should_redraw=True)
    if action == "action:long":
        log("Display action long-press detected. Toggling backlight.")
        return InteractionResult(toggle_backlight=True)
    if action != "action":
        return InteractionResult()

    selected_id = SETTINGS_MENU_ITEM_IDS[ui_state.settings_selected_index]
    ui_state.pending_power_action = "" if selected_id not in {"reboot", "poweroff"} else ui_state.pending_power_action

    if selected_id == "wifi":
        try:
            next_enabled = not config_bool(settings.get("fallback_ap_enabled"), True)
            save_runtime_overrides(Path(settings["storage_root"]), fallback_ap_enabled=next_enabled)
            message = set_backcountry_wifi_enabled(settings, next_enabled)
        except Exception as error:
            set_ui_notice(ui_state, str(error), "error", 5.0)
            return InteractionResult(should_redraw=True)
        set_ui_notice(ui_state, message, "success")
        return InteractionResult(should_redraw=True, reload_settings=True, refresh_status=True)

    if selected_id == "brightness":
        try:
            next_brightness = next_display_brightness(settings.get("display_brightness"))
            save_runtime_overrides(Path(settings["storage_root"]), display_brightness=next_brightness)
        except Exception as error:
            set_ui_notice(ui_state, str(error), "error", 5.0)
            return InteractionResult(should_redraw=True)
        set_ui_notice(ui_state, f"Backlight set to {next_brightness}%.", "success")
        return InteractionResult(should_redraw=True, reload_settings=True)

    if selected_id == "reboot":
        if ui_state.pending_power_action == "reboot":
            set_ui_notice(ui_state, "Rebooting the Raspberry Pi...", "success", 10.0)
            ui_state.pending_power_action = ""
            return InteractionResult(should_redraw=True, system_action="reboot")
        ui_state.pending_power_action = "reboot"
        set_ui_notice(ui_state, "Press Action again to reboot the Raspberry Pi.", "info", 6.0)
        return InteractionResult(should_redraw=True)

    if selected_id == "poweroff":
        if ui_state.pending_power_action == "poweroff":
            set_ui_notice(ui_state, "Powering down the Raspberry Pi...", "success", 10.0)
            ui_state.pending_power_action = ""
            return InteractionResult(should_redraw=True, system_action="poweroff")
        ui_state.pending_power_action = "poweroff"
        set_ui_notice(ui_state, "Press Action again to power the Raspberry Pi down.", "info", 6.0)
        return InteractionResult(should_redraw=True)

    ui_state.manual_view_override = ui_state.settings_return_view
    set_ui_notice(ui_state, "Closed settings.", "info", 2.5)
    return InteractionResult(should_redraw=True)


def wait_for_action_or_timeout(
    timeout_seconds: float | None,
    buttons: DisplayButtonManager | None,
    *,
    poll_seconds: float = DISPLAY_BUTTON_POLL_SECONDS,
) -> str | None:
    if buttons is None:
        if timeout_seconds is None:
            time.sleep(max(1.0, poll_seconds))
            return None
        time.sleep(max(0.0, float(timeout_seconds)))
        return None
    return buttons.wait_for_action(timeout_seconds, poll_seconds=poll_seconds)


def seconds_until(deadline: float, fallback: float = 0.0) -> float:
    return max(float(fallback), deadline - time.monotonic())


def main() -> int:
    args = parse_args()
    if args.self_test:
        return run_self_test(args.model)

    display = None
    console_process: subprocess.Popen[bytes] | None = None
    console_signature = ""
    active_model = ""
    last_signature = ""
    last_init_error = ""
    last_console_error = ""
    button_manager: DisplayButtonManager | None = None
    ui_state = ScreenUiState()
    backlight_enabled = True
    blanked_for_disable = False
    settings = load_screen_settings()
    status: dict[str, object] | None = None
    next_status_poll_at = 0.0
    next_config_check_at = 0.0

    while True:
        if not backlight_enabled:
            if display is not None:
                try:
                    display.blank()
                    display.set_backlight(False)
                except Exception as error:
                    log(f"Could not enter headless screen mode cleanly: {error}")
            button_action = wait_for_action_or_timeout(
                None,
                button_manager,
                poll_seconds=DISPLAY_BUTTON_HEADLESS_POLL_SECONDS,
            )
            if button_action:
                backlight_enabled = True
                if display is not None:
                    try:
                        display.set_backlight(True)
                    except Exception as error:
                        log(f"Could not wake the screen backlight: {error}")
                status = None
                next_status_poll_at = 0.0
                next_config_check_at = 0.0
                last_signature = ""
            continue

        now = time.monotonic()
        if now >= next_config_check_at:
            settings = load_screen_settings()
            next_config_check_at = now + DEFAULT_CONFIG_CHECK_SECONDS

        status_poll_seconds = float(
            settings.get("display_status_poll_seconds") or DEFAULT_DISPLAY_STATUS_POLL_SECONDS
        )
        backend = normalize_display_backend(settings.get("display_backend"))
        model_key = normalize_display_model(settings.get("display_model"))
        button_pins = settings.get("display_button_pins") if isinstance(settings.get("display_button_pins"), dict) else {}

        if button_manager is None or not button_manager.matches(button_pins):
            button_manager = DisplayButtonManager(button_pins)

        if not settings["display_enabled"]:
            if console_process is not None:
                stop_console_process(console_process, "Stopped boot console mirror because the physical screen is disabled.")
                console_process = None
                console_signature = ""
                last_console_error = ""
            if display is not None and not blanked_for_disable:
                try:
                    display.blank()
                    display.set_backlight(False)
                except Exception:
                    pass
                blanked_for_disable = True
            backlight_enabled = True
            ui_state.manual_view_override = None
            ui_state.pending_power_action = ""
            status = None
            next_status_poll_at = 0.0
            wait_for_action_or_timeout(DEFAULT_DISABLED_REFRESH_SECONDS, button_manager)
            continue

        blanked_for_disable = False
        if backend == "console":
            if display is not None:
                try:
                    display.blank()
                    display.set_backlight(False)
                except Exception:
                    pass
                display = None
                active_model = ""
                last_signature = ""

            binary_path = console_binary_path(model_key)
            desired_signature = f"{model_key}:{binary_path}"
            process_exited = console_process is not None and console_process.poll() is not None

            if console_process is None or process_exited or console_signature != desired_signature:
                if console_process is not None:
                    stop_console_process(console_process)
                    console_process = None
                if not binary_path.exists():
                    message = f"Console display binary is missing for {model_key}: {binary_path}"
                    if message != last_console_error:
                        log(f"{message}. Falling back to app-driven userspace mode.")
                        last_console_error = message
                    backend = "userspace"
                else:
                    try:
                        console_process = subprocess.Popen([str(binary_path)], cwd=str(APP_ROOT))
                        console_signature = desired_signature
                        last_console_error = ""
                        log(f"Started boot console mirror for {model_key}.")
                    except OSError as error:
                        if str(error) != last_console_error:
                            log(f"Could not start boot console mirror: {error}. Falling back to app-driven userspace mode.")
                            last_console_error = str(error)
                        console_process = None
                        console_signature = ""
                        backend = "userspace"

            if backend == "console":
                wait_for_action_or_timeout(min(status_poll_seconds, DEFAULT_CONFIG_CHECK_SECONDS), button_manager)
                continue

        if console_process is not None:
            stop_console_process(console_process, "Stopped boot console mirror and returned to app-driven screen mode.")
            console_process = None
            console_signature = ""
            last_console_error = ""

        if display is None or active_model != str(settings["display_model"]):
            try:
                display = PhysicalDisplay(str(settings["display_model"]))
                display.set_backlight(backlight_enabled)
                active_model = str(settings["display_model"])
                last_signature = ""
                last_init_error = ""
                log(f"Initialized physical screen for {active_model}.")
            except Exception as error:
                if str(error) != last_init_error:
                    log(f"Screen initialization failed: {error}")
                    last_init_error = str(error)
                display = None
                active_model = ""
                time.sleep(10)
                continue

        now = time.monotonic()
        if status is None or now >= next_status_poll_at:
            status = fetch_status(settings)
            next_status_poll_at = now + status_poll_seconds
        view = choose_view(settings, status, ui_state)
        signature = state_signature(settings, status, view, ui_state)
        if signature != last_signature and backlight_enabled:
            try:
                display.show(render_for_view(view, settings, status, ui_state))
                last_signature = signature
            except Exception as error:
                log(f"Screen render failed: {error}")
                display = None
                active_model = ""
                time.sleep(5)
                continue
        button_action = wait_for_action_or_timeout(
            min(seconds_until(next_status_poll_at), seconds_until(next_config_check_at)),
            button_manager,
        )
        if view == SETTINGS_VIEW_KEY:
            try:
                interaction = handle_settings_button_action(button_action, settings, ui_state)
            except Exception as error:
                log(f"Settings action failed: {error}")
                set_ui_notice(ui_state, str(error), "error", 5.0)
                interaction = InteractionResult(should_redraw=True)
        else:
            interaction = handle_button_action(button_action, settings, status, ui_state)

        if interaction.reload_settings:
            settings = load_screen_settings()
            next_config_check_at = time.monotonic() + DEFAULT_CONFIG_CHECK_SECONDS
        if interaction.refresh_status:
            status = fetch_status(settings)
            next_status_poll_at = time.monotonic() + status_poll_seconds

        if interaction.toggle_backlight:
            backlight_enabled = not backlight_enabled
            try:
                if display is not None:
                    display.set_backlight(backlight_enabled)
                if not backlight_enabled:
                    if display is not None:
                        display.blank()
                else:
                    last_signature = ""
            except Exception as error:
                log(f"Could not update backlight state: {error}")
        if interaction.should_redraw:
            immediate_view = choose_view(settings, status, ui_state)
            immediate_signature = state_signature(settings, status, immediate_view, ui_state)
            try:
                if backlight_enabled and display is not None:
                    display.show(render_for_view(immediate_view, settings, status, ui_state))
                    last_signature = immediate_signature
            except Exception as error:
                log(f"Screen render failed after button press: {error}")
                display = None
                active_model = ""
                time.sleep(2)
        if interaction.system_action:
            try:
                time.sleep(0.35)
                perform_power_action(interaction.system_action)
            except Exception as error:
                log(f"Power action failed: {error}")
                set_ui_notice(ui_state, str(error), "error", 5.0)
                last_signature = ""


if __name__ == "__main__":
    raise SystemExit(main())
