from __future__ import annotations

import json
import mimetypes
import os
import queue
import re
import secrets
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from urllib.parse import quote

from flask import Flask, Response, jsonify, redirect, request, send_file, send_from_directory

from audiobook_metadata import extract_audiobook_embedded_metadata


APP_ROOT = Path(__file__).resolve().parents[1]
STATIC_ROOT = APP_ROOT / "data"
DEFAULT_STORAGE_ROOT = APP_ROOT / ".nomadscreen-runtime"

DEFAULT_DEVICE_NAME = "Backcountry Broadcast"
DEFAULT_MDNS_HOST = "backcountrybroadcast"
DEFAULT_ACCESS_POINT_SSID = "BackcountryBroadcast"
DEFAULT_ACCESS_POINT_PASSWORD = "backpackingmedia"
DEFAULT_WIFI_INTERFACE = "wlan0"
DEFAULT_KNOWN_WIFI_TIMEOUT_SECONDS = 20
DEFAULT_APP_PATH = "/app"
DEFAULT_MEDIA_ROOT = "/media"
DEFAULT_METADATA_ROOT = "/media/.nomadscreen"
DEFAULT_METADATA_INDEX_PATH = "/media/.nomadscreen/library.json"
DEFAULT_CATALOG_DB_PATH = "/media/.nomadscreen/library.db"
DEFAULT_RUNTIME_CONFIG_PATH = "/nomadscreen.config.json"
DEFAULT_METADATA_REFRESH_SCRIPT = APP_ROOT / "tools" / "nomadscreen_refresh_metadata.py"
DEFAULT_BIND_ADDRESS = "0.0.0.0"
DEFAULT_HTTP_PORT = 80
DEFAULT_MAX_CLIENTS = 6
DEFAULT_MAX_STREAMS = 12
DEFAULT_CLIENT_WINDOW_SECONDS = 300
DEFAULT_METADATA_REFRESH_TIMEOUT_SECONDS = 1800
DEFAULT_FILEBROWSER_PORT = 8081
DEVICE_AUTH_COOKIE_NAME = "nomadscreen_device_auth"
DEVICE_AUTH_SESSION_SECONDS = 12 * 60 * 60
MIN_DEVICE_PAGE_PASSWORD_LENGTH = 4
MAX_DEVICE_PAGE_PASSWORD_LENGTH = 128
MAX_DEVICE_NAME_LENGTH = 80
MAX_HOTSPOT_SSID_LENGTH = 32
MIN_HOTSPOT_PASSWORD_LENGTH = 8
MAX_HOTSPOT_PASSWORD_LENGTH = 63
DEFAULT_CATALOG_PAGE_SIZE = 40
MAX_CATALOG_PAGE_SIZE = 80
DEFAULT_HOME_MOVIE_LIMIT = 8
DEFAULT_HOME_SHOW_LIMIT = 8
DEFAULT_HOME_MUSIC_LIMIT = 6
DEFAULT_HOME_AUDIOBOOK_LIMIT = 6
DEFAULT_HOME_DOCUMENT_LIMIT = 6
DEFAULT_SEARCH_RESULT_LIMIT = 60
MAX_CATALOG_LOOKUP_PATHS = 200

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


def validated_device_page_password(value: str) -> str:
    password = normalize_hotspot_password(value)
    if len(password) < MIN_DEVICE_PAGE_PASSWORD_LENGTH or len(password) > MAX_DEVICE_PAGE_PASSWORD_LENGTH:
        raise ValueError(
            f"Device page password must be {MIN_DEVICE_PAGE_PASSWORD_LENGTH}-{MAX_DEVICE_PAGE_PASSWORD_LENGTH} characters."
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


def ensure_table_columns(connection: sqlite3.Connection, table_name: str, columns: dict[str, str]) -> None:
    existing = {
        str(row[1]).lower()
        for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    }
    for column_name, definition in columns.items():
        if column_name.lower() in existing:
            continue
        connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")


def configure_sqlite_connection(connection: sqlite3.Connection) -> sqlite3.Connection:
    connection.execute("PRAGMA busy_timeout = 5000")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = FULL")
    connection.execute("PRAGMA wal_autocheckpoint = 200")
    connection.create_function("audiobook_collection_key", 3, audiobook_collection_key)
    connection.create_function("audiobook_series_sort_rank", 3, audiobook_series_sort_rank)
    connection.create_function("audiobook_series_sort_number", 3, audiobook_series_sort_number)
    return connection


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


def slugify_text(value: object, fallback: str = "unknown-show") -> str:
    output = []
    previous_dash = False
    for character in unicodedata.normalize("NFKD", str(value or "")):
        if unicodedata.combining(character):
            continue
        if character.isascii() and character.isalnum():
            output.append(character.lower())
            previous_dash = False
        elif output and not previous_dash:
            output.append("-")
            previous_dash = True
    return "".join(output).strip("-") or fallback


def normalize_audiobook_collection_label(value: object) -> str:
    normalized = normalize_catalog_query(value)
    if not normalized:
        return ""
    cleaned = normalize_catalog_query(re.sub(r"\s+#\d+(?:\.\d+)?\s*$", "", normalized))
    return cleaned or normalized


def audiobook_embedded_series_index(value: object) -> str:
    match = re.search(r"\s+#(\d+(?:\.\d+)?)\s*$", normalize_catalog_query(value))
    return normalize_catalog_query(match.group(1)) if match else ""


def audiobook_series_index_value(series_index: object, series_name: object, album: object) -> str:
    embedded_value = audiobook_embedded_series_index(series_name) or audiobook_embedded_series_index(album)
    if embedded_value:
        return normalize_catalog_query(embedded_value)
    direct_value = normalize_catalog_query(series_index)
    if direct_value:
        direct_match = re.search(r"(\d+(?:\.\d+)?)", direct_value)
        return normalize_catalog_query(direct_match.group(1)) if direct_match else direct_value
    return ""


def audiobook_series_sort_rank(series_index: object, series_name: object, album: object) -> int:
    return 0 if audiobook_series_index_value(series_index, series_name, album) else 1


def audiobook_series_sort_number(series_index: object, series_name: object, album: object) -> float:
    normalized = audiobook_series_index_value(series_index, series_name, album)
    if not normalized:
        return 0.0
    try:
        return float(normalized)
    except ValueError:
        return 0.0


def relative_media_path(path: object) -> str:
    normalized = normalize_virtual_path(str(path or ""))
    if normalized.startswith("/media/"):
        return normalized[len("/media/") :]
    return normalized.lstrip("/")


def audiobook_path_segments(path: object) -> list[str]:
    segments = [segment.strip() for segment in relative_media_path(path).split("/") if segment.strip()]
    if segments and lowercase_copy(segments[0]) == "audiobooks":
        return segments[1:]
    return segments


def audiobook_folder_segments(path: object) -> list[str]:
    segments = audiobook_path_segments(path)
    return segments[:-1] if len(segments) > 1 else []


def audiobook_collection_name(series_name: object, album: object, path: object) -> str:
    safe_series_name = normalize_audiobook_collection_label(series_name)
    if safe_series_name:
        return safe_series_name
    safe_album = normalize_audiobook_collection_label(album)
    if safe_album:
        return safe_album
    folders = audiobook_folder_segments(path)
    if len(folders) >= 2:
        return normalize_audiobook_collection_label(title_from_path(folders[1]))
    return ""


def audiobook_collection_key(series_name: object, album: object, path: object) -> str:
    return slugify_text(audiobook_collection_name(series_name, album, path))


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


def safe_float(value: object, default: float, minimum: float = 0.0) -> float:
    try:
        parsed = float(value)
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


def atomic_save_upload(uploaded_file: object, destination_path: Path, staging_root: Path) -> Path:
    staging_root.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=".nomadscreen-upload-", suffix=".part", dir=str(staging_root))
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            uploaded_file.save(handle)
            handle.flush()
            os.fsync(handle.fileno())
        final_path = ensure_unique_path(destination_path)
        os.replace(temp_path, final_path)
        fsync_directory(final_path.parent)
        return final_path
    except Exception:
        try:
            temp_path.unlink()
        except OSError:
            pass
        raise


def playback_client_key(remote_address: str | None) -> str:
    safe_address = str(remote_address or "").strip()
    return f"device-ip:{safe_address}" if safe_address else "device-ip:unknown"


def normalize_iso_timestamp(value: object) -> str:
    safe_value = str(value or "").strip()
    return safe_value if safe_value else iso_timestamp_now()


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


def sanitize_status_line(raw_value: object, max_chars: int = 320) -> str:
    value = " ".join(str(raw_value or "").replace("\x00", "").split()).strip()
    return value[:max_chars]


def catalog_search_text(parts: list[object]) -> str:
    return " ".join(str(part or "").strip() for part in parts if str(part or "").strip())


def normalize_catalog_query(raw_value: object) -> str:
    return " ".join(str(raw_value or "").split()).strip()


def normalize_catalog_genre(raw_value: object) -> str:
    return " ".join(str(raw_value or "").split()).strip()


def normalize_catalog_limit(raw_value: object, default: int = DEFAULT_CATALOG_PAGE_SIZE) -> int:
    try:
        parsed = int(raw_value)
    except (TypeError, ValueError):
        return default
    return max(1, min(MAX_CATALOG_PAGE_SIZE, parsed))


def normalize_catalog_offset(raw_value: object) -> int:
    try:
        parsed = int(raw_value)
    except (TypeError, ValueError):
        return 0
    return max(0, parsed)


def catalog_like_pattern(raw_query: object) -> str:
    normalized = normalize_catalog_query(raw_query).lower()
    return f"%{normalized}%" if normalized else "%"


def split_catalog_genres(raw_value: object) -> list[str]:
    known: set[str] = set()
    values: list[str] = []
    for part in str(raw_value or "").split(","):
        genre = " ".join(part.split()).strip()
        if not genre:
            continue
        key = genre.casefold()
        if key in known:
            continue
        known.add(key)
        values.append(genre)
    return values


def catalog_genre_pattern(raw_value: object) -> str:
    normalized = normalize_catalog_genre(raw_value).lower()
    return f"%,{normalized},%" if normalized else "%"


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
    device_password = normalize_hotspot_password(str(raw_config.get("devicePassword") or ""))
    if device_password and (
        len(device_password) < MIN_DEVICE_PAGE_PASSWORD_LENGTH or len(device_password) > MAX_DEVICE_PAGE_PASSWORD_LENGTH
    ):
        device_password = ""
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
    filebrowser_port = safe_int(
        os.environ.get("NOMADSCREEN_FILEBROWSER_PORT") or raw_config.get("fileBrowserPort"),
        DEFAULT_FILEBROWSER_PORT,
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
        "device_password": device_password,
        "wifi_interface": wifi_interface or DEFAULT_WIFI_INTERFACE,
        "known_wifi_timeout_seconds": known_wifi_timeout_seconds,
        "fallback_ap_enabled": fallback_ap_enabled,
        "mdns_host": mdns_host,
        "mdns_enabled": env_bool("NOMADSCREEN_MDNS", bool(raw_config.get("mdnsEnabled", False))),
        "bind_address": bind_address,
        "http_port": http_port,
        "filebrowser_port": filebrowser_port,
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
        self.show_count = 0
        self.upload_status = self.default_upload_status()
        self.metadata_refresh_status = self.default_metadata_refresh_status()
        self.device_auth_sessions: dict[str, float] = {}
        self.prepare_upload_staging_directory()
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

    def default_metadata_refresh_status(self) -> dict[str, object]:
        return {
            "id": "",
            "active": False,
            "phase": "idle",
            "message": "",
            "error": "",
            "reason": "",
            "detail": "",
            "enabled": bool(self.settings.get("metadata_refresh_on_rescan")),
            "configured": self.metadata_refresh_configured(),
            "online": False,
            "attempted": False,
            "success": False,
            "script": str(self.metadata_refresh_script_path()),
            "recentLines": [],
            "outputLineCount": 0,
            "exitCode": None,
            "startedAt": "",
            "updatedAt": "",
            "completedAt": "",
        }

    def metadata_refresh_status_payload(self) -> dict[str, object]:
        with self.lock:
            payload = dict(self.metadata_refresh_status)
        payload["recentLines"] = list(payload.get("recentLines") or [])
        return payload

    def device_access_password(self) -> str:
        return str(self.settings.get("device_password") or self.settings.get("wifi_password") or "").strip()

    def device_access_uses_dedicated_password(self) -> bool:
        return bool(str(self.settings.get("device_password") or "").strip())

    def cleanup_device_auth_sessions(self, now: float | None = None) -> None:
        cutoff = float(now or time.time())
        for token, expires_at in list(self.device_auth_sessions.items()):
            if float(expires_at) <= cutoff:
                self.device_auth_sessions.pop(token, None)

    def is_device_authenticated(self, token: object) -> bool:
        safe_token = str(token or "").strip()
        if not safe_token:
            return False
        with self.lock:
            self.cleanup_device_auth_sessions()
            expires_at = self.device_auth_sessions.get(safe_token)
            if not expires_at:
                return False
            if float(expires_at) <= time.time():
                self.device_auth_sessions.pop(safe_token, None)
                return False
            return True

    def create_device_auth_session(self) -> tuple[str, int]:
        token = secrets.token_urlsafe(32)
        expires_at = int(time.time()) + DEVICE_AUTH_SESSION_SECONDS
        with self.lock:
            self.cleanup_device_auth_sessions()
            self.device_auth_sessions[token] = float(expires_at)
        return token, DEVICE_AUTH_SESSION_SECONDS

    def clear_device_auth_session(self, token: object) -> None:
        safe_token = str(token or "").strip()
        if not safe_token:
            return
        with self.lock:
            self.device_auth_sessions.pop(safe_token, None)

    def set_metadata_refresh_status(
        self,
        refresh_id: str,
        *,
        active: bool | None = None,
        phase: str | None = None,
        message: str | None = None,
        error: str | None = None,
        reason: str | None = None,
        detail: str | None = None,
        enabled: bool | None = None,
        configured: bool | None = None,
        online: bool | None = None,
        attempted: bool | None = None,
        success: bool | None = None,
        script: str | None = None,
        recent_lines: list[str] | None = None,
        append_line: str | None = None,
        exit_code: int | None = None,
    ) -> dict[str, object]:
        safe_refresh_id = normalize_upload_id(refresh_id) or f"metadata-{int(time.time() * 1000)}"
        safe_phase = lowercase_copy(phase) if phase is not None else None
        if safe_phase not in {None, "idle", "skipped", "preparing", "running", "completed", "error"}:
            safe_phase = "running"
        safe_message = sanitize_status_line(message, 240) if message is not None else None
        safe_error = sanitize_status_line(error, 240) if error is not None else None
        safe_reason = sanitize_status_line(reason, 80) if reason is not None else None
        safe_detail = str(detail or "").replace("\x00", "").strip()[:1200] if detail is not None else None
        safe_script = str(script or "").strip()[:260] if script is not None else None
        safe_recent_lines = (
            [line for line in (sanitize_status_line(entry) for entry in recent_lines) if line][-12:]
            if recent_lines is not None
            else None
        )
        safe_append_line = sanitize_status_line(append_line) if append_line is not None else None
        now = iso_timestamp_now()

        with self.lock:
            current = dict(self.metadata_refresh_status)
            if str(current.get("id") or "") != safe_refresh_id:
                current = self.default_metadata_refresh_status()
                current["id"] = safe_refresh_id
                current["startedAt"] = now
            elif not str(current.get("startedAt") or ""):
                current["startedAt"] = now

            if safe_phase is not None:
                current["phase"] = safe_phase
            if safe_message is not None:
                current["message"] = safe_message
            if safe_error is not None:
                current["error"] = safe_error
            if safe_reason is not None:
                current["reason"] = safe_reason
            if safe_detail is not None:
                current["detail"] = safe_detail
            if enabled is not None:
                current["enabled"] = bool(enabled)
            if configured is not None:
                current["configured"] = bool(configured)
            if online is not None:
                current["online"] = bool(online)
            if attempted is not None:
                current["attempted"] = bool(attempted)
            if success is not None:
                current["success"] = bool(success)
            if safe_script is not None:
                current["script"] = safe_script
            if safe_recent_lines is not None:
                current["recentLines"] = safe_recent_lines
                current["outputLineCount"] = len(safe_recent_lines)
            if safe_append_line:
                recent_lines_buffer = list(current.get("recentLines") or [])
                recent_lines_buffer.append(safe_append_line)
                current["recentLines"] = recent_lines_buffer[-12:]
                current["outputLineCount"] = int(current.get("outputLineCount") or 0) + 1
                if not current.get("detail"):
                    current["detail"] = "\n".join(current["recentLines"])
            if exit_code is not None:
                current["exitCode"] = int(exit_code)

            if not str(current.get("detail") or "").strip() and current.get("recentLines"):
                current["detail"] = "\n".join(list(current["recentLines"])[-8:])

            if active is not None:
                current["active"] = bool(active)
            else:
                current["active"] = current["phase"] in {"preparing", "running"}
            if current["phase"] in {"idle", "skipped", "completed", "error"}:
                current["active"] = False

            if current["phase"] in {"skipped", "completed", "error"}:
                current["completedAt"] = now
            elif current["phase"] != "idle":
                current["completedAt"] = ""

            current["updatedAt"] = now
            self.metadata_refresh_status = current
            return self.metadata_refresh_status_payload()

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

    def file_manager_password_path(self) -> Path:
        return (Path(self.settings["storage_root"]) / "filebrowser" / "admin-password.txt").resolve(strict=False)

    def file_manager_password(self) -> str:
        password_path = self.file_manager_password_path()
        try:
            with password_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    value = line.strip()
                    if value:
                        return value
        except OSError:
            return ""
        return ""

    def file_manager_status_payload(self, local_ip: str, mdns_host: str) -> dict[str, object]:
        port = safe_int(self.settings.get("filebrowser_port"), DEFAULT_FILEBROWSER_PORT, 1)
        ip_url = self.compose_url(local_ip, port)
        mdns_url = self.compose_url(mdns_host, port)
        password = self.file_manager_password()
        return {
            "username": "admin",
            "password": password,
            "passwordAvailable": bool(password),
            "passwordPath": str(self.file_manager_password_path()) if password else "",
            "port": port,
            "url": mdns_url if self.settings["mdns_enabled"] else ip_url,
            "ipUrl": ip_url,
            "mdnsUrl": mdns_url,
            "root": str(self.media_root_path()),
        }

    def device_auth_payload(self, authenticated: bool) -> dict[str, object]:
        return {
            "required": True,
            "authenticated": bool(authenticated),
            "usesDedicatedPassword": self.device_access_uses_dedicated_password(),
        }

    def media_root_path(self) -> Path:
        return Path(self.settings["media_directory"]).resolve(strict=False)

    def metadata_root_path(self) -> Path:
        return (self.media_root_path() / ".nomadscreen").resolve(strict=False)

    def upload_staging_root_path(self) -> Path:
        return (self.metadata_root_path() / "uploads").resolve(strict=False)

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

    def prepare_upload_staging_directory(self) -> None:
        staging_root = self.upload_staging_root_path()
        try:
            staging_root.mkdir(parents=True, exist_ok=True)
        except OSError:
            return
        for candidate in staging_root.glob(".nomadscreen-upload-*.part"):
            try:
                candidate.unlink()
            except OSError:
                continue

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
        refresh_id = f"metadata-{int(time.time() * 1000)}"
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
        self.set_metadata_refresh_status(
            refresh_id,
            phase="preparing",
            enabled=bool(result["enabled"]),
            configured=bool(result["configured"]),
            online=False,
            attempted=False,
            success=False,
            script=str(script_path),
            recent_lines=[],
            detail="",
            message="Checking connectivity before starting the TMDb metadata refresh...",
            error="",
            reason="",
        )

        if not bool(self.settings.get("metadata_refresh_on_rescan")):
            result["reason"] = "disabled"
            result["message"] = "Metadata refresh is disabled for rescans."
            self.set_metadata_refresh_status(
                refresh_id,
                phase="skipped",
                enabled=False,
                configured=bool(result["configured"]),
                online=False,
                attempted=False,
                success=False,
                message=result["message"],
                reason=result["reason"],
                error="",
            )
            return result
        if not script_path.exists():
            result["reason"] = "missing-script"
            result["message"] = "Metadata refresh script was not found, so the Pi used a normal rescan."
            self.set_metadata_refresh_status(
                refresh_id,
                phase="error",
                enabled=bool(result["enabled"]),
                configured=bool(result["configured"]),
                online=False,
                attempted=False,
                success=False,
                message=result["message"],
                reason=result["reason"],
                error="Metadata refresh script not found.",
            )
            return result
        if not self.metadata_refresh_configured():
            result["reason"] = "missing-credentials"
            result["message"] = "TMDb credentials are not configured, so the Pi used a normal rescan."
            self.set_metadata_refresh_status(
                refresh_id,
                phase="skipped",
                enabled=bool(result["enabled"]),
                configured=False,
                online=False,
                attempted=False,
                success=False,
                message=result["message"],
                reason=result["reason"],
                error="",
            )
            return result

        online = self.internet_available()
        result["online"] = online
        if not online:
            result["reason"] = "offline"
            result["message"] = "The Pi is offline, so metadata refresh was skipped."
            self.set_metadata_refresh_status(
                refresh_id,
                phase="skipped",
                enabled=bool(result["enabled"]),
                configured=True,
                online=False,
                attempted=False,
                success=False,
                message=result["message"],
                reason=result["reason"],
                error="",
            )
            return result

        command = [
            sys.executable,
            "-u",
            str(script_path),
            "--storage-root",
            str(self.settings["storage_root"]),
            "--media-root",
            str(self.settings["media_directory"]),
        ]
        result["attempted"] = True
        result["ran"] = True
        self.set_metadata_refresh_status(
            refresh_id,
            phase="running",
            enabled=bool(result["enabled"]),
            configured=True,
            online=True,
            attempted=True,
            success=False,
            script=str(script_path),
            recent_lines=[],
            detail="",
            message="TMDb metadata refresh is running on the Pi now...",
            error="",
            reason="",
        )
        output_lines: list[str] = []
        try:
            completed = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                cwd=str(APP_ROOT),
                env={
                    **os.environ,
                    "NOMADSCREEN_STORAGE_ROOT": str(self.settings["storage_root"]),
                    "NOMADSCREEN_MEDIA_ROOT": str(self.settings["media_directory"]),
                    "PYTHONUNBUFFERED": "1",
                },
            )
        except (OSError, subprocess.SubprocessError) as error:
            result["reason"] = "execution-error"
            result["message"] = "Metadata refresh could not be started, so the Pi used a normal rescan."
            result["detail"] = str(error)
            self.set_metadata_refresh_status(
                refresh_id,
                phase="error",
                enabled=bool(result["enabled"]),
                configured=True,
                online=True,
                attempted=True,
                success=False,
                message=result["message"],
                reason=result["reason"],
                error=sanitize_status_line(str(error), 240),
                detail=result["detail"],
            )
            return result

        line_queue: queue.Queue[str | None] = queue.Queue()
        timeout_seconds = max(int(self.settings["metadata_refresh_timeout_seconds"]), 1)
        deadline = time.monotonic() + timeout_seconds

        def read_process_output() -> None:
            stream = completed.stdout
            if stream is None:
                line_queue.put(None)
                return
            try:
                for raw_line in stream:
                    line_queue.put(raw_line)
            finally:
                try:
                    stream.close()
                except OSError:
                    pass
                line_queue.put(None)

        reader_thread = threading.Thread(target=read_process_output, daemon=True)
        reader_thread.start()
        stream_closed = False
        timed_out = False

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            try:
                raw_line = line_queue.get(timeout=min(0.25, max(remaining, 0.05)))
            except queue.Empty:
                raw_line = None
            if raw_line is None:
                if completed.poll() is not None:
                    stream_closed = True
                if stream_closed and line_queue.empty():
                    break
            else:
                clean_line = sanitize_status_line(raw_line)
                if clean_line:
                    output_lines.append(clean_line)
                    self.set_metadata_refresh_status(
                        refresh_id,
                        phase="running",
                        enabled=bool(result["enabled"]),
                        configured=True,
                        online=True,
                        attempted=True,
                        success=False,
                        message="TMDb metadata refresh is running on the Pi now...",
                        append_line=clean_line,
                    )
            if completed.poll() is not None and line_queue.empty():
                break

        if timed_out:
            completed.kill()
            try:
                completed.wait(timeout=2)
            except subprocess.SubprocessError:
                pass
            reader_thread.join(timeout=1.0)
            result["reason"] = "timeout"
            result["message"] = "Metadata refresh timed out, so the Pi fell back to the normal rescan results."
            result["detail"] = "\n".join(output_lines[-8:])
            self.set_metadata_refresh_status(
                refresh_id,
                phase="error",
                enabled=bool(result["enabled"]),
                configured=True,
                online=True,
                attempted=True,
                success=False,
                message=result["message"],
                reason=result["reason"],
                error=f"Timed out after {timeout_seconds} seconds.",
                detail=result["detail"],
            )
            return result

        return_code = completed.wait()
        reader_thread.join(timeout=1.0)
        while True:
            try:
                raw_line = line_queue.get_nowait()
            except queue.Empty:
                break
            if raw_line is None:
                continue
            clean_line = sanitize_status_line(raw_line)
            if clean_line:
                output_lines.append(clean_line)
                self.set_metadata_refresh_status(
                    refresh_id,
                    phase="running",
                    enabled=bool(result["enabled"]),
                    configured=True,
                    online=True,
                    attempted=True,
                    success=False,
                    message="TMDb metadata refresh is running on the Pi now...",
                    append_line=clean_line,
                )

        result["detail"] = "\n".join(output_lines[-8:])
        if return_code == 0:
            result["success"] = True
            result["message"] = "TMDb metadata refreshed before rescanning the library."
            self.set_metadata_refresh_status(
                refresh_id,
                phase="completed",
                enabled=bool(result["enabled"]),
                configured=True,
                online=True,
                attempted=True,
                success=True,
                message=result["message"],
                reason="",
                error="",
                detail=result["detail"],
                exit_code=return_code,
            )
            return result

        result["reason"] = "command-failed"
        result["message"] = "Metadata refresh failed, so the Pi fell back to the normal rescan results."
        self.set_metadata_refresh_status(
            refresh_id,
            phase="error",
            enabled=bool(result["enabled"]),
            configured=True,
            online=True,
            attempted=True,
            success=False,
            message=result["message"],
            reason=result["reason"],
            error=f"Command exited with status {return_code}.",
            detail=result["detail"],
            exit_code=return_code,
        )
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
                    "narrators": str(entry.get("narrators") or ""),
                    "publisher": str(entry.get("publisher") or ""),
                    "language": str(entry.get("language") or ""),
                    "tags": str(entry.get("tags") or ""),
                    "seriesName": str(entry.get("seriesName") or ""),
                    "seriesIndex": str(entry.get("seriesIndex") or ""),
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
        if item["section"] == "tv":
            item["showTitle"] = prettify_name(segments[2]) if len(segments) >= 4 else "Unknown Show"
            item["showSlug"] = slugify(str(item["showTitle"]))
            item["seasonLabel"] = prettify_name(segments[3]) if len(segments) >= 5 else "Season 1"
            item["seasonNumber"] = parse_season_number(str(item["seasonLabel"]))
            item["episodeNumber"] = parse_episode_number(str(item["title"]))
            return

        if item["section"] in {"music", "audiobooks"}:
            if len(segments) >= 4:
                item["artist"] = prettify_name(segments[2])
                item["album"] = prettify_name(segments[3])
            elif len(segments) >= 3:
                item["artist"] = prettify_name(segments[2])

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
                "narrators",
                "publisher",
                "language",
                "tags",
                "seriesName",
                "seriesIndex",
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
                        "narrators": "",
                        "publisher": "",
                        "language": "",
                        "tags": "",
                        "seriesName": "",
                        "seriesIndex": "",
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
                    if item["section"] == "audiobooks":
                        embedded = extract_audiobook_embedded_metadata(actual_path, self.metadata_root_path())
                        for field, value in embedded.items():
                            if isinstance(value, str):
                                if value:
                                    item[field] = value
                            elif isinstance(value, (int, float)):
                                if float(value) > 0:
                                    item[field] = value
                            elif value:
                                item[field] = value
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
        show_records = self.group_show_records(scanned_items)

        try:
            self.rebuild_catalog_database(scanned_items, show_records)
        except sqlite3.Error:
            pass

        with self.lock:
            self.media_library = scanned_items
            self.item_metadata = item_entries
            self.show_metadata = show_entries
            self.metadata_generated_at = generated_at
            self.metadata_generator = generator
            self.metadata_available = bool(item_entries or show_entries)
            self.metadata_index_stale = metadata_index_stale
            self.storage_ready = storage_ready
            self.show_count = len(show_records)

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

    def group_show_records(self, items: list[dict[str, object]] | None = None) -> list[dict[str, object]]:
        shows: dict[str, dict[str, object]] = {}
        for item in items if items is not None else self.media_library:
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
            season["episodes"].append(dict(item))

        ordered_shows = list(shows.values())
        ordered_shows.sort(key=lambda show: lowercase_copy(str(show["title"])))

        for show in ordered_shows:
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
                episode_total += int(season["episodeCount"])
            show["seasonCount"] = len(show["seasons"])
            show["episodeCount"] = episode_total

        return ordered_shows

    def serialize_show_summary(self, show: dict[str, object]) -> dict[str, object]:
        return {
            "title": str(show.get("title") or "Unknown Show"),
            "slug": str(show.get("slug") or ""),
            "year": str(show.get("year") or ""),
            "overview": str(show.get("overview") or ""),
            "genres": str(show.get("genres") or ""),
            "contentRating": str(show.get("contentRating") or ""),
            "posterPath": normalize_virtual_path(str(show.get("posterPath") or "")),
            "backdropPath": normalize_virtual_path(str(show.get("backdropPath") or "")),
            "posterUrl": self.asset_url_for_path(str(show.get("posterPath") or "")),
            "backdropUrl": self.asset_url_for_path(str(show.get("backdropPath") or "")),
            "metadataSource": str(show.get("metadataSource") or ""),
            "tmdbRating": float(show.get("tmdbRating") or 0.0),
            "matchConfidence": float(show.get("matchConfidence") or 0.0),
            "seasonCount": int(show.get("seasonCount") or 0),
            "episodeCount": int(show.get("episodeCount") or 0),
            "detailUrl": f"{DEFAULT_APP_PATH}/tv/{show.get('slug') or ''}",
        }

    def serialize_show_detail(self, show: dict[str, object]) -> dict[str, object]:
        payload = self.serialize_show_summary(show)
        payload["seasons"] = []
        for season in show.get("seasons", []):
            payload["seasons"].append(
                {
                    "key": str(season.get("key") or ""),
                    "label": str(season.get("label") or ""),
                    "number": int(season.get("number") or 0),
                    "episodeCount": int(season.get("episodeCount") or 0),
                    "episodes": [self.serialize_media_item(episode) for episode in season.get("episodes", [])],
                }
            )
        return payload

    def build_show_library(self) -> list[dict[str, object]]:
        return [self.serialize_show_detail(show) for show in self.group_show_records()]

    def catalog_db_path(self) -> Path:
        resolved = self.resolve_virtual_path(DEFAULT_CATALOG_DB_PATH)
        if resolved is not None:
            return resolved
        return (self.media_root_path() / ".nomadscreen" / "library.db").resolve(strict=False)

    def catalog_item_search_text(self, item: dict[str, object]) -> str:
        return catalog_search_text(
            [
                item.get("title"),
                item.get("sortTitle"),
                item.get("overview"),
                item.get("tagline"),
                item.get("year"),
                item.get("releaseDate"),
                item.get("genres"),
                item.get("contentRating"),
                item.get("artist"),
                item.get("album"),
                item.get("narrators"),
                item.get("publisher"),
                item.get("language"),
                item.get("tags"),
                item.get("seriesName"),
                item.get("seriesIndex"),
                item.get("showTitle"),
                item.get("seasonLabel"),
                item.get("path"),
                item.get("section"),
                item.get("extension"),
            ]
        ).lower()

    def catalog_show_search_text(self, show: dict[str, object]) -> str:
        season_labels = " ".join(str(season.get("label") or "") for season in show.get("seasons", []))
        return catalog_search_text(
            [
                show.get("title"),
                show.get("year"),
                show.get("overview"),
                show.get("genres"),
                show.get("contentRating"),
                season_labels,
            ]
        ).lower()

    def rebuild_catalog_database(
        self,
        items: list[dict[str, object]],
        shows: list[dict[str, object]],
    ) -> None:
        db_path = self.catalog_db_path()
        db_path.parent.mkdir(parents=True, exist_ok=True)

        item_rows = [
            (
                str(item.get("path") or ""),
                str(item.get("title") or ""),
                str(item.get("sortTitle") or ""),
                lowercase_copy(str(item.get("sortTitle") or item.get("title") or "")),
                str(item.get("overview") or ""),
                str(item.get("tagline") or ""),
                str(item.get("year") or ""),
                str(item.get("releaseDate") or ""),
                str(item.get("genres") or ""),
                str(item.get("contentRating") or ""),
                str(item.get("artist") or ""),
                str(item.get("album") or ""),
                str(item.get("narrators") or ""),
                str(item.get("publisher") or ""),
                str(item.get("language") or ""),
                str(item.get("tags") or ""),
                str(item.get("seriesName") or ""),
                str(item.get("seriesIndex") or ""),
                normalize_virtual_path(str(item.get("posterPath") or "")),
                normalize_virtual_path(str(item.get("backdropPath") or "")),
                str(item.get("metadataSource") or ""),
                float(item.get("tmdbRating") or 0.0),
                float(item.get("runtimeMinutes") or 0.0),
                float(item.get("matchConfidence") or 0.0),
                str(item.get("showTitle") or ""),
                str(item.get("showSlug") or ""),
                str(item.get("seasonLabel") or ""),
                int(item.get("seasonNumber") or 0),
                int(item.get("episodeNumber") or 0),
                1 if bool(item.get("hasMetadata")) else 0,
                int(item.get("bytes") or 0),
                str(item.get("section") or ""),
                str(item.get("type") or ""),
                str(item.get("extension") or ""),
                self.catalog_item_search_text(item),
            )
            for item in items
        ]
        show_rows = [
            (
                str(show.get("slug") or ""),
                str(show.get("title") or ""),
                lowercase_copy(str(show.get("title") or "")),
                str(show.get("overview") or ""),
                str(show.get("year") or ""),
                str(show.get("genres") or ""),
                str(show.get("contentRating") or ""),
                normalize_virtual_path(str(show.get("posterPath") or "")),
                normalize_virtual_path(str(show.get("backdropPath") or "")),
                str(show.get("metadataSource") or ""),
                float(show.get("tmdbRating") or 0.0),
                float(show.get("matchConfidence") or 0.0),
                int(show.get("seasonCount") or 0),
                int(show.get("episodeCount") or 0),
                self.catalog_show_search_text(show),
            )
            for show in shows
        ]

        with sqlite3.connect(db_path) as connection:
            configure_sqlite_connection(connection)
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS items (
                    path TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    sort_title TEXT NOT NULL,
                    sort_key TEXT NOT NULL,
                    overview TEXT NOT NULL,
                    tagline TEXT NOT NULL,
                    year TEXT NOT NULL,
                    release_date TEXT NOT NULL,
                    genres TEXT NOT NULL,
                    content_rating TEXT NOT NULL,
                    artist TEXT NOT NULL,
                    album TEXT NOT NULL,
                    narrators TEXT NOT NULL DEFAULT '',
                    publisher TEXT NOT NULL DEFAULT '',
                    language TEXT NOT NULL DEFAULT '',
                    tags TEXT NOT NULL DEFAULT '',
                    series_name TEXT NOT NULL DEFAULT '',
                    series_index TEXT NOT NULL DEFAULT '',
                    poster_path TEXT NOT NULL,
                    backdrop_path TEXT NOT NULL,
                    metadata_source TEXT NOT NULL,
                    tmdb_rating REAL NOT NULL,
                    runtime_minutes REAL NOT NULL,
                    match_confidence REAL NOT NULL,
                    show_title TEXT NOT NULL,
                    show_slug TEXT NOT NULL,
                    season_label TEXT NOT NULL,
                    season_number INTEGER NOT NULL,
                    episode_number INTEGER NOT NULL,
                    has_metadata INTEGER NOT NULL,
                    bytes INTEGER NOT NULL,
                    section TEXT NOT NULL,
                    type TEXT NOT NULL,
                    extension TEXT NOT NULL,
                    search_text TEXT NOT NULL
                )
                """
            )
            ensure_table_columns(
                connection,
                "items",
                {
                    "narrators": "TEXT NOT NULL DEFAULT ''",
                    "publisher": "TEXT NOT NULL DEFAULT ''",
                    "language": "TEXT NOT NULL DEFAULT ''",
                    "tags": "TEXT NOT NULL DEFAULT ''",
                    "series_name": "TEXT NOT NULL DEFAULT ''",
                    "series_index": "TEXT NOT NULL DEFAULT ''",
                },
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS shows (
                    slug TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    sort_key TEXT NOT NULL,
                    overview TEXT NOT NULL,
                    year TEXT NOT NULL,
                    genres TEXT NOT NULL,
                    content_rating TEXT NOT NULL,
                    poster_path TEXT NOT NULL,
                    backdrop_path TEXT NOT NULL,
                    metadata_source TEXT NOT NULL,
                    tmdb_rating REAL NOT NULL,
                    match_confidence REAL NOT NULL,
                    season_count INTEGER NOT NULL,
                    episode_count INTEGER NOT NULL,
                    search_text TEXT NOT NULL
                )
                """
            )
            connection.execute("CREATE INDEX IF NOT EXISTS idx_items_section_sort ON items(section, sort_key, path)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_items_show_slug ON items(show_slug, season_number, episode_number)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_items_search_text ON items(search_text)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_shows_sort ON shows(sort_key, slug)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_shows_search_text ON shows(search_text)")
            self.ensure_playback_tables(connection)
            connection.execute("DELETE FROM items")
            connection.execute("DELETE FROM shows")
            connection.executemany(
                """
                INSERT INTO items (
                    path, title, sort_title, sort_key, overview, tagline, year, release_date,
                    genres, content_rating, artist, album, narrators, publisher, language, tags,
                    series_name, series_index, poster_path, backdrop_path,
                    metadata_source, tmdb_rating, runtime_minutes, match_confidence,
                    show_title, show_slug, season_label, season_number, episode_number,
                    has_metadata, bytes, section, type, extension, search_text
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                item_rows,
            )
            connection.executemany(
                """
                INSERT INTO shows (
                    slug, title, sort_key, overview, year, genres, content_rating,
                    poster_path, backdrop_path, metadata_source, tmdb_rating,
                    match_confidence, season_count, episode_count, search_text
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                show_rows,
            )
            connection.commit()

    def catalog_connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.catalog_db_path(), timeout=5.0)
        configure_sqlite_connection(connection)
        connection.row_factory = sqlite3.Row
        return connection

    def ensure_playback_tables(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS playback_state (
                client_key TEXT NOT NULL,
                remote_addr TEXT NOT NULL,
                path TEXT NOT NULL,
                section TEXT NOT NULL,
                show_slug TEXT NOT NULL,
                current_time REAL NOT NULL,
                duration REAL NOT NULL,
                completed INTEGER NOT NULL,
                watched_override INTEGER,
                last_played_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (client_key, path)
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_playback_state_client_recent ON playback_state(client_key, last_played_at DESC)"
        )

    def normalize_playback_entry_payload(self, payload: object) -> dict[str, object] | None:
        if not isinstance(payload, dict):
            return None

        safe_path = normalize_virtual_path(str(payload.get("path") or ""))
        if not safe_path:
            return None

        library_item = self.find_library_item(safe_path)
        safe_section = lowercase_copy(str(payload.get("section") or (library_item or {}).get("section") or "")).strip()
        if safe_section not in {"movies", "tv", "audiobooks"}:
            return None

        safe_show_slug = slugify(
            str(payload.get("showSlug") or (library_item or {}).get("showSlug") or "")
        ) if safe_section == "tv" else ""

        return {
            "path": safe_path,
            "section": safe_section,
            "showSlug": safe_show_slug,
            "currentTime": safe_float(payload.get("currentTime"), 0.0, 0.0),
            "duration": safe_float(payload.get("duration"), 0.0, 0.0),
            "completed": config_bool(payload.get("completed"), False),
            "lastPlayedAt": normalize_iso_timestamp(payload.get("lastPlayedAt")),
        }

    def playback_entry_from_row(self, row: sqlite3.Row) -> dict[str, object]:
        return {
            "path": normalize_virtual_path(str(row["path"] or "")),
            "section": str(row["section"] or ""),
            "showSlug": str(row["show_slug"] or ""),
            "currentTime": float(row["current_time"] or 0.0),
            "duration": float(row["duration"] or 0.0),
            "completed": bool(row["completed"]),
            "lastPlayedAt": str(row["last_played_at"] or ""),
        }

    def playback_state_payload(self, remote_address: str | None) -> dict[str, object]:
        client_key = playback_client_key(remote_address)
        with self.catalog_connection() as connection:
            self.ensure_playback_tables(connection)
            rows = connection.execute(
                """
                SELECT path, section, show_slug, current_time, duration, completed, watched_override, last_played_at
                FROM playback_state
                WHERE client_key = ?
                ORDER BY last_played_at DESC, path
                """,
                (client_key,),
            ).fetchall()

        playback: dict[str, dict[str, object]] = {}
        watched: dict[str, bool] = {}
        for row in rows:
            entry = self.playback_entry_from_row(row)
            safe_path = str(entry["path"])
            if safe_path:
                playback[safe_path] = entry
                if row["watched_override"] is not None:
                    watched[safe_path] = bool(row["watched_override"])

        return {
            "profile": {
                "source": "device-ip",
                "sharedAcrossBrowsers": True,
            },
            "count": len(playback),
            "watchedCount": len(watched),
            "playback": playback,
            "watched": watched,
        }

    def save_playback_state(
        self,
        remote_address: str | None,
        playback_entries: object,
        watched_updates: object,
        clear_watched_paths: object,
    ) -> dict[str, object]:
        client_key = playback_client_key(remote_address)
        safe_remote_address = str(remote_address or "").strip()
        safe_entries: list[dict[str, object]] = []
        safe_watched_updates: dict[str, bool] = {}
        safe_clear_paths: list[str] = []

        if isinstance(playback_entries, list):
            for raw_entry in playback_entries:
                safe_entry = self.normalize_playback_entry_payload(raw_entry)
                if safe_entry is not None:
                    safe_entries.append(safe_entry)

        if isinstance(watched_updates, dict):
            for raw_path, raw_value in watched_updates.items():
                safe_path = normalize_virtual_path(str(raw_path or ""))
                if safe_path:
                    safe_watched_updates[safe_path] = config_bool(raw_value, False)

        if isinstance(clear_watched_paths, list):
            for raw_path in clear_watched_paths:
                safe_path = normalize_virtual_path(str(raw_path or ""))
                if safe_path and safe_path not in safe_clear_paths:
                    safe_clear_paths.append(safe_path)

        updated_count = 0
        with self.lock:
            with self.catalog_connection() as connection:
                self.ensure_playback_tables(connection)
                updated_at = iso_timestamp_now()

                for entry in safe_entries:
                    connection.execute(
                        """
                        INSERT INTO playback_state (
                            client_key, remote_addr, path, section, show_slug,
                            current_time, duration, completed, watched_override,
                            last_played_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
                        ON CONFLICT(client_key, path) DO UPDATE SET
                            remote_addr = excluded.remote_addr,
                            section = excluded.section,
                            show_slug = excluded.show_slug,
                            current_time = excluded.current_time,
                            duration = excluded.duration,
                            completed = excluded.completed,
                            last_played_at = excluded.last_played_at,
                            updated_at = excluded.updated_at
                        """,
                        (
                            client_key,
                            safe_remote_address,
                            str(entry["path"]),
                            str(entry["section"]),
                            str(entry["showSlug"]),
                            float(entry["currentTime"]),
                            float(entry["duration"]),
                            1 if bool(entry["completed"]) else 0,
                            str(entry["lastPlayedAt"]),
                            updated_at,
                        ),
                    )
                    updated_count += 1

                for safe_path, watched in safe_watched_updates.items():
                    existing_row = connection.execute(
                        """
                        SELECT section, show_slug, current_time, duration, completed, last_played_at
                        FROM playback_state
                        WHERE client_key = ? AND path = ?
                        LIMIT 1
                        """,
                        (client_key, safe_path),
                    ).fetchone()
                    library_item = self.find_library_item(safe_path)
                    safe_section = str(
                        (library_item or {}).get("section")
                        or (existing_row["section"] if existing_row is not None else "")
                        or ""
                    )
                    if safe_section not in {"movies", "tv", "audiobooks"}:
                        continue
                    safe_show_slug = str(
                        (library_item or {}).get("showSlug")
                        or (existing_row["show_slug"] if existing_row is not None else "")
                        or ""
                    )
                    safe_last_played_at = str(
                        (existing_row["last_played_at"] if existing_row is not None else "") or updated_at
                    )
                    connection.execute(
                        """
                        INSERT INTO playback_state (
                            client_key, remote_addr, path, section, show_slug,
                            current_time, duration, completed, watched_override,
                            last_played_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(client_key, path) DO UPDATE SET
                            remote_addr = excluded.remote_addr,
                            section = excluded.section,
                            show_slug = excluded.show_slug,
                            watched_override = excluded.watched_override,
                            updated_at = excluded.updated_at
                        """,
                        (
                            client_key,
                            safe_remote_address,
                            safe_path,
                            safe_section,
                            safe_show_slug,
                            float(existing_row["current_time"] if existing_row is not None else 0.0),
                            float(existing_row["duration"] if existing_row is not None else 0.0),
                            1 if (existing_row is not None and bool(existing_row["completed"])) else 0,
                            1 if watched else 0,
                            safe_last_played_at,
                            updated_at,
                        ),
                    )
                    updated_count += 1

                for safe_path in safe_clear_paths:
                    connection.execute(
                        """
                        UPDATE playback_state
                        SET watched_override = NULL, updated_at = ?
                        WHERE client_key = ? AND path = ?
                        """,
                        (updated_at, client_key, safe_path),
                    )
                    connection.execute(
                        """
                        DELETE FROM playback_state
                        WHERE client_key = ? AND path = ?
                          AND watched_override IS NULL
                          AND completed = 0
                          AND current_time <= 0
                          AND duration <= 0
                        """,
                        (client_key, safe_path),
                    )
                    updated_count += 1

                connection.commit()

        return {
            "ok": True,
            "updated": updated_count,
            "profile": {
                "source": "device-ip",
                "sharedAcrossBrowsers": True,
            },
        }

    def clear_playback_state(self, remote_address: str | None) -> dict[str, object]:
        client_key = playback_client_key(remote_address)
        with self.lock:
            with self.catalog_connection() as connection:
                self.ensure_playback_tables(connection)
                deleted = connection.execute(
                    "DELETE FROM playback_state WHERE client_key = ?",
                    (client_key,),
                ).rowcount
                connection.commit()

        return {
            "ok": True,
            "deleted": int(deleted or 0),
            "profile": {
                "source": "device-ip",
                "sharedAcrossBrowsers": True,
            },
        }

    def catalog_item_from_row(self, row: sqlite3.Row) -> dict[str, object]:
        return {
            "path": normalize_virtual_path(str(row["path"])),
            "title": str(row["title"] or ""),
            "sortTitle": str(row["sort_title"] or ""),
            "overview": str(row["overview"] or ""),
            "tagline": str(row["tagline"] or ""),
            "year": str(row["year"] or ""),
            "releaseDate": str(row["release_date"] or ""),
            "genres": str(row["genres"] or ""),
            "contentRating": str(row["content_rating"] or ""),
            "artist": str(row["artist"] or ""),
            "album": str(row["album"] or ""),
            "narrators": str(row["narrators"] or ""),
            "publisher": str(row["publisher"] or ""),
            "language": str(row["language"] or ""),
            "tags": str(row["tags"] or ""),
            "seriesName": str(row["series_name"] or ""),
            "seriesIndex": str(row["series_index"] or ""),
            "posterPath": normalize_virtual_path(str(row["poster_path"] or "")),
            "backdropPath": normalize_virtual_path(str(row["backdrop_path"] or "")),
            "metadataSource": str(row["metadata_source"] or ""),
            "tmdbRating": float(row["tmdb_rating"] or 0.0),
            "runtimeMinutes": float(row["runtime_minutes"] or 0.0),
            "matchConfidence": float(row["match_confidence"] or 0.0),
            "showTitle": str(row["show_title"] or ""),
            "showSlug": str(row["show_slug"] or ""),
            "seasonLabel": str(row["season_label"] or ""),
            "seasonNumber": int(row["season_number"] or 0),
            "episodeNumber": int(row["episode_number"] or 0),
            "hasMetadata": bool(row["has_metadata"]),
            "bytes": int(row["bytes"] or 0),
            "section": str(row["section"] or ""),
            "type": str(row["type"] or ""),
            "extension": str(row["extension"] or ""),
        }

    def serialize_catalog_item_row(self, row: sqlite3.Row) -> dict[str, object]:
        return self.serialize_media_item(self.catalog_item_from_row(row))

    def serialize_catalog_show_row(self, row: sqlite3.Row) -> dict[str, object]:
        return {
            "title": str(row["title"] or "Unknown Show"),
            "slug": str(row["slug"] or ""),
            "year": str(row["year"] or ""),
            "overview": str(row["overview"] or ""),
            "genres": str(row["genres"] or ""),
            "contentRating": str(row["content_rating"] or ""),
            "posterPath": normalize_virtual_path(str(row["poster_path"] or "")),
            "backdropPath": normalize_virtual_path(str(row["backdrop_path"] or "")),
            "posterUrl": self.asset_url_for_path(str(row["poster_path"] or "")),
            "backdropUrl": self.asset_url_for_path(str(row["backdrop_path"] or "")),
            "metadataSource": str(row["metadata_source"] or ""),
            "tmdbRating": float(row["tmdb_rating"] or 0.0),
            "matchConfidence": float(row["match_confidence"] or 0.0),
            "seasonCount": int(row["season_count"] or 0),
            "episodeCount": int(row["episode_count"] or 0),
            "detailUrl": f"{DEFAULT_APP_PATH}/tv/{row['slug']}",
        }

    def catalog_counts_payload(self) -> dict[str, int]:
        return {
            "total": len(self.media_library),
            "movies": self.count_section("movies"),
            "shows": int(self.show_count),
            "episodes": self.count_section("tv"),
            "music": self.count_section("music"),
            "audiobooks": self.count_section("audiobooks"),
            "documents": self.count_section("documents"),
        }

    def catalog_metadata_payload(self) -> dict[str, object]:
        return {
            "available": self.metadata_available,
            "generatedAt": self.metadata_generated_at,
            "generator": self.metadata_generator,
            "itemCount": len(self.item_metadata),
            "showCount": len(self.show_metadata),
        }

    def catalog_summary_payload(self) -> dict[str, object]:
        return {
            "counts": self.catalog_counts_payload(),
            "metadata": self.catalog_metadata_payload(),
        }

    def catalog_home_payload(self) -> dict[str, object]:
        grouped_shows = [self.serialize_show_summary(show) for show in self.group_show_records()]
        sections = {
            "movies": [
                self.serialize_media_item(item)
                for item in self.media_library
                if item["section"] == "movies"
            ][:DEFAULT_HOME_MOVIE_LIMIT],
            "tv": grouped_shows[:DEFAULT_HOME_SHOW_LIMIT],
            "music": [
                self.serialize_media_item(item)
                for item in self.media_library
                if item["section"] == "music"
            ][:DEFAULT_HOME_MUSIC_LIMIT],
            "audiobooks": [
                self.serialize_media_item(item)
                for item in self.media_library
                if item["section"] == "audiobooks"
            ][:DEFAULT_HOME_AUDIOBOOK_LIMIT],
            "documents": [
                self.serialize_media_item(item)
                for item in self.media_library
                if item["section"] == "documents"
            ][:DEFAULT_HOME_DOCUMENT_LIMIT],
        }
        return {
            "counts": self.catalog_counts_payload(),
            "metadata": self.catalog_metadata_payload(),
            "sections": sections,
        }

    def catalog_search_payload(self, query: object, limit: object) -> dict[str, object]:
        safe_query = normalize_catalog_query(query)
        safe_limit = normalize_catalog_limit(limit, DEFAULT_SEARCH_RESULT_LIMIT)
        if not safe_query:
            return {"query": "", "count": 0, "items": []}
        with self.catalog_connection() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM items
                WHERE search_text LIKE ?
                ORDER BY
                    CASE section
                        WHEN 'movies' THEN 0
                        WHEN 'tv' THEN 1
                        WHEN 'music' THEN 2
                        WHEN 'audiobooks' THEN 3
                        WHEN 'documents' THEN 4
                        ELSE 99
                    END,
                    sort_key,
                    path
                LIMIT ?
                """,
                (catalog_like_pattern(safe_query), safe_limit),
            ).fetchall()
        return {
            "query": safe_query,
            "count": len(rows),
            "items": [self.serialize_catalog_item_row(row) for row in rows],
        }

    def catalog_genres_payload(self) -> dict[str, list[str]]:
        grouped = {
            "movies": [],
            "tv": [],
            "audiobooks": [],
        }
        known = {
            "movies": set(),
            "tv": set(),
            "audiobooks": set(),
        }

        def add_genres(section: str, raw_value: object) -> None:
            if section not in grouped:
                return
            for genre in split_catalog_genres(raw_value):
                key = genre.casefold()
                if key in known[section]:
                    continue
                known[section].add(key)
                grouped[section].append(genre)

        with self.catalog_connection() as connection:
            movie_rows = connection.execute("SELECT genres FROM items WHERE section = 'movies' AND genres <> ''").fetchall()
            audiobook_rows = connection.execute(
                "SELECT genres FROM items WHERE section = 'audiobooks' AND genres <> ''"
            ).fetchall()
            show_rows = connection.execute("SELECT genres FROM shows WHERE genres <> ''").fetchall()

        for row in movie_rows:
            add_genres("movies", row["genres"])
        for row in audiobook_rows:
            add_genres("audiobooks", row["genres"])
        for row in show_rows:
            add_genres("tv", row["genres"])

        for section, values in grouped.items():
            grouped[section] = sorted(values, key=str.casefold)

        return grouped

    def catalog_movies_payload(self, offset: object, limit: object, query: object, genre: object) -> dict[str, object]:
        safe_offset = normalize_catalog_offset(offset)
        safe_limit = normalize_catalog_limit(limit)
        safe_query = normalize_catalog_query(query)
        safe_genre = normalize_catalog_genre(genre)
        params: list[object] = ["movies"]
        where = "WHERE section = ?"
        if safe_query:
            where += " AND search_text LIKE ?"
            params.append(catalog_like_pattern(safe_query))
        if safe_genre:
            where += " AND LOWER(',' || REPLACE(REPLACE(genres, ', ', ','), ' ,', ',') || ',') LIKE ?"
            params.append(catalog_genre_pattern(safe_genre))
        with self.catalog_connection() as connection:
            total = int(connection.execute(f"SELECT COUNT(*) FROM items {where}", params).fetchone()[0])
            rows = connection.execute(
                f"""
                SELECT *
                FROM items
                {where}
                ORDER BY sort_key, path
                LIMIT ? OFFSET ?
                """,
                [*params, safe_limit, safe_offset],
            ).fetchall()
        return {
            "query": safe_query,
            "genre": safe_genre,
            "offset": safe_offset,
            "limit": safe_limit,
            "total": total,
            "count": len(rows),
            "hasMore": safe_offset + len(rows) < total,
            "items": [self.serialize_catalog_item_row(row) for row in rows],
        }

    def catalog_movie_payload(self, path: object) -> dict[str, object] | None:
        safe_path = normalize_virtual_path(str(path or ""))
        if not safe_path:
            return None
        with self.catalog_connection() as connection:
            row = connection.execute(
                "SELECT * FROM items WHERE path = ? AND section = 'movies' LIMIT 1",
                (safe_path,),
            ).fetchone()
        return self.serialize_catalog_item_row(row) if row is not None else None

    def catalog_audiobooks_payload(
        self,
        offset: object,
        limit: object,
        query: object,
        genre: object,
        collection: object,
        author: object,
    ) -> dict[str, object]:
        safe_offset = normalize_catalog_offset(offset)
        safe_limit = normalize_catalog_limit(limit)
        safe_query = normalize_catalog_query(query)
        safe_genre = normalize_catalog_genre(genre)
        safe_collection = normalize_audiobook_collection_label(collection)
        safe_author = normalize_catalog_query(author)
        params: list[object] = ["audiobooks"]
        where = "WHERE section = ?"
        if safe_query:
            where += " AND search_text LIKE ?"
            params.append(catalog_like_pattern(safe_query))
        if safe_genre:
            where += " AND LOWER(',' || REPLACE(REPLACE(genres, ', ', ','), ' ,', ',') || ',') LIKE ?"
            params.append(catalog_genre_pattern(safe_genre))
        if safe_collection:
            where += " AND audiobook_collection_key(series_name, album, path) = ?"
            params.append(slugify_text(safe_collection))
        if safe_author:
            where += " AND LOWER(TRIM(artist)) = ?"
            params.append(lowercase_copy(safe_author))
        order_by = "sort_key, path"
        if safe_collection:
            order_by = """
                audiobook_series_sort_rank(series_index, series_name, album),
                audiobook_series_sort_number(series_index, series_name, album),
                sort_key,
                path
            """
        with self.catalog_connection() as connection:
            total = int(connection.execute(f"SELECT COUNT(*) FROM items {where}", params).fetchone()[0])
            rows = connection.execute(
                f"""
                SELECT *
                FROM items
                {where}
                ORDER BY {order_by}
                LIMIT ? OFFSET ?
                """,
                [*params, safe_limit, safe_offset],
            ).fetchall()
        return {
            "query": safe_query,
            "genre": safe_genre,
            "collection": safe_collection,
            "author": safe_author,
            "offset": safe_offset,
            "limit": safe_limit,
            "total": total,
            "count": len(rows),
            "hasMore": safe_offset + len(rows) < total,
            "items": [self.serialize_catalog_item_row(row) for row in rows],
        }

    def catalog_audiobook_payload(self, path: object) -> dict[str, object] | None:
        safe_path = normalize_virtual_path(str(path or ""))
        if not safe_path:
            return None
        with self.catalog_connection() as connection:
            row = connection.execute(
                "SELECT * FROM items WHERE path = ? AND section = 'audiobooks' LIMIT 1",
                (safe_path,),
            ).fetchone()
        return self.serialize_catalog_item_row(row) if row is not None else None

    def catalog_shows_payload(self, offset: object, limit: object, query: object, genre: object) -> dict[str, object]:
        safe_offset = normalize_catalog_offset(offset)
        safe_limit = normalize_catalog_limit(limit)
        safe_query = normalize_catalog_query(query)
        safe_genre = normalize_catalog_genre(genre)
        params: list[object] = []
        where = ""
        if safe_query:
            where = "WHERE search_text LIKE ?"
            params.append(catalog_like_pattern(safe_query))
        if safe_genre:
            where = f"{where} {'AND' if where else 'WHERE'} LOWER(',' || REPLACE(REPLACE(genres, ', ', ','), ' ,', ',') || ',') LIKE ?"
            params.append(catalog_genre_pattern(safe_genre))
        with self.catalog_connection() as connection:
            total = int(connection.execute(f"SELECT COUNT(*) FROM shows {where}", params).fetchone()[0])
            rows = connection.execute(
                f"""
                SELECT *
                FROM shows
                {where}
                ORDER BY sort_key, slug
                LIMIT ? OFFSET ?
                """,
                [*params, safe_limit, safe_offset],
            ).fetchall()
        return {
            "query": safe_query,
            "genre": safe_genre,
            "offset": safe_offset,
            "limit": safe_limit,
            "total": total,
            "count": len(rows),
            "hasMore": safe_offset + len(rows) < total,
            "items": [self.serialize_catalog_show_row(row) for row in rows],
        }

    def catalog_show_payload(self, slug: object) -> dict[str, object] | None:
        safe_slug = slugify(str(slug or ""))
        if not safe_slug:
            return None
        with self.catalog_connection() as connection:
            show_row = connection.execute(
                "SELECT * FROM shows WHERE slug = ? LIMIT 1",
                (safe_slug,),
            ).fetchone()
            if show_row is None:
                return None
            episode_rows = connection.execute(
                """
                SELECT *
                FROM items
                WHERE section = 'tv' AND show_slug = ?
                ORDER BY season_number, lower(season_label), episode_number, sort_key, path
                """,
                (safe_slug,),
            ).fetchall()

        show = self.serialize_catalog_show_row(show_row)
        seasons: list[dict[str, object]] = []
        season_map: dict[str, dict[str, object]] = {}
        for row in episode_rows:
            item = self.serialize_catalog_item_row(row)
            season_label = str(item.get("seasonLabel") or "")
            if not season_label:
                season_number = int(item.get("seasonNumber") or 1)
                season_label = "Specials" if season_number == 0 else f"Season {season_number}"
                item["seasonLabel"] = season_label
            season_key = f"{int(item.get('seasonNumber') or 0)}|{lowercase_copy(season_label)}"
            season = season_map.get(season_key)
            if season is None:
                season = {
                    "key": season_key,
                    "label": season_label,
                    "number": int(item.get("seasonNumber") or 0),
                    "episodeCount": 0,
                    "episodes": [],
                }
                season_map[season_key] = season
                seasons.append(season)
            season["episodes"].append(item)
            season["episodeCount"] += 1
        show["seasons"] = seasons
        return show

    def catalog_random_item_payload(self, section: object) -> dict[str, object] | None:
        safe_section = lowercase_copy(str(section or "")).strip()
        params: list[object] = []
        where = ""
        if safe_section and safe_section not in {"all", "any"}:
            where = "WHERE section = ?"
            params.append(safe_section)
        with self.catalog_connection() as connection:
            row = connection.execute(
                f"SELECT * FROM items {where} ORDER BY RANDOM() LIMIT 1",
                params,
            ).fetchone()
        return self.serialize_catalog_item_row(row) if row is not None else None

    def catalog_lookup_payload(self, paths: object) -> dict[str, object]:
        safe_paths: list[str] = []
        seen_paths: set[str] = set()
        if isinstance(paths, list):
            for raw_path in paths[:MAX_CATALOG_LOOKUP_PATHS]:
                safe_path = normalize_virtual_path(str(raw_path or ""))
                if safe_path and safe_path not in seen_paths:
                    safe_paths.append(safe_path)
                    seen_paths.add(safe_path)

        if not safe_paths:
            return {
                "count": 0,
                "showCount": 0,
                "items": [],
                "shows": [],
            }

        placeholders = ", ".join("?" for _ in safe_paths)
        with self.catalog_connection() as connection:
            item_rows = connection.execute(
                f"SELECT * FROM items WHERE path IN ({placeholders})",
                safe_paths,
            ).fetchall()

        item_by_path = {
            normalize_virtual_path(str(row["path"] or "")): self.serialize_catalog_item_row(row)
            for row in item_rows
        }
        ordered_items = [item_by_path[path] for path in safe_paths if path in item_by_path]

        show_slugs: list[str] = []
        seen_slugs: set[str] = set()
        for item in ordered_items:
            safe_slug = slugify(str(item.get("showSlug") or ""))
            if item.get("section") == "tv" and safe_slug and safe_slug not in seen_slugs:
                show_slugs.append(safe_slug)
                seen_slugs.add(safe_slug)

        shows = [show for slug in show_slugs if (show := self.catalog_show_payload(slug)) is not None]
        return {
            "count": len(ordered_items),
            "showCount": len(shows),
            "items": ordered_items,
            "shows": shows,
        }

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
                **self.catalog_counts_payload(),
                "shows": len(shows),
            },
            "metadata": self.catalog_metadata_payload(),
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

    def status_payload(self, authenticated: bool = False) -> dict[str, object]:
        local_ip = self.best_local_ip()
        port = int(self.settings["http_port"])
        mdns_host = f"{self.settings['mdns_host']}.local"
        ip_app_url = self.compose_url(local_ip, port, DEFAULT_APP_PATH)
        mdns_url = self.compose_url(mdns_host, port, DEFAULT_APP_PATH)
        network = self.network_snapshot()
        current_name = str(network["current_name"])
        reported_ssid = current_name if current_name else str(network["hotspot_name"])
        is_authenticated = bool(authenticated)
        hotspot_password = str(network["hotspot_password"]) if is_authenticated else ""
        current_password = str(network["current_password"]) if is_authenticated else ""
        if is_authenticated:
            file_manager = self.file_manager_status_payload(local_ip, mdns_host)
            metadata_refresh = self.metadata_refresh_status_payload()
        else:
            file_manager = {
                "username": "admin",
                "password": "",
                "passwordAvailable": False,
                "passwordPath": "",
                "port": safe_int(self.settings.get("filebrowser_port"), DEFAULT_FILEBROWSER_PORT, 1),
                "url": "",
                "ipUrl": "",
                "mdnsUrl": "",
                "root": "",
            }
            metadata_refresh = {
                "id": "",
                "active": False,
                "phase": "idle",
                "message": "",
                "error": "",
                "reason": "",
                "detail": "",
                "enabled": False,
                "configured": False,
                "online": False,
                "attempted": False,
                "success": False,
                "script": "",
                "recentLines": [],
                "outputLineCount": 0,
                "exitCode": None,
                "startedAt": "",
                "updatedAt": "",
                "completedAt": "",
            }
        return {
            "device": self.settings["device_name"],
            "ssid": reported_ssid,
            "password": current_password,
            "networkMode": network["mode"],
            "networkName": current_name,
            "hotspotSsid": network["hotspot_name"],
            "hotspotPassword": hotspot_password,
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
            "uploadTempRoot": str(self.settings["upload_tmp_directory"]) if is_authenticated else "",
            "uploadTempReady": self.upload_temp_ready if is_authenticated else False,
            "clients": self.active_client_count(),
            "lastPlayed": self.last_played_title,
            "lastPlayedType": self.last_played_type,
            "metadataAvailable": self.metadata_available,
            "metadataGeneratedAt": self.metadata_generated_at,
            "metadataGenerator": self.metadata_generator,
            "metadataItemCount": len(self.item_metadata),
            "metadataShowCount": len(self.show_metadata),
            "preferServerLibrary": self.metadata_index_stale,
            "upload": self.upload_status_payload() if is_authenticated else self.default_upload_status(),
            "metadataRefresh": metadata_refresh,
            "fileManager": file_manager,
            "deviceAuth": self.device_auth_payload(is_authenticated),
            "platform": "raspberry-pi-zero-w",
        }

    def device_config_payload(self) -> dict[str, object]:
        return {
            "deviceName": str(self.settings["device_name"]),
            "hotspotSsid": str(self.settings["ssid"]),
            "wifiPassword": str(self.settings["wifi_password"]),
            "tmdbApiKey": str(self.settings.get("tmdb_api_key") or ""),
            "devicePasswordConfigured": self.device_access_uses_dedicated_password(),
            "configSource": self.settings["config_source"],
        }

    def save_device_config(
        self,
        device_name: object,
        hotspot_ssid: object,
        wifi_password: object,
        tmdb_api_key: object = "",
        tmdb_bearer_token: object | None = None,
        device_password: object | None = None,
    ) -> dict[str, object]:
        safe_device_name = normalize_device_name(str(device_name or ""))[:MAX_DEVICE_NAME_LENGTH]
        if not safe_device_name:
            raise ValueError("Enter a server name.")

        safe_hotspot_ssid = normalize_hotspot_ssid(str(hotspot_ssid or ""))
        if not safe_hotspot_ssid:
            raise ValueError("Enter a fallback Wi-Fi name.")

        safe_wifi_password = validated_hotspot_password(str(wifi_password or ""))
        safe_device_password = normalize_hotspot_password(str(device_password or ""))
        if safe_device_password:
            safe_device_password = validated_device_page_password(safe_device_password)

        with self.lock:
            config_path = Path(self.settings["config_path"])
            raw_config, _ = read_runtime_config_file(config_path)
            raw_config["deviceName"] = safe_device_name
            raw_config.pop("serverName", None)
            raw_config["hotspotSsid"] = safe_hotspot_ssid
            raw_config.pop("accessPointSsid", None)
            raw_config["wifiPassword"] = safe_wifi_password
            raw_config["tmdbApiKey"] = str(tmdb_api_key or "").strip()
            if safe_device_password:
                raw_config["devicePassword"] = safe_device_password
            if tmdb_bearer_token is not None:
                raw_config["tmdbBearerToken"] = str(tmdb_bearer_token or "").strip()
            if isinstance(raw_config.get("wifi"), dict):
                wifi_block = dict(raw_config.get("wifi") or {})
                wifi_block["ssid"] = safe_hotspot_ssid
                wifi_block["password"] = safe_wifi_password
                raw_config["wifi"] = wifi_block
            config_path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(config_path, json.dumps(raw_config, indent=2, ensure_ascii=False) + "\n")
            self.settings = load_settings()
            self.configure_upload_temp_directory()
            self.prepare_upload_staging_directory()

        message = (
            "Saved device settings. Fallback Wi-Fi changes apply the next time the hotspot starts. "
            "The TMDb API key is used on the next online rescan."
        )
        if safe_device_password:
            message = (
                f"{message} The Device page will use the new password the next time you unlock it."
            )

        return {
            "ok": True,
            "message": message,
            "config": self.device_config_payload(),
            "status": self.status_payload(authenticated=True),
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


def current_device_auth_token() -> str:
    return str(request.cookies.get(DEVICE_AUTH_COOKIE_NAME) or "").strip()


def request_has_device_access() -> bool:
    return state.is_device_authenticated(current_device_auth_token())


def set_device_auth_cookie(response: Response, token: str, max_age: int) -> Response:
    response.set_cookie(
        DEVICE_AUTH_COOKIE_NAME,
        token,
        max_age=max_age,
        httponly=True,
        secure=request.is_secure,
        samesite="Lax",
        path="/",
    )
    return response


def clear_device_auth_cookie(response: Response) -> Response:
    response.delete_cookie(DEVICE_AUTH_COOKIE_NAME, path="/", httponly=True, secure=request.is_secure, samesite="Lax")
    return response


def unauthorized_device_response(message: str = "Enter the device page password to continue.") -> Response:
    response = no_store_json(
        {
            "error": message,
            "deviceAuth": state.device_auth_payload(False),
        },
        401,
    )
    return clear_device_auth_cookie(response)


def require_device_access(handler):
    @wraps(handler)
    def wrapped(*args, **kwargs):
        if not request_has_device_access():
            return unauthorized_device_response()
        return handler(*args, **kwargs)

    return wrapped


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
    return no_store_json(state.status_payload(authenticated=request_has_device_access()))


@app.get("/api/device-auth")
def api_device_auth_status() -> Response:
    authenticated = request_has_device_access()
    return no_store_json(
        {
            "ok": True,
            "deviceAuth": state.device_auth_payload(authenticated),
        }
    )


@app.post("/api/device-auth/login")
def api_device_auth_login() -> Response:
    payload = request.get_json(silent=True) or {}
    password = normalize_hotspot_password(str(payload.get("password") or ""))
    if not password or password != state.device_access_password():
        return unauthorized_device_response("That password did not unlock the Device page.")
    token, max_age = state.create_device_auth_session()
    response = no_store_json(
        {
            "ok": True,
            "message": "Device page unlocked.",
            "deviceAuth": state.device_auth_payload(True),
            "status": state.status_payload(authenticated=True),
        }
    )
    return set_device_auth_cookie(response, token, max_age)


@app.post("/api/device-auth/logout")
def api_device_auth_logout() -> Response:
    state.clear_device_auth_session(current_device_auth_token())
    response = no_store_json(
        {
            "ok": True,
            "message": "Device page locked.",
            "deviceAuth": state.device_auth_payload(False),
            "status": state.status_payload(authenticated=False),
        }
    )
    return clear_device_auth_cookie(response)


@app.get("/api/device-config")
@require_device_access
def api_device_config_get() -> Response:
    return no_store_json({"ok": True, "config": state.device_config_payload()})


@app.post("/api/device-config")
@require_device_access
def api_device_config() -> Response:
    payload = request.get_json(silent=True) or {}
    try:
        result = state.save_device_config(
            payload.get("deviceName"),
            payload.get("hotspotSsid") or payload.get("wifiName"),
            payload.get("wifiPassword"),
            payload.get("tmdbApiKey"),
            payload.get("tmdbBearerToken"),
            payload.get("devicePassword"),
        )
    except ValueError as error:
        return no_store_json({"error": str(error)}, 400)
    except OSError:
        return no_store_json({"error": "Could not save the device settings file."}, 500)
    return no_store_json(result)


@app.get("/api/playback-state")
def api_playback_state_get() -> Response:
    try:
        payload = state.playback_state_payload(request.remote_addr)
    except sqlite3.Error:
        return no_store_json({"error": "Playback history is unavailable right now."}, 500)
    return no_store_json({"ok": True, **payload})


@app.post("/api/playback-state")
def api_playback_state_post() -> Response:
    payload = request.get_json(silent=True) or {}
    try:
        result = state.save_playback_state(
            request.remote_addr,
            payload.get("playback"),
            payload.get("watched"),
            payload.get("clearWatched"),
        )
    except sqlite3.Error:
        return no_store_json({"error": "Playback history could not be saved right now."}, 500)
    return no_store_json(result, 202)


@app.delete("/api/playback-state")
def api_playback_state_delete() -> Response:
    try:
        result = state.clear_playback_state(request.remote_addr)
    except sqlite3.Error:
        return no_store_json({"error": "Playback history could not be cleared right now."}, 500)
    return no_store_json(result)


@app.post("/api/upload-progress")
@require_device_access
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


@app.get("/api/catalog/summary")
def api_catalog_summary() -> Response:
    return no_store_json({"ok": True, **state.catalog_summary_payload()})


@app.get("/api/catalog/home")
def api_catalog_home() -> Response:
    return no_store_json({"ok": True, **state.catalog_home_payload()})


@app.get("/api/catalog/search")
def api_catalog_search() -> Response:
    try:
        payload = state.catalog_search_payload(
            request.args.get("q"),
            request.args.get("limit"),
        )
    except sqlite3.Error:
        return no_store_json({"error": "Catalog search is unavailable right now."}, 500)
    return no_store_json({"ok": True, **payload})


@app.get("/api/catalog/genres")
def api_catalog_genres() -> Response:
    try:
        payload = state.catalog_genres_payload()
    except sqlite3.Error:
        return no_store_json({"error": "Catalog genres are unavailable right now."}, 500)
    return no_store_json({"ok": True, **payload})


@app.get("/api/catalog/movies")
def api_catalog_movies() -> Response:
    try:
        payload = state.catalog_movies_payload(
            request.args.get("offset"),
            request.args.get("limit"),
            request.args.get("q"),
            request.args.get("genre"),
        )
    except sqlite3.Error:
        return no_store_json({"error": "Movie catalog is unavailable right now."}, 500)
    return no_store_json({"ok": True, **payload})


@app.get("/api/catalog/movie")
def api_catalog_movie() -> Response:
    try:
        movie = state.catalog_movie_payload(request.args.get("path"))
    except sqlite3.Error:
        return no_store_json({"error": "Movie details are unavailable right now."}, 500)
    if movie is None:
        return no_store_json({"error": "Movie not found"}, 404)
    return no_store_json({"ok": True, "item": movie})


@app.get("/api/catalog/audiobooks")
def api_catalog_audiobooks() -> Response:
    try:
        payload = state.catalog_audiobooks_payload(
            request.args.get("offset"),
            request.args.get("limit"),
            request.args.get("q"),
            request.args.get("genre"),
            request.args.get("collection"),
            request.args.get("author"),
        )
    except sqlite3.Error:
        return no_store_json({"error": "Audiobook catalog is unavailable right now."}, 500)
    return no_store_json({"ok": True, **payload})


@app.get("/api/catalog/audiobook")
def api_catalog_audiobook() -> Response:
    try:
        audiobook = state.catalog_audiobook_payload(request.args.get("path"))
    except sqlite3.Error:
        return no_store_json({"error": "Audiobook details are unavailable right now."}, 500)
    if audiobook is None:
        return no_store_json({"error": "Audiobook not found"}, 404)
    return no_store_json({"ok": True, "item": audiobook})


@app.get("/api/catalog/shows")
def api_catalog_shows() -> Response:
    try:
        payload = state.catalog_shows_payload(
            request.args.get("offset"),
            request.args.get("limit"),
            request.args.get("q"),
            request.args.get("genre"),
        )
    except sqlite3.Error:
        return no_store_json({"error": "Show catalog is unavailable right now."}, 500)
    return no_store_json({"ok": True, **payload})


@app.get("/api/catalog/show")
def api_catalog_show() -> Response:
    try:
        show = state.catalog_show_payload(request.args.get("slug"))
    except sqlite3.Error:
        return no_store_json({"error": "Show details are unavailable right now."}, 500)
    if show is None:
        return no_store_json({"error": "Show not found"}, 404)
    return no_store_json({"ok": True, "show": show})


@app.get("/api/catalog/random")
def api_catalog_random() -> Response:
    try:
        item = state.catalog_random_item_payload(request.args.get("section"))
    except sqlite3.Error:
        return no_store_json({"error": "Random playback is unavailable right now."}, 500)
    if item is None:
        return no_store_json({"error": "No matching media found"}, 404)
    return no_store_json({"ok": True, "item": item})


@app.post("/api/catalog/lookup")
def api_catalog_lookup() -> Response:
    payload = request.get_json(silent=True) or {}
    try:
        result = state.catalog_lookup_payload(payload.get("paths"))
    except sqlite3.Error:
        return no_store_json({"error": "Catalog lookup is unavailable right now."}, 500)
    return no_store_json({"ok": True, **result})


@app.get("/api/upload-destinations")
@require_device_access
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
@require_device_access
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
            final_path = atomic_save_upload(uploaded, actual_path, state.upload_staging_root_path())
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
@require_device_access
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
