from __future__ import annotations

import json
import mimetypes
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from flask import Flask, Response, jsonify, redirect, request, send_file, send_from_directory


APP_ROOT = Path(__file__).resolve().parents[1]
STATIC_ROOT = APP_ROOT / "data"
DEFAULT_STORAGE_ROOT = APP_ROOT / "sdcard-template"

DEFAULT_DEVICE_NAME = "Nomad Screen"
DEFAULT_MDNS_HOST = "nomadscreen"
DEFAULT_ACCESS_POINT_SSID = "NomadScreen"
DEFAULT_ACCESS_POINT_PASSWORD = "backpackingmedia"
DEFAULT_WIFI_INTERFACE = "wlan0"
DEFAULT_KNOWN_WIFI_TIMEOUT_SECONDS = 20
DEFAULT_APP_PATH = "/app"
DEFAULT_MEDIA_ROOT = "/media"
DEFAULT_METADATA_ROOT = "/media/.nomadscreen"
DEFAULT_METADATA_INDEX_PATH = "/media/.nomadscreen/library.json"
DEFAULT_RUNTIME_CONFIG_PATH = "/nomadscreen.config.json"
DEFAULT_METADATA_REFRESH_SCRIPT = APP_ROOT / "tools" / "nomadscreen_refresh_metadata.py"
DEFAULT_BIND_ADDRESS = "0.0.0.0"
DEFAULT_HTTP_PORT = 80
DEFAULT_MAX_CLIENTS = 6
DEFAULT_MAX_STREAMS = 12
DEFAULT_CLIENT_WINDOW_SECONDS = 300
DEFAULT_METADATA_REFRESH_TIMEOUT_SECONDS = 1800
MAX_DEVICE_NAME_LENGTH = 80
MAX_HOTSPOT_SSID_LENGTH = 32
MIN_HOTSPOT_PASSWORD_LENGTH = 8
MAX_HOTSPOT_PASSWORD_LENGTH = 63

SECTION_ORDER = {
    "movies": 0,
    "tv": 1,
    "music": 2,
    "audiobooks": 3,
    "documents": 4,
}

UPLOAD_SECTION_CONFIG = {
    "movies": {"label": "Movies", "base_path": "/media/movies", "media_types": {"video"}},
    "tv": {"label": "TV Shows", "base_path": "/media/tv", "media_types": {"video"}},
    "music": {"label": "Music", "base_path": "/media/music", "media_types": {"audio"}},
    "audiobooks": {"label": "Audiobooks", "base_path": "/media/audiobooks", "media_types": {"audio"}},
    "documents": {"label": "Documents", "base_path": "/media/documents", "media_types": {"document", "image"}},
}


def normalize_device_name(value: str) -> str:
    return " ".join(str(value or "").split()).strip()


def normalize_hotspot_ssid(value: str) -> str:
    return " ".join(str(value or "").split()).strip()[:MAX_HOTSPOT_SSID_LENGTH]


def normalize_hotspot_password(value: str) -> str:
    return str(value or "").strip()


def validated_hotspot_password(value: str) -> str:
    password = normalize_hotspot_password(value)
    if len(password) < MIN_HOTSPOT_PASSWORD_LENGTH or len(password) > MAX_HOTSPOT_PASSWORD_LENGTH:
        raise ValueError(
            f"Fallback Wi-Fi password must be {MIN_HOTSPOT_PASSWORD_LENGTH}-{MAX_HOTSPOT_PASSWORD_LENGTH} characters."
        )
    return password


def derive_compact_device_token(device_name: str, lowercase: bool) -> str:
    normalized = normalize_device_name(device_name)
    output = []
    capitalize_next = True
    for character in normalized:
        if character.isalnum():
            if lowercase:
                output.append(character.lower())
            elif capitalize_next:
                output.append(character.upper())
            else:
                output.append(character.lower())
            capitalize_next = False
        elif output:
            capitalize_next = True
    return "".join(output)


def sanitize_mdns_host(value: str) -> str:
    output = []
    previous_dash = False
    for character in str(value or ""):
        if character.isalnum():
            output.append(character.lower())
            previous_dash = False
        elif character in {" ", "-", "_", "."} and output and not previous_dash:
            output.append("-")
            previous_dash = True
    result = "".join(output)[:63].rstrip("-")
    return result


def derived_access_point_ssid(device_name: str) -> str:
    derived = derive_compact_device_token(device_name, lowercase=False)
    return derived or DEFAULT_ACCESS_POINT_SSID


def derived_mdns_host(device_name: str) -> str:
    compact = derive_compact_device_token(device_name, lowercase=True)
    if compact:
        return compact
    sanitized = sanitize_mdns_host(device_name)
    return sanitized or DEFAULT_MDNS_HOST


def configured_access_point_ssid(raw_config: dict[str, object], device_name: str) -> str:
    wifi_block = raw_config.get("wifi") if isinstance(raw_config.get("wifi"), dict) else {}
    raw_value = (
        raw_config.get("hotspotSsid")
        or raw_config.get("accessPointSsid")
        or wifi_block.get("ssid")
        or ""
    )
    return normalize_hotspot_ssid(str(raw_value)) or derived_access_point_ssid(device_name)


def read_runtime_config_file(config_path: Path) -> tuple[dict[str, object], str]:
    raw_config: dict[str, object] = {}
    config_source = "defaults"
    if config_path.exists():
        try:
            raw_config = json.loads(config_path.read_text(encoding="utf-8"))
            config_source = str(config_path)
        except (OSError, json.JSONDecodeError):
            raw_config = {}
            config_source = f"{config_path} (unreadable)"
    return raw_config, config_source


def normalize_virtual_path(raw_path: str) -> str:
    normalized = str(raw_path or "").strip().replace("\\", "/")
    if not normalized:
        return ""
    if not normalized.startswith("/"):
        normalized = "/" + normalized
    while "//" in normalized:
        normalized = normalized.replace("//", "/")
    pieces = []
    for piece in normalized.split("/"):
        if not piece or piece == ".":
            continue
        if piece == "..":
            if pieces:
                pieces.pop()
            continue
        pieces.append(piece)
    return "/" + "/".join(pieces)


def lowercase_copy(value: str) -> str:
    return str(value or "").lower()


def split_path(path: str) -> list[str]:
    return [segment for segment in normalize_virtual_path(path).split("/") if segment]


def normalize_spacing(value: str) -> str:
    normalized = []
    previous_space = False
    for character in str(value or "").strip():
        if character in {" ", "_", "-", "."}:
            if normalized and not previous_space:
                normalized.append(" ")
            previous_space = True
            continue
        normalized.append(character)
        previous_space = False
    return "".join(normalized).strip()


def prettify_name(value: str) -> str:
    pretty = normalize_spacing(value)
    return pretty or str(value or "")


def title_from_path(path: str) -> str:
    name = Path(normalize_virtual_path(path)).name
    if "." in name:
        name = name.rsplit(".", 1)[0]
    return prettify_name(name)


def file_name_from_path(path: str) -> str:
    return Path(normalize_virtual_path(path)).name


def slugify(title: str) -> str:
    output = []
    previous_dash = False
    for character in str(title or ""):
        if character.isalnum():
            output.append(character.lower())
            previous_dash = False
        elif output and not previous_dash:
            output.append("-")
            previous_dash = True
    return "".join(output).rstrip("-") or "library-item"


def classify_media_type(path: str) -> str:
    lowered = lowercase_copy(path)
    if lowered.endswith((".mp4", ".mkv", ".mov", ".webm", ".m4v", ".avi")):
        return "video"
    if lowered.endswith((".mp3", ".m4a", ".m4b", ".aac", ".wav", ".flac", ".ogg")):
        return "audio"
    if lowered.endswith((".jpg", ".jpeg", ".png", ".gif", ".webp")):
        return "image"
    if lowered.endswith((".pdf", ".txt", ".md", ".csv", ".gpx", ".kml", ".doc", ".docx")):
        return "document"
    return ""


def section_from_path(segments: list[str], media_type: str) -> str:
    if len(segments) >= 2 and segments[0] == "media":
        section = lowercase_copy(segments[1])
        if section == "photos":
            return "documents"
        if section in {"movies", "tv", "music", "audiobooks", "documents"}:
            return section
    if media_type == "video":
        return "movies"
    if media_type == "audio":
        return "music"
    if media_type in {"image", "document"}:
        return "documents"
    return "library"


def parse_season_number(label: str) -> int:
    lowered = lowercase_copy(label)
    if lowered.startswith("special"):
        return 0
    digits = "".join(character for character in lowered if character.isdigit())
    return int(digits) if digits else 1


def parse_episode_number(title: str) -> int:
    upper = str(title or "").upper()
    for index in range(len(upper) - 1):
        if upper[index] == "E" and upper[index + 1].isdigit():
            digits = []
            for cursor in range(index + 1, len(upper)):
                if not upper[cursor].isdigit():
                    break
                digits.append(upper[cursor])
            if digits:
                return int("".join(digits))
    leading_digits = []
    for character in upper:
        if not character.isdigit():
            break
        leading_digits.append(character)
    return int("".join(leading_digits)) if leading_digits else 0


def merge_string(current_value: str, new_value: str) -> str:
    return str(new_value) if str(new_value or "") else str(current_value or "")


def guess_mime_type(path: str) -> str:
    lowered = lowercase_copy(path)
    if lowered.endswith(".m4b"):
        return "audio/mp4"
    if lowered.endswith(".md"):
        return "text/markdown; charset=utf-8"
    if lowered.endswith(".gpx"):
        return "application/gpx+xml"
    if lowered.endswith(".kml"):
        return "application/vnd.google-earth.kml+xml"
    guessed, _encoding = mimetypes.guess_type(path)
    return guessed or "application/octet-stream"


def env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def config_bool(value: object, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    lowered = str(value).strip().lower()
    if not lowered:
        return default
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    return default


def safe_int(value: object, default: int, minimum: int = 0) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= minimum else default


def default_upload_temp_directory() -> Path:
    if os.name == "posix":
        return Path("/var/tmp/nomadscreen-upload")
    return Path(tempfile.gettempdir()) / "nomadscreen-upload"


def sanitize_upload_filename(raw_filename: str) -> str:
    file_name = Path(str(raw_filename or "").replace("\\", "/")).name.strip().replace("\x00", "")
    if not file_name or file_name in {".", ".."} or file_name.startswith("."):
        return ""
    return file_name


def sanitize_relative_segments(raw_path: str) -> list[str]:
    segments = []
    for piece in str(raw_path or "").replace("\\", "/").split("/"):
        cleaned = " ".join(piece.split()).strip()
        if not cleaned or cleaned in {".", ".."}:
            continue
        segments.append(cleaned)
    return segments


def build_upload_destination_path(section: str, folder: str) -> str:
    section_config = UPLOAD_SECTION_CONFIG.get(section)
    if section_config is None:
        return ""

    base_path = normalize_virtual_path(str(section_config["base_path"]))
    pieces = split_path(base_path) + sanitize_relative_segments(folder)
    destination = normalize_virtual_path("/" + "/".join(pieces))
    if not destination:
        return ""
    if destination != base_path and not destination.startswith(base_path + "/"):
        return ""
    return destination


def upload_section_from_destination(destination_path: str) -> str:
    normalized = normalize_virtual_path(destination_path)
    for section, config in UPLOAD_SECTION_CONFIG.items():
        base_path = normalize_virtual_path(str(config["base_path"]))
        if normalized == base_path or normalized.startswith(base_path + "/"):
            return section
    return ""


def normalize_upload_destination(raw_path: str) -> str:
    normalized = normalize_virtual_path(raw_path)
    return normalized if upload_section_from_destination(normalized) else ""


def upload_folder_from_destination(destination_path: str, section: str) -> str:
    section_config = UPLOAD_SECTION_CONFIG.get(section)
    if section_config is None:
        return ""
    base_path = normalize_virtual_path(str(section_config["base_path"]))
    normalized = normalize_upload_destination(destination_path)
    if not normalized or normalized == base_path:
        return ""
    if normalized.startswith(base_path + "/"):
        return normalized[len(base_path) + 1 :]
    return ""


def build_upload_virtual_path_from_destination(destination: str, relative_path: str, fallback_name: str = "") -> str:
    safe_destination = normalize_upload_destination(destination)
    if not safe_destination:
        return ""

    segments = sanitize_relative_segments(relative_path)
    if not segments and fallback_name:
        fallback = sanitize_upload_filename(fallback_name)
        segments = [fallback] if fallback else []
    if not segments:
        return ""

    file_name = sanitize_upload_filename(segments[-1] or fallback_name)
    if not file_name:
        return ""

    pieces = split_path(safe_destination) + segments[:-1] + [file_name]
    target_path = normalize_virtual_path("/" + "/".join(pieces))
    if not target_path or target_path == safe_destination:
        return ""
    if not target_path.startswith(safe_destination.rstrip("/") + "/"):
        return ""
    return target_path


def build_upload_virtual_path(section: str, folder: str, file_name: str) -> str:
    destination = build_upload_destination_path(section, folder)
    if not destination:
        return ""
    return build_upload_virtual_path_from_destination(destination, file_name, file_name)


def ensure_unique_path(target_path: Path) -> Path:
    if not target_path.exists():
        return target_path

    stem = target_path.stem
    suffix = target_path.suffix
    counter = 2
    while True:
        candidate = target_path.with_name(f"{stem} ({counter}){suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


def iso_timestamp_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_upload_id(raw_value: object) -> str:
    output = []
    previous_dash = False
    for character in str(raw_value or "").strip():
        if character.isalnum():
            output.append(character.lower())
            previous_dash = False
        elif character in {"-", "_"} and output and not previous_dash:
            output.append(character)
            previous_dash = True
        elif output and not previous_dash:
            output.append("-")
            previous_dash = True
    return "".join(output).strip("-")[:80]


def load_settings() -> dict[str, object]:
    storage_root_value = os.environ.get("NOMADSCREEN_STORAGE_ROOT", "").strip()
    storage_root = Path(storage_root_value) if storage_root_value else DEFAULT_STORAGE_ROOT
    storage_root = storage_root.expanduser()
    config_path = storage_root / DEFAULT_RUNTIME_CONFIG_PATH.lstrip("/")
    raw_config, config_source = read_runtime_config_file(config_path)

    raw_name = raw_config.get("deviceName") or raw_config.get("serverName") or DEFAULT_DEVICE_NAME
    device_name = normalize_device_name(str(raw_name))[:MAX_DEVICE_NAME_LENGTH] or DEFAULT_DEVICE_NAME

    wifi_password = normalize_hotspot_password(
        raw_config.get("wifiPassword")
        or ((raw_config.get("wifi") or {}).get("password") if isinstance(raw_config.get("wifi"), dict) else "")
        or DEFAULT_ACCESS_POINT_PASSWORD
    )
    if wifi_password and (
        len(wifi_password) < MIN_HOTSPOT_PASSWORD_LENGTH or len(wifi_password) > MAX_HOTSPOT_PASSWORD_LENGTH
    ):
        wifi_password = DEFAULT_ACCESS_POINT_PASSWORD
    hotspot_ssid = configured_access_point_ssid(raw_config, device_name)
    media_root_value = (
        os.environ.get("NOMADSCREEN_MEDIA_ROOT", "").strip()
        or str(raw_config.get("mediaPath") or raw_config.get("mediaDirectory") or "")
    )
    if media_root_value:
        media_directory = Path(media_root_value).expanduser()
        if not media_directory.is_absolute():
            media_directory = storage_root / media_directory
    else:
        media_directory = storage_root / DEFAULT_MEDIA_ROOT.lstrip("/")
    media_directory = media_directory.expanduser()
    metadata_refresh_script_value = (
        os.environ.get("NOMADSCREEN_METADATA_REFRESH_SCRIPT", "").strip()
        or str(raw_config.get("metadataRefreshScript") or "")
    )
    if metadata_refresh_script_value:
        metadata_refresh_script = Path(metadata_refresh_script_value).expanduser()
        if not metadata_refresh_script.is_absolute():
            metadata_refresh_script = APP_ROOT / metadata_refresh_script
    else:
        metadata_refresh_script = DEFAULT_METADATA_REFRESH_SCRIPT
    metadata_refresh_script = metadata_refresh_script.expanduser()
    upload_tmp_value = os.environ.get("NOMADSCREEN_UPLOAD_TMP_DIR", "").strip()
    if upload_tmp_value:
        upload_tmp_directory = Path(upload_tmp_value).expanduser()
        if not upload_tmp_directory.is_absolute():
            upload_tmp_directory = storage_root / upload_tmp_directory
    else:
        upload_tmp_directory = default_upload_temp_directory()
    upload_tmp_directory = upload_tmp_directory.expanduser()

    bind_address = (
        os.environ.get("NOMADSCREEN_BIND", "").strip()
        or str(raw_config.get("bindAddress") or DEFAULT_BIND_ADDRESS)
    )
    http_port = safe_int(
        os.environ.get("NOMADSCREEN_PORT") or raw_config.get("httpPort") or raw_config.get("port"),
        DEFAULT_HTTP_PORT,
        1,
    )
    wifi_interface = (
        os.environ.get("NOMADSCREEN_WIFI_INTERFACE", "").strip()
        or str(
            raw_config.get("wifiInterface")
            or ((raw_config.get("wifi") or {}).get("interface") if isinstance(raw_config.get("wifi"), dict) else "")
            or DEFAULT_WIFI_INTERFACE
        ).strip()
    )
    known_wifi_timeout_seconds = safe_int(
        raw_config.get("knownWifiTimeoutSeconds") or raw_config.get("wifiConnectTimeoutSeconds"),
        DEFAULT_KNOWN_WIFI_TIMEOUT_SECONDS,
        5,
    )
    metadata_refresh_on_rescan = env_bool(
        "NOMADSCREEN_METADATA_REFRESH_ON_RESCAN",
        config_bool(raw_config.get("metadataRefreshOnRescan"), True),
    )
    metadata_refresh_timeout_seconds = safe_int(
        os.environ.get("NOMADSCREEN_METADATA_REFRESH_TIMEOUT_SECONDS") or raw_config.get("metadataRefreshTimeoutSeconds"),
        DEFAULT_METADATA_REFRESH_TIMEOUT_SECONDS,
        30,
    )
    fallback_ap_enabled = env_bool(
        "NOMADSCREEN_FALLBACK_AP",
        config_bool(
            raw_config.get("fallbackAccessPointEnabled", raw_config.get("accessPointEnabled")),
            True,
        ),
    )
    mdns_host = sanitize_mdns_host(str(raw_config.get("mdnsHost") or "")) or derived_mdns_host(device_name)

    return {
        "storage_root": storage_root,
        "media_directory": media_directory,
        "metadata_refresh_script": metadata_refresh_script,
        "metadata_refresh_on_rescan": metadata_refresh_on_rescan,
        "metadata_refresh_timeout_seconds": metadata_refresh_timeout_seconds,
        "upload_tmp_directory": upload_tmp_directory,
        "config_path": config_path,
        "device_name": device_name,
        "ssid": hotspot_ssid,
        "wifi_password": wifi_password,
        "wifi_interface": wifi_interface or DEFAULT_WIFI_INTERFACE,
        "known_wifi_timeout_seconds": known_wifi_timeout_seconds,
        "fallback_ap_enabled": fallback_ap_enabled,
        "mdns_host": mdns_host,
        "mdns_enabled": env_bool("NOMADSCREEN_MDNS", bool(raw_config.get("mdnsEnabled", False))),
        "bind_address": bind_address,
        "http_port": http_port,
        "max_clients": safe_int(raw_config.get("maxClients"), DEFAULT_MAX_CLIENTS, 1),
        "max_streams": safe_int(raw_config.get("maxStreams"), DEFAULT_MAX_STREAMS, 1),
        "client_window_seconds": safe_int(
            raw_config.get("clientWindowSeconds"),
            DEFAULT_CLIENT_WINDOW_SECONDS,
            30,
        ),
        "tmdb_api_key": str(raw_config.get("tmdbApiKey") or "").strip(),
        "tmdb_bearer_token": str(raw_config.get("tmdbBearerToken") or "").strip(),
        "config_source": config_source,
    }


class AppState:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.settings = load_settings()
        self.upload_temp_ready = False
        self.configure_upload_temp_directory()
        self.media_library: list[dict[str, object]] = []
        self.item_metadata: list[dict[str, object]] = []
        self.show_metadata: dict[str, dict[str, object]] = {}
        self.metadata_available = False
        self.metadata_generated_at = ""
        self.metadata_generator = ""
        self.metadata_index_stale = False
        self.last_played_title = ""
        self.last_played_type = ""
        self.last_played_at = 0.0
        self.active_streams = 0
        self.recent_clients: dict[str, float] = {}
        self.storage_ready = False
        self.upload_status = self.default_upload_status()
        self.scan_library()

    def default_upload_status(self) -> dict[str, object]:
        return {
            "id": "",
            "active": False,
            "phase": "idle",
            "destination": "",
            "section": "",
            "folder": "",
            "fileCount": 0,
            "uploadedCount": 0,
            "bytesSent": 0,
            "bytesTotal": 0,
            "percent": 0,
            "message": "",
            "error": "",
            "warnings": [],
            "startedAt": "",
            "updatedAt": "",
            "completedAt": "",
        }

    def upload_status_payload(self) -> dict[str, object]:
        with self.lock:
            payload = dict(self.upload_status)
        payload["warnings"] = list(payload.get("warnings") or [])
        return payload

    def set_upload_status(
        self,
        upload_id: str,
        *,
        active: bool | None = None,
        phase: str | None = None,
        destination: str | None = None,
        section: str | None = None,
        folder: str | None = None,
        file_count: int | None = None,
        uploaded_count: int | None = None,
        bytes_sent: int | None = None,
        bytes_total: int | None = None,
        message: str | None = None,
        error: str | None = None,
        warnings: list[str] | None = None,
    ) -> dict[str, object]:
        safe_upload_id = normalize_upload_id(upload_id) or f"upload-{int(time.time() * 1000)}"
        safe_phase = lowercase_copy(phase) if phase is not None else None
        if safe_phase not in {None, "idle", "uploading", "processing", "completed", "error"}:
            safe_phase = "uploading"
        safe_destination = normalize_upload_destination(destination) if destination is not None else None
        safe_section = lowercase_copy(section) if section is not None else None
        safe_folder = "/".join(sanitize_relative_segments(folder)) if folder is not None else None
        if safe_destination:
            safe_section = upload_section_from_destination(safe_destination)
            safe_folder = upload_folder_from_destination(safe_destination, safe_section)
        elif safe_section:
            if safe_section not in UPLOAD_SECTION_CONFIG:
                safe_section = ""
                safe_folder = ""
            else:
                safe_destination = build_upload_destination_path(safe_section, safe_folder or "")
        safe_message = " ".join(str(message or "").split()).strip()[:240] if message is not None else None
        safe_error = " ".join(str(error or "").split()).strip()[:240] if error is not None else None
        safe_warnings = (
            [" ".join(str(entry or "").split()).strip()[:240] for entry in warnings if str(entry or "").strip()]
            if warnings is not None
            else None
        )
        now = iso_timestamp_now()

        with self.lock:
            current = dict(self.upload_status)
            if str(current.get("id") or "") != safe_upload_id:
                current = self.default_upload_status()
                current["id"] = safe_upload_id
                current["startedAt"] = now
            elif not str(current.get("startedAt") or ""):
                current["startedAt"] = now

            if safe_phase is not None:
                current["phase"] = safe_phase
            if safe_destination is not None:
                current["destination"] = safe_destination
            if safe_section is not None:
                current["section"] = safe_section
            if safe_folder is not None:
                current["folder"] = safe_folder
            if file_count is not None:
                current["fileCount"] = max(int(file_count), 0)
            if uploaded_count is not None:
                current["uploadedCount"] = max(int(uploaded_count), 0)
            if bytes_total is not None:
                current["bytesTotal"] = max(int(bytes_total), 0)
            if bytes_sent is not None:
                current["bytesSent"] = max(int(bytes_sent), 0)
            if current["bytesTotal"] > 0 and current["bytesSent"] > current["bytesTotal"]:
                current["bytesSent"] = current["bytesTotal"]
            if safe_message is not None:
                current["message"] = safe_message
            if safe_error is not None:
                current["error"] = safe_error
            if safe_warnings is not None:
                current["warnings"] = safe_warnings

            if current["phase"] == "processing" and current["bytesTotal"] > 0:
                current["bytesSent"] = current["bytesTotal"]
            if current["phase"] == "completed":
                current["bytesSent"] = current["bytesTotal"] or current["bytesSent"]
                current["completedAt"] = now
            elif current["phase"] != "completed":
                current["completedAt"] = ""

            if current["bytesTotal"] > 0:
                current["percent"] = int(round((current["bytesSent"] / current["bytesTotal"]) * 100))
            elif current["phase"] in {"processing", "completed"} and current["bytesSent"] > 0:
                current["percent"] = 100
            else:
                current["percent"] = 0

            if active is not None:
                current["active"] = bool(active)
            else:
                current["active"] = current["phase"] in {"uploading", "processing"}
            if current["phase"] in {"idle", "completed", "error"}:
                current["active"] = False

            current["updatedAt"] = now
            self.upload_status = current
            return self.upload_status_payload()

    def cleanup_recent_clients(self, now: float | None = None) -> None:
        cutoff = (now or time.time()) - int(self.settings["client_window_seconds"])
        for client_ip, seen_at in list(self.recent_clients.items()):
            if seen_at < cutoff:
                self.recent_clients.pop(client_ip, None)

    def record_client(self, remote_address: str | None) -> None:
        if not remote_address:
            return
        with self.lock:
            now = time.time()
            self.recent_clients[remote_address] = now
            self.cleanup_recent_clients(now)

    def active_client_count(self) -> int:
        with self.lock:
            self.cleanup_recent_clients()
            return len(self.recent_clients)

    def begin_stream(self) -> None:
        with self.lock:
            self.active_streams += 1

    def end_stream(self) -> None:
        with self.lock:
            if self.active_streams > 0:
                self.active_streams -= 1

    def best_local_ip(self) -> str:
        bind_address = str(self.settings["bind_address"]).strip()
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

    def nmcli_value(self, fields: str, *args: str) -> list[str]:
        try:
            completed = subprocess.run(
                ["nmcli", "-t", "-g", fields, *args],
                check=False,
                capture_output=True,
                text=True,
                timeout=1.5,
            )
        except (OSError, subprocess.SubprocessError):
            return []
        if completed.returncode != 0:
            return []
        return [line.strip() for line in completed.stdout.splitlines() if line.strip()]

    def network_snapshot(self) -> dict[str, object]:
        wifi_interface = str(self.settings.get("wifi_interface") or DEFAULT_WIFI_INTERFACE).strip() or DEFAULT_WIFI_INTERFACE
        hotspot_name = str(self.settings["ssid"])
        hotspot_password = str(self.settings["wifi_password"])
        snapshot = {
            "mode": "unknown",
            "current_name": "",
            "current_password": "",
            "hotspot_name": hotspot_name,
            "hotspot_password": hotspot_password,
            "interface": wifi_interface,
        }

        for line in self.nmcli_value("DEVICE,TYPE,STATE,CONNECTION", "device", "status"):
            if not line.startswith(f"{wifi_interface}:"):
                continue

            parts = line.split(":", 3)
            if len(parts) < 4:
                return snapshot

            _device, device_type, device_state, connection_name = parts
            if device_type != "wifi":
                snapshot["mode"] = "offline"
                return snapshot

            if device_state != "connected" or not connection_name or connection_name == "--":
                snapshot["mode"] = "offline"
                return snapshot

            mode_lines = self.nmcli_value("802-11-wireless.mode", "connection", "show", connection_name)
            ssid_lines = self.nmcli_value("802-11-wireless.ssid", "connection", "show", connection_name)
            connection_mode = mode_lines[0].lower() if mode_lines else ""
            current_name = ssid_lines[0] if ssid_lines else connection_name

            if connection_mode == "ap":
                snapshot["mode"] = "hotspot"
                snapshot["current_name"] = current_name or hotspot_name
                snapshot["current_password"] = hotspot_password
                return snapshot

            snapshot["mode"] = "client"
            snapshot["current_name"] = current_name or connection_name
            return snapshot

        return snapshot

    def compose_url(self, host: str, port: int, suffix: str = "") -> str:
        safe_host = str(host or "").strip() or "127.0.0.1"
        safe_suffix = suffix if suffix.startswith("/") or not suffix else "/" + suffix
        if port in {80, 443}:
            return f"http://{safe_host}{safe_suffix}"
        return f"http://{safe_host}:{port}{safe_suffix}"

    def media_root_path(self) -> Path:
        return Path(self.settings["media_directory"]).resolve(strict=False)

    def upload_temp_root_path(self) -> Path:
        return Path(self.settings["upload_tmp_directory"]).resolve(strict=False)

    def configure_upload_temp_directory(self) -> None:
        upload_temp_directory = self.upload_temp_root_path()
        try:
            upload_temp_directory.mkdir(parents=True, exist_ok=True)
            tempfile.tempdir = str(upload_temp_directory)
            self.upload_temp_ready = True
        except OSError:
            self.upload_temp_ready = False

    def metadata_refresh_script_path(self) -> Path:
        return Path(self.settings["metadata_refresh_script"]).resolve(strict=False)

    def metadata_refresh_configured(self) -> bool:
        return bool(
            str(self.settings.get("tmdb_api_key") or "").strip() or str(self.settings.get("tmdb_bearer_token") or "").strip()
        )

    def internet_available(self) -> bool:
        for host, port in (("api.themoviedb.org", 443), ("1.1.1.1", 53)):
            try:
                with socket.create_connection((host, port), timeout=2.5):
                    return True
            except OSError:
                continue
        return False

    def summarize_process_output(self, stdout: str, stderr: str, max_lines: int = 8, max_chars: int = 600) -> str:
        lines = [line.strip() for line in f"{stdout}\n{stderr}".splitlines() if line.strip()]
        if not lines:
            return ""
        summary = "\n".join(lines[-max_lines:])
        if len(summary) > max_chars:
            summary = summary[-max_chars:]
        return summary

    def run_metadata_refresh_on_rescan(self) -> dict[str, object]:
        script_path = self.metadata_refresh_script_path()
        result: dict[str, object] = {
            "enabled": bool(self.settings.get("metadata_refresh_on_rescan")),
            "configured": self.metadata_refresh_configured(),
            "online": False,
            "attempted": False,
            "ran": False,
            "success": False,
            "script": str(script_path),
            "reason": "",
            "message": "",
            "detail": "",
        }

        if not bool(self.settings.get("metadata_refresh_on_rescan")):
            result["reason"] = "disabled"
            result["message"] = "Metadata refresh is disabled for rescans."
            return result
        if not script_path.exists():
            result["reason"] = "missing-script"
            result["message"] = "Metadata refresh script was not found, so the Pi used a normal rescan."
            return result
        if not self.metadata_refresh_configured():
            result["reason"] = "missing-credentials"
            result["message"] = "TMDb credentials are not configured, so the Pi used a normal rescan."
            return result

        online = self.internet_available()
        result["online"] = online
        if not online:
            result["reason"] = "offline"
            result["message"] = "The Pi is offline, so metadata refresh was skipped."
            return result

        command = [
            sys.executable,
            str(script_path),
            "--storage-root",
            str(self.settings["storage_root"]),
            "--media-root",
            str(self.settings["media_directory"]),
        ]
        result["attempted"] = True
        result["ran"] = True
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=int(self.settings["metadata_refresh_timeout_seconds"]),
                cwd=str(APP_ROOT),
                env={
                    **os.environ,
                    "NOMADSCREEN_STORAGE_ROOT": str(self.settings["storage_root"]),
                    "NOMADSCREEN_MEDIA_ROOT": str(self.settings["media_directory"]),
                },
            )
        except (OSError, subprocess.SubprocessError) as error:
            result["reason"] = "execution-error"
            result["message"] = "Metadata refresh could not be started, so the Pi used a normal rescan."
            result["detail"] = str(error)
            return result

        result["detail"] = self.summarize_process_output(completed.stdout, completed.stderr)
        if completed.returncode == 0:
            result["success"] = True
            result["message"] = "TMDb metadata refreshed before rescanning the library."
            return result

        result["reason"] = "command-failed"
        result["message"] = "Metadata refresh failed, so the Pi fell back to the normal rescan results."
        return result

    def virtual_media_path(self, actual_path: Path) -> str:
        try:
            relative_path = actual_path.resolve(strict=False).relative_to(self.media_root_path())
        except ValueError:
            return ""
        if str(relative_path) in {"", "."}:
            return DEFAULT_MEDIA_ROOT
        return normalize_virtual_path(f"{DEFAULT_MEDIA_ROOT}/{relative_path.as_posix()}")

    def resolve_virtual_path(self, virtual_path: str) -> Path | None:
        normalized = normalize_virtual_path(virtual_path)
        if not normalized or not normalized.startswith(DEFAULT_MEDIA_ROOT):
            return None
        media_root = self.media_root_path()
        relative_path = normalized[len(DEFAULT_MEDIA_ROOT) :].lstrip("/")
        candidate = (media_root / relative_path).resolve(strict=False)
        storage_text = str(media_root).lower()
        candidate_text = str(candidate).lower()
        if candidate_text != storage_text and not candidate_text.startswith(storage_text + os.sep.lower()):
            return None
        return candidate

    def load_metadata(self) -> tuple[list[dict[str, object]], dict[str, dict[str, object]], str, str]:
        item_entries: list[dict[str, object]] = []
        show_entries: dict[str, dict[str, object]] = {}
        generated_at = ""
        generator = ""

        metadata_index = self.resolve_virtual_path(DEFAULT_METADATA_INDEX_PATH)
        if metadata_index is None or not metadata_index.exists():
            return item_entries, show_entries, generated_at, generator

        try:
            raw = json.loads(metadata_index.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return item_entries, show_entries, generated_at, generator

        generated_at = str(raw.get("generatedAt") or "")
        generator = str(raw.get("generator") or "")

        for entry in raw.get("shows", []):
            if not isinstance(entry, dict):
                continue
            slug = str(entry.get("slug") or "")
            if not slug:
                continue
            show_entries[slug] = {
                "slug": slug,
                "title": str(entry.get("title") or ""),
                "year": str(entry.get("year") or ""),
                "overview": str(entry.get("overview") or ""),
                "genres": str(entry.get("genres") or ""),
                "contentRating": str(entry.get("contentRating") or ""),
                "posterPath": normalize_virtual_path(str(entry.get("posterPath") or "")),
                "backdropPath": normalize_virtual_path(str(entry.get("backdropPath") or "")),
                "metadataSource": str(entry.get("source") or ""),
                "tmdbRating": float(entry.get("tmdbRating") or 0.0),
                "matchConfidence": float(entry.get("matchConfidence") or 0.0),
            }

        for entry in raw.get("items", []):
            if not isinstance(entry, dict):
                continue
            path = normalize_virtual_path(str(entry.get("path") or ""))
            if not path:
                continue
            item_entries.append(
                {
                    "path": path,
                    "title": str(entry.get("title") or ""),
                    "sortTitle": str(entry.get("sortTitle") or ""),
                    "overview": str(entry.get("overview") or ""),
                    "tagline": str(entry.get("tagline") or ""),
                    "year": str(entry.get("year") or ""),
                    "releaseDate": str(entry.get("releaseDate") or ""),
                    "genres": str(entry.get("genres") or ""),
                    "contentRating": str(entry.get("contentRating") or ""),
                    "artist": str(entry.get("artist") or ""),
                    "album": str(entry.get("album") or ""),
                    "posterPath": normalize_virtual_path(str(entry.get("posterPath") or "")),
                    "backdropPath": normalize_virtual_path(str(entry.get("backdropPath") or "")),
                    "metadataSource": str(entry.get("source") or ""),
                    "tmdbRating": float(entry.get("tmdbRating") or 0.0),
                    "showTitle": str(entry.get("showTitle") or ""),
                    "showSlug": str(entry.get("showSlug") or ""),
                    "seasonLabel": str(entry.get("seasonLabel") or ""),
                    "runtimeMinutes": float(entry.get("runtimeMinutes") or 0.0),
                    "matchConfidence": float(entry.get("matchConfidence") or 0.0),
                    "seasonNumber": int(entry.get("seasonNumber") or 0),
                    "episodeNumber": int(entry.get("episodeNumber") or 0),
                }
            )

        return item_entries, show_entries, generated_at, generator

    def find_item_metadata(self, path: str, item_entries: list[dict[str, object]]) -> dict[str, object] | None:
        normalized_path = normalize_virtual_path(path)
        lowered_path = lowercase_copy(normalized_path)
        target_file_name = lowercase_copy(file_name_from_path(normalized_path))
        target_section = section_from_path(split_path(normalized_path), classify_media_type(normalized_path))
        fallback = None

        for entry in item_entries:
            if lowercase_copy(str(entry["path"])) == lowered_path:
                return entry
            if not target_file_name:
                continue
            if lowercase_copy(file_name_from_path(str(entry["path"]))) != target_file_name:
                continue
            entry_section = section_from_path(split_path(str(entry["path"])), classify_media_type(str(entry["path"])))
            if target_section and entry_section != target_section:
                continue
            if fallback is not None:
                return None
            fallback = entry
        return fallback

    def decorate_item(self, item: dict[str, object]) -> None:
        segments = split_path(str(item["path"]))
        item["section"] = section_from_path(segments, str(item["type"]))
        if item["section"] != "tv":
            return
        item["showTitle"] = prettify_name(segments[2]) if len(segments) >= 4 else "Unknown Show"
        item["showSlug"] = slugify(str(item["showTitle"]))
        item["seasonLabel"] = prettify_name(segments[3]) if len(segments) >= 5 else "Season 1"
        item["seasonNumber"] = parse_season_number(str(item["seasonLabel"]))
        item["episodeNumber"] = parse_episode_number(str(item["title"]))

    def apply_item_metadata(
        self,
        item: dict[str, object],
        item_entries: list[dict[str, object]],
        show_entries: dict[str, dict[str, object]],
    ) -> None:
        metadata = self.find_item_metadata(str(item["path"]), item_entries)
        if metadata is not None:
            for field in (
                "title",
                "sortTitle",
                "overview",
                "tagline",
                "year",
                "releaseDate",
                "genres",
                "contentRating",
                "artist",
                "album",
                "posterPath",
                "backdropPath",
                "metadataSource",
                "showTitle",
                "showSlug",
                "seasonLabel",
            ):
                item[field] = merge_string(str(item.get(field) or ""), str(metadata.get(field) or ""))
            for numeric_field in ("tmdbRating", "runtimeMinutes", "matchConfidence", "seasonNumber", "episodeNumber"):
                if float(metadata.get(numeric_field) or 0) > 0:
                    item[numeric_field] = metadata.get(numeric_field)
            item["hasMetadata"] = True

        if item["section"] == "tv":
            if not str(item.get("showSlug") or "") and str(item.get("showTitle") or ""):
                item["showSlug"] = slugify(str(item["showTitle"]))
            if int(item.get("seasonNumber") or 0) == 0 and str(item.get("seasonLabel") or ""):
                item["seasonNumber"] = parse_season_number(str(item["seasonLabel"]))
            if int(item.get("episodeNumber") or 0) == 0:
                item["episodeNumber"] = parse_episode_number(str(item["title"]))

            show_metadata = show_entries.get(str(item.get("showSlug") or ""))
            if show_metadata is not None:
                for field, source in (
                    ("showTitle", "title"),
                    ("year", "year"),
                    ("overview", "overview"),
                    ("genres", "genres"),
                    ("contentRating", "contentRating"),
                    ("posterPath", "posterPath"),
                    ("backdropPath", "backdropPath"),
                    ("metadataSource", "metadataSource"),
                ):
                    item[field] = merge_string(str(item.get(field) or ""), str(show_metadata.get(source) or ""))
                if float(item.get("tmdbRating") or 0) <= 0 and float(show_metadata.get("tmdbRating") or 0) > 0:
                    item["tmdbRating"] = show_metadata["tmdbRating"]
                if float(item.get("matchConfidence") or 0) <= 0 and float(show_metadata.get("matchConfidence") or 0) > 0:
                    item["matchConfidence"] = show_metadata["matchConfidence"]
                item["hasMetadata"] = True

    def scan_library(self) -> None:
        item_entries, show_entries, generated_at, generator = self.load_metadata()
        media_directory = self.media_root_path()
        scanned_items: list[dict[str, object]] = []
        storage_ready = media_directory.exists() and media_directory.is_dir()
        if not storage_ready:
            try:
                media_directory.mkdir(parents=True, exist_ok=True)
                storage_ready = media_directory.exists() and media_directory.is_dir()
            except OSError:
                storage_ready = False

        if storage_ready:
            for root, dirs, files in os.walk(media_directory):
                dirs[:] = [directory for directory in dirs if directory.lower() != ".nomadscreen"]
                for file_name in files:
                    actual_path = Path(root) / file_name
                    virtual_path = self.virtual_media_path(actual_path)
                    if not virtual_path:
                        continue
                    media_type = classify_media_type(virtual_path)
                    if not media_type:
                        continue
                    item = {
                        "title": title_from_path(virtual_path),
                        "path": normalize_virtual_path(virtual_path),
                        "type": media_type,
                        "section": "library",
                        "extension": Path(virtual_path).suffix.lstrip(".").upper(),
                        "sortTitle": title_from_path(virtual_path),
                        "overview": "",
                        "tagline": "",
                        "year": "",
                        "releaseDate": "",
                        "genres": "",
                        "contentRating": "",
                        "artist": "",
                        "album": "",
                        "posterPath": "",
                        "backdropPath": "",
                        "metadataSource": "",
                        "tmdbRating": 0.0,
                        "runtimeMinutes": 0.0,
                        "matchConfidence": 0.0,
                        "showTitle": "",
                        "showSlug": "",
                        "seasonLabel": "",
                        "seasonNumber": 0,
                        "episodeNumber": 0,
                        "hasMetadata": False,
                        "bytes": actual_path.stat().st_size if actual_path.exists() else 0,
                    }
                    self.decorate_item(item)
                    self.apply_item_metadata(item, item_entries, show_entries)
                    scanned_items.append(item)

        scanned_items.sort(
            key=lambda item: (
                SECTION_ORDER.get(str(item["section"]), 99),
                lowercase_copy(str(item.get("showTitle") or "")) if item["section"] == "tv" else "",
                int(item.get("seasonNumber") or 0) if item["section"] == "tv" else 0,
                int(item.get("episodeNumber") or 10_000) if item["section"] == "tv" else 0,
                lowercase_copy(str(item.get("sortTitle") or item["title"])),
                lowercase_copy(str(item["path"])),
            )
        )

        metadata_paths = {normalize_virtual_path(str(entry.get("path") or "")) for entry in item_entries}
        scanned_paths = {normalize_virtual_path(str(item.get("path") or "")) for item in scanned_items}
        metadata_index_stale = bool(metadata_paths) and metadata_paths != scanned_paths

        with self.lock:
            self.media_library = scanned_items
            self.item_metadata = item_entries
            self.show_metadata = show_entries
            self.metadata_generated_at = generated_at
            self.metadata_generator = generator
            self.metadata_available = bool(item_entries or show_entries)
            self.metadata_index_stale = metadata_index_stale
            self.storage_ready = storage_ready

    def count_section(self, section: str) -> int:
        return sum(1 for item in self.media_library if item["section"] == section)

    def asset_url_for_path(self, path: str) -> str:
        normalized = normalize_virtual_path(path)
        return f"/api/asset?path={quote(normalized, safe='')}" if normalized else ""

    def stream_url_for_path(self, path: str) -> str:
        normalized = normalize_virtual_path(path)
        return f"/api/stream?path={quote(normalized, safe='')}" if normalized else ""

    def serialize_media_item(self, item: dict[str, object]) -> dict[str, object]:
        payload = dict(item)
        payload["streamUrl"] = self.stream_url_for_path(str(item["path"]))
        payload["posterUrl"] = self.asset_url_for_path(str(item.get("posterPath") or ""))
        payload["backdropUrl"] = self.asset_url_for_path(str(item.get("backdropPath") or ""))
        return payload

    def build_show_library(self) -> list[dict[str, object]]:
        shows: dict[str, dict[str, object]] = {}
        for item in self.media_library:
            if item["section"] != "tv":
                continue

            slug = str(item.get("showSlug") or slugify(str(item.get("showTitle") or item["title"])))
            show = shows.get(slug)
            if show is None:
                show = {
                    "title": str(item.get("showTitle") or "Unknown Show"),
                    "slug": slug,
                    "year": "",
                    "overview": "",
                    "genres": "",
                    "contentRating": "",
                    "posterPath": "",
                    "backdropPath": "",
                    "metadataSource": "",
                    "tmdbRating": 0.0,
                    "matchConfidence": 0.0,
                    "seasons": [],
                    "_seasonMap": {},
                }
                shows[slug] = show

            for field in ("year", "overview", "genres", "contentRating", "posterPath", "backdropPath", "metadataSource"):
                if not show[field] and item.get(field):
                    show[field] = item[field]
            if float(show["tmdbRating"]) <= 0 and float(item.get("tmdbRating") or 0) > 0:
                show["tmdbRating"] = item["tmdbRating"]
            if float(show["matchConfidence"]) <= 0 and float(item.get("matchConfidence") or 0) > 0:
                show["matchConfidence"] = item["matchConfidence"]

            season_label = str(item.get("seasonLabel") or "")
            if not season_label:
                season_number = int(item.get("seasonNumber") or 1)
                season_label = "Specials" if season_number == 0 else f"Season {season_number}"
            season_key = f"{int(item.get('seasonNumber') or 0)}|{season_label.lower()}"
            season = show["_seasonMap"].get(season_key)
            if season is None:
                season = {
                    "key": season_key,
                    "label": season_label,
                    "number": int(item.get("seasonNumber") or 0),
                    "episodes": [],
                }
                show["_seasonMap"][season_key] = season
                show["seasons"].append(season)
            season["episodes"].append(item)

        ordered_shows = list(shows.values())
        ordered_shows.sort(key=lambda show: lowercase_copy(str(show["title"])))

        for show in ordered_shows:
            show["posterUrl"] = self.asset_url_for_path(str(show["posterPath"]))
            show["backdropUrl"] = self.asset_url_for_path(str(show["backdropPath"]))
            show["detailUrl"] = f"{DEFAULT_APP_PATH}/tv/{show['slug']}"
            show["seasons"].sort(key=lambda season: (int(season["number"]), lowercase_copy(str(season["label"]))))
            episode_total = 0
            for season in show["seasons"]:
                season["episodes"].sort(
                    key=lambda episode: (
                        int(episode.get("episodeNumber") or 10_000),
                        lowercase_copy(str(episode.get("sortTitle") or episode["title"])),
                    )
                )
                season["episodeCount"] = len(season["episodes"])
                season["episodes"] = [self.serialize_media_item(episode) for episode in season["episodes"]]
                episode_total += int(season["episodeCount"])
            show["seasonCount"] = len(show["seasons"])
            show["episodeCount"] = episode_total
            show.pop("_seasonMap", None)

        return ordered_shows

    def library_payload(self) -> dict[str, object]:
        shows = self.build_show_library()
        sections = {
            "movies": [],
            "tv": shows,
            "music": [],
            "audiobooks": [],
            "documents": [],
        }
        for item in self.media_library:
            if item["section"] in {"movies", "music", "audiobooks", "documents"}:
                sections[str(item["section"])].append(self.serialize_media_item(item))

        return {
            "count": len(self.media_library),
            "counts": {
                "total": len(self.media_library),
                "movies": self.count_section("movies"),
                "shows": len(shows),
                "episodes": self.count_section("tv"),
                "music": self.count_section("music"),
                "audiobooks": self.count_section("audiobooks"),
                "documents": self.count_section("documents"),
            },
            "metadata": {
                "available": self.metadata_available,
                "generatedAt": self.metadata_generated_at,
                "generator": self.metadata_generator,
                "itemCount": len(self.item_metadata),
                "showCount": len(self.show_metadata),
            },
            "sections": sections,
        }

    def upload_destinations_payload(self) -> dict[str, object]:
        paths: set[str] = set()
        roots: list[dict[str, str]] = []

        for section, config in UPLOAD_SECTION_CONFIG.items():
            root_path = normalize_virtual_path(str(config["base_path"]))
            roots.append(
                {
                    "section": section,
                    "label": str(config["label"]),
                    "path": root_path,
                }
            )
            paths.add(root_path)
            actual_root = self.resolve_virtual_path(root_path)
            if actual_root is None or not actual_root.exists() or not actual_root.is_dir():
                continue

            for root, dirs, _files in os.walk(actual_root):
                dirs[:] = [directory for directory in dirs if directory.lower() != ".nomadscreen"]
                virtual_path = self.virtual_media_path(Path(root))
                if not virtual_path:
                    continue
                normalized = normalize_virtual_path(virtual_path)
                if normalized:
                    paths.add(normalized)

        ordered_paths = sorted(
            paths,
            key=lambda path: (
                SECTION_ORDER.get(upload_section_from_destination(path), 99),
                len(split_path(path)),
                lowercase_copy(path),
            ),
        )
        return {
            "count": len(ordered_paths),
            "paths": ordered_paths,
            "roots": roots,
        }

    def status_payload(self) -> dict[str, object]:
        local_ip = self.best_local_ip()
        port = int(self.settings["http_port"])
        mdns_host = f"{self.settings['mdns_host']}.local"
        ip_app_url = self.compose_url(local_ip, port, DEFAULT_APP_PATH)
        mdns_url = self.compose_url(mdns_host, port, DEFAULT_APP_PATH)
        network = self.network_snapshot()
        current_name = str(network["current_name"])
        reported_ssid = current_name if current_name else str(network["hotspot_name"])
        return {
            "device": self.settings["device_name"],
            "ssid": reported_ssid,
            "password": str(network["current_password"]),
            "networkMode": network["mode"],
            "networkName": current_name,
            "hotspotSsid": network["hotspot_name"],
            "hotspotPassword": network["hotspot_password"],
            "wifiInterface": network["interface"],
            "fallbackApEnabled": self.settings["fallback_ap_enabled"],
            "knownWifiTimeoutSeconds": self.settings["known_wifi_timeout_seconds"],
            "ip": local_ip,
            "appUrl": mdns_url if self.settings["mdns_enabled"] else ip_app_url,
            "ipAppUrl": ip_app_url,
            "mdnsHost": mdns_host,
            "mdnsUrl": mdns_url,
            "mdnsReady": self.settings["mdns_enabled"],
            "streamPort": port,
            "streamBaseUrl": self.compose_url(local_ip, port),
            "activeStreams": self.active_streams,
            "maxStreams": self.settings["max_streams"],
            "maxClients": self.settings["max_clients"],
            "configSource": self.settings["config_source"],
            "sdMounted": self.storage_ready,
            "libraryCount": len(self.media_library),
            "mediaRoot": str(self.settings["media_directory"]),
            "mediaVirtualRoot": DEFAULT_MEDIA_ROOT,
            "uploadTempRoot": str(self.settings["upload_tmp_directory"]),
            "uploadTempReady": self.upload_temp_ready,
            "clients": self.active_client_count(),
            "lastPlayed": self.last_played_title,
            "lastPlayedType": self.last_played_type,
            "metadataAvailable": self.metadata_available,
            "metadataGeneratedAt": self.metadata_generated_at,
            "metadataGenerator": self.metadata_generator,
            "metadataItemCount": len(self.item_metadata),
            "metadataShowCount": len(self.show_metadata),
            "preferServerLibrary": self.metadata_index_stale,
            "upload": self.upload_status_payload(),
            "platform": "raspberry-pi-zero-w",
        }

    def device_config_payload(self) -> dict[str, object]:
        return {
            "deviceName": str(self.settings["device_name"]),
            "hotspotSsid": str(self.settings["ssid"]),
            "wifiPassword": str(self.settings["wifi_password"]),
            "tmdbApiKey": str(self.settings.get("tmdb_api_key") or ""),
            "tmdbBearerToken": str(self.settings.get("tmdb_bearer_token") or ""),
            "configSource": self.settings["config_source"],
        }

    def save_device_config(
        self,
        device_name: object,
        hotspot_ssid: object,
        wifi_password: object,
        tmdb_api_key: object = "",
        tmdb_bearer_token: object = "",
    ) -> dict[str, object]:
        safe_device_name = normalize_device_name(str(device_name or ""))[:MAX_DEVICE_NAME_LENGTH]
        if not safe_device_name:
            raise ValueError("Enter a server name.")

        safe_hotspot_ssid = normalize_hotspot_ssid(str(hotspot_ssid or ""))
        if not safe_hotspot_ssid:
            raise ValueError("Enter a fallback Wi-Fi name.")

        safe_wifi_password = validated_hotspot_password(str(wifi_password or ""))

        with self.lock:
            config_path = Path(self.settings["config_path"])
            raw_config, _ = read_runtime_config_file(config_path)
            raw_config["deviceName"] = safe_device_name
            raw_config.pop("serverName", None)
            raw_config["hotspotSsid"] = safe_hotspot_ssid
            raw_config.pop("accessPointSsid", None)
            raw_config["wifiPassword"] = safe_wifi_password
            raw_config["tmdbApiKey"] = str(tmdb_api_key or "").strip()
            raw_config["tmdbBearerToken"] = str(tmdb_bearer_token or "").strip()
            if isinstance(raw_config.get("wifi"), dict):
                wifi_block = dict(raw_config.get("wifi") or {})
                wifi_block["ssid"] = safe_hotspot_ssid
                wifi_block["password"] = safe_wifi_password
                raw_config["wifi"] = wifi_block
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(json.dumps(raw_config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            self.settings = load_settings()
            self.configure_upload_temp_directory()

        return {
            "ok": True,
            "message": "Saved device settings. Fallback Wi-Fi changes apply the next time the hotspot starts. TMDb credentials are used on the next online rescan.",
            "config": self.device_config_payload(),
            "status": self.status_payload(),
        }

    def find_library_item(self, path: str) -> dict[str, object] | None:
        normalized = normalize_virtual_path(path)
        for item in self.media_library:
            if item["path"] == normalized:
                return item
        return None

    def update_playback(self, item: dict[str, object]) -> None:
        with self.lock:
            self.last_played_title = str(item.get("title") or "")
            self.last_played_type = str(item.get("type") or "")
            self.last_played_at = time.time()


def add_file_headers(response: Response, cache_control: str) -> Response:
    response.headers["Cache-Control"] = cache_control
    response.headers["Accept-Ranges"] = "bytes"
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, HEAD, OPTIONS"
    response.headers["Access-Control-Expose-Headers"] = (
        "Accept-Ranges, Content-Length, Content-Range, Content-Type, Cache-Control"
    )
    return response


def no_store_json(payload: dict[str, object], status_code: int = 200) -> Response:
    response = jsonify(payload)
    response.status_code = status_code
    response.headers["Cache-Control"] = "no-store"
    return response


def plain_text_response(message: str, status_code: int) -> Response:
    response = Response(message, status=status_code, mimetype="text/plain")
    response.headers["Cache-Control"] = "no-store"
    return response


state = AppState()
app = Flask(__name__, static_folder=None)


@app.before_request
def record_request_client() -> None:
    state.record_client(request.remote_addr)


def send_app_shell() -> Response:
    return send_from_directory(STATIC_ROOT, "index.html")


def serve_storage_file(track_playback: bool) -> Response:
    if request.method == "OPTIONS":
        return add_file_headers(Response(status=204), "no-store")

    requested_path = normalize_virtual_path(str(request.args.get("path") or ""))
    actual_path = state.resolve_virtual_path(requested_path)
    if actual_path is None or not actual_path.exists() or actual_path.is_dir():
        return plain_text_response("Media file not found", 404)

    item = state.find_library_item(requested_path)
    if track_playback:
        state.begin_stream()
    try:
        response = send_file(
            actual_path,
            mimetype=guess_mime_type(requested_path),
            conditional=True,
            etag=not track_playback,
            max_age=0 if track_playback else 31536000,
        )
    except OSError:
        if track_playback:
            state.end_stream()
        return plain_text_response("Media file could not be opened", 500)

    if track_playback:
        if item is not None and request.method != "HEAD":
            state.update_playback(item)
        response.call_on_close(state.end_stream)
    return add_file_headers(response, "no-store" if track_playback else "public, max-age=31536000, immutable")


@app.get("/")
def root() -> Response:
    return redirect(DEFAULT_APP_PATH)


@app.get("/app")
@app.get("/app/")
def app_index() -> Response:
    return send_app_shell()


@app.get("/app/<path:_route>")
def app_routes(_route: str) -> Response:
    return send_app_shell()


@app.get("/index.html")
def index_html() -> Response:
    return send_app_shell()


@app.get("/styles.css")
def styles_css() -> Response:
    return send_from_directory(STATIC_ROOT, "styles.css")


@app.get("/app.js")
def app_js() -> Response:
    return send_from_directory(STATIC_ROOT, "app.js")


@app.get("/api/status")
def api_status() -> Response:
    return no_store_json(state.status_payload())


@app.get("/api/device-config")
def api_device_config_get() -> Response:
    return no_store_json({"ok": True, "config": state.device_config_payload()})


@app.post("/api/device-config")
def api_device_config() -> Response:
    payload = request.get_json(silent=True) or {}
    try:
        result = state.save_device_config(
            payload.get("deviceName"),
            payload.get("hotspotSsid") or payload.get("wifiName"),
            payload.get("wifiPassword"),
            payload.get("tmdbApiKey"),
            payload.get("tmdbBearerToken"),
        )
    except ValueError as error:
        return no_store_json({"error": str(error)}, 400)
    except OSError:
        return no_store_json({"error": "Could not save the device settings file."}, 500)
    return no_store_json(result)


@app.post("/api/upload-progress")
def api_upload_progress() -> Response:
    payload = request.get_json(silent=True) or {}
    upload_id = normalize_upload_id(payload.get("uploadId"))
    if not upload_id:
        return no_store_json({"error": "Missing uploadId"}, 400)

    upload = state.set_upload_status(
        upload_id,
        active=lowercase_copy(str(payload.get("phase") or "uploading")) not in {"completed", "error", "idle"},
        phase=str(payload.get("phase") or "uploading"),
        destination=str(payload.get("destination") or ""),
        section=str(payload.get("section") or ""),
        folder=str(payload.get("folder") or ""),
        file_count=safe_int(payload.get("fileCount"), 0, 0),
        uploaded_count=safe_int(payload.get("uploadedCount"), 0, 0),
        bytes_sent=safe_int(payload.get("bytesSent"), 0, 0),
        bytes_total=safe_int(payload.get("bytesTotal"), 0, 0),
        message=str(payload.get("message") or ""),
        error=str(payload.get("error") or ""),
    )
    return no_store_json({"ok": True, "upload": upload}, 202)


@app.get("/api/library")
def api_library() -> Response:
    return no_store_json(state.library_payload())


@app.get("/api/upload-destinations")
def api_upload_destinations() -> Response:
    return no_store_json(state.upload_destinations_payload())


@app.route("/api/stream", methods=["GET", "HEAD", "OPTIONS"])
def api_stream() -> Response:
    if not state.storage_ready:
        return plain_text_response("Media storage unavailable", 503)
    return serve_storage_file(track_playback=True)


@app.route("/api/asset", methods=["GET", "HEAD", "OPTIONS"])
def api_asset() -> Response:
    if not state.storage_ready:
        return plain_text_response("Media storage unavailable", 503)
    return serve_storage_file(track_playback=False)


@app.post("/api/upload")
def api_upload() -> Response:
    upload_id = normalize_upload_id(request.form.get("uploadId")) or f"upload-{int(time.time() * 1000)}"
    raw_destination = str(request.form.get("destination") or "")
    destination = normalize_upload_destination(raw_destination)
    if not destination:
        legacy_section = lowercase_copy(str(request.form.get("section") or ""))
        legacy_folder = str(request.form.get("folder") or "")
        destination = build_upload_destination_path(legacy_section, legacy_folder)

    section = upload_section_from_destination(destination)
    section_config = UPLOAD_SECTION_CONFIG.get(section)
    if not destination or section_config is None:
        allowed_roots = ", ".join(str(config["base_path"]) for config in UPLOAD_SECTION_CONFIG.values())
        state.set_upload_status(
            upload_id,
            phase="error",
            destination=raw_destination,
            message="Upload failed. Pick a destination under one of the library roots.",
            error=f"Choose a destination inside {allowed_roots}",
        )
        return no_store_json({"error": f"Choose a destination inside {allowed_roots}"}, 400)

    files = [uploaded for uploaded in request.files.getlist("files") if str(uploaded.filename or "").strip()]
    if not files:
        state.set_upload_status(
            upload_id,
            phase="error",
            destination=destination,
            section=section,
            message="Upload failed. Choose at least one file first.",
            error="Choose at least one file to upload",
        )
        return no_store_json({"error": "Choose at least one file to upload"}, 400)

    relative_paths = request.form.getlist("relativePaths")
    normalized_folder = upload_folder_from_destination(destination, section)
    current_upload = state.upload_status_payload()
    known_total_bytes = (
        int(current_upload.get("bytesTotal") or 0) if str(current_upload.get("id") or "") == upload_id else 0
    )
    state.set_upload_status(
        upload_id,
        active=True,
        phase="processing",
        destination=destination,
        section=section,
        folder=normalized_folder,
        file_count=len(files),
        uploaded_count=0,
        bytes_sent=known_total_bytes,
        bytes_total=known_total_bytes,
        message="Transfer finished. Saving files on the Pi...",
        error="",
        warnings=[],
    )
    uploaded_items: list[dict[str, object]] = []
    warnings: list[str] = []
    saved_bytes = 0

    for index, uploaded in enumerate(files):
        requested_relative_path = (
            str(relative_paths[index])
            if index < len(relative_paths) and str(relative_paths[index] or "").strip()
            else str(uploaded.filename or "")
        )
        file_name = sanitize_upload_filename(Path(requested_relative_path).name) or sanitize_upload_filename(
            str(uploaded.filename or "")
        )
        if not file_name:
            warnings.append("Skipped a file with an invalid name.")
            continue

        virtual_path = build_upload_virtual_path_from_destination(destination, requested_relative_path, file_name)
        if not virtual_path:
            warnings.append(f"Skipped {file_name}: invalid destination.")
            continue

        media_type = classify_media_type(virtual_path)
        allowed_types = set(section_config["media_types"])
        if media_type not in allowed_types:
            warnings.append(f"Skipped {file_name}: unsupported file type for {section_config['label']}.")
            continue

        actual_path = state.resolve_virtual_path(virtual_path)
        if actual_path is None:
            warnings.append(f"Skipped {file_name}: destination could not be resolved.")
            continue

        try:
            actual_path.parent.mkdir(parents=True, exist_ok=True)
            final_path = ensure_unique_path(actual_path)
            uploaded.save(final_path)
            saved_bytes += final_path.stat().st_size
            final_virtual_path = state.virtual_media_path(final_path)
            if not final_virtual_path:
                warnings.append(f"Skipped {file_name}: destination could not be resolved after save.")
                continue
            uploaded_items.append(
                {
                    "name": final_path.name,
                    "path": normalize_virtual_path(final_virtual_path),
                    "bytes": final_path.stat().st_size,
                    "section": section,
                    "type": media_type,
                }
            )
            processing_bytes = known_total_bytes or saved_bytes
            state.set_upload_status(
                upload_id,
                active=True,
                phase="processing",
                destination=destination,
                section=section,
                folder=normalized_folder,
                file_count=len(files),
                uploaded_count=len(uploaded_items),
                bytes_sent=processing_bytes,
                bytes_total=processing_bytes,
                message=f"Saving files on the Pi ({len(uploaded_items)}/{len(files)})...",
                warnings=warnings,
            )
        except OSError as error:
            error_message = str(getattr(error, "strerror", "") or error).strip()
            warnings.append(f"Failed to save {file_name}{': ' + error_message if error_message else '.'}")

    if not uploaded_items:
        state.set_upload_status(
            upload_id,
            phase="error",
            destination=destination,
            section=section,
            folder=normalized_folder,
            file_count=len(files),
            uploaded_count=0,
            bytes_sent=known_total_bytes,
            bytes_total=known_total_bytes,
            message="Upload failed before any files were saved.",
            error="No files were uploaded",
            warnings=warnings,
        )
        return no_store_json({"error": "No files were uploaded", "warnings": warnings}, 400)

    processing_bytes = known_total_bytes or saved_bytes
    state.set_upload_status(
        upload_id,
        active=True,
        phase="processing",
        destination=destination,
        section=section,
        folder=normalized_folder,
        file_count=len(files),
        uploaded_count=len(uploaded_items),
        bytes_sent=processing_bytes,
        bytes_total=processing_bytes,
        message="Upload complete. Rescanning the library...",
        warnings=warnings,
    )
    state.scan_library()
    upload_status = state.set_upload_status(
        upload_id,
        phase="completed",
        destination=destination,
        section=section,
        folder=normalized_folder,
        file_count=len(files),
        uploaded_count=len(uploaded_items),
        bytes_sent=processing_bytes,
        bytes_total=processing_bytes,
        message=f"Uploaded {len(uploaded_items)} file{'s' if len(uploaded_items) != 1 else ''}.",
        error="",
        warnings=warnings,
    )
    return no_store_json(
        {
            "ok": True,
            "uploadId": upload_id,
            "destination": destination,
            "section": section,
            "folder": normalized_folder,
            "count": len(uploaded_items),
            "savedBytes": saved_bytes,
            "mediaRoot": str(state.settings["media_directory"]),
            "uploaded": uploaded_items,
            "warnings": warnings,
            "upload": upload_status,
        },
        201,
    )


@app.post("/api/rescan")
def api_rescan() -> Response:
    metadata_refresh = state.run_metadata_refresh_on_rescan()
    state.scan_library()
    message = "Library rescanned."
    if metadata_refresh.get("message"):
        if bool(metadata_refresh.get("success")):
            message = str(metadata_refresh["message"])
        elif str(metadata_refresh.get("reason") or "") == "offline":
            message = "Library rescanned. The Pi is offline, so TMDb metadata refresh was skipped."
        elif str(metadata_refresh.get("reason") or "") == "missing-credentials":
            message = "Library rescanned. Add TMDb credentials to enable metadata refresh during rescans."
        elif str(metadata_refresh.get("reason") or "") == "disabled":
            message = "Library rescanned without running the metadata refresh command."
        else:
            message = f"{str(metadata_refresh['message'])} Library scan still completed."
    return no_store_json({"ok": True, "message": message, "metadataRefresh": metadata_refresh})


@app.errorhandler(404)
def not_found(_error: Exception) -> Response:
    if request.path == DEFAULT_APP_PATH or request.path.startswith(DEFAULT_APP_PATH + "/"):
        return send_app_shell()
    return plain_text_response("Not found", 404)


if __name__ == "__main__":
    app.run(
        host=str(state.settings["bind_address"]),
        port=int(state.settings["http_port"]),
        threaded=True,
    )
