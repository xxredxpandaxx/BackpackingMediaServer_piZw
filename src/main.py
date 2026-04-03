from __future__ import annotations

import json
import mimetypes
import os
import socket
import threading
import time
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
DEFAULT_APP_PATH = "/app"
DEFAULT_MEDIA_ROOT = "/media"
DEFAULT_METADATA_ROOT = "/media/.nomadscreen"
DEFAULT_METADATA_INDEX_PATH = "/media/.nomadscreen/library.json"
DEFAULT_RUNTIME_CONFIG_PATH = "/nomadscreen.config.json"
DEFAULT_BIND_ADDRESS = "0.0.0.0"
DEFAULT_HTTP_PORT = 80
DEFAULT_MAX_CLIENTS = 6
DEFAULT_MAX_STREAMS = 12
DEFAULT_CLIENT_WINDOW_SECONDS = 300

SECTION_ORDER = {
    "movies": 0,
    "tv": 1,
    "music": 2,
    "audiobooks": 3,
    "documents": 4,
}


def normalize_device_name(value: str) -> str:
    return " ".join(str(value or "").split()).strip()


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


def safe_int(value: object, default: int, minimum: int = 0) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= minimum else default


def load_settings() -> dict[str, object]:
    storage_root_value = os.environ.get("NOMADSCREEN_STORAGE_ROOT", "").strip()
    storage_root = Path(storage_root_value) if storage_root_value else DEFAULT_STORAGE_ROOT
    storage_root = storage_root.expanduser()
    config_path = storage_root / DEFAULT_RUNTIME_CONFIG_PATH.lstrip("/")
    raw_config: dict[str, object] = {}
    config_source = "defaults"

    if config_path.exists():
        try:
            raw_config = json.loads(config_path.read_text(encoding="utf-8"))
            config_source = str(config_path)
        except (OSError, json.JSONDecodeError):
            raw_config = {}
            config_source = f"{config_path} (unreadable)"

    raw_name = raw_config.get("deviceName") or raw_config.get("serverName") or DEFAULT_DEVICE_NAME
    device_name = normalize_device_name(str(raw_name)) or DEFAULT_DEVICE_NAME

    wifi_password = str(
        raw_config.get("wifiPassword")
        or ((raw_config.get("wifi") or {}).get("password") if isinstance(raw_config.get("wifi"), dict) else "")
        or DEFAULT_ACCESS_POINT_PASSWORD
    )
    if wifi_password and len(wifi_password) < 8:
        wifi_password = DEFAULT_ACCESS_POINT_PASSWORD

    bind_address = (
        os.environ.get("NOMADSCREEN_BIND", "").strip()
        or str(raw_config.get("bindAddress") or DEFAULT_BIND_ADDRESS)
    )
    http_port = safe_int(
        os.environ.get("NOMADSCREEN_PORT") or raw_config.get("httpPort") or raw_config.get("port"),
        DEFAULT_HTTP_PORT,
        1,
    )
    mdns_host = sanitize_mdns_host(str(raw_config.get("mdnsHost") or "")) or derived_mdns_host(device_name)

    return {
        "storage_root": storage_root,
        "media_directory": storage_root / DEFAULT_MEDIA_ROOT.lstrip("/"),
        "config_path": config_path,
        "device_name": device_name,
        "ssid": derived_access_point_ssid(device_name),
        "wifi_password": wifi_password,
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
        "config_source": config_source,
    }


class AppState:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.settings = load_settings()
        self.media_library: list[dict[str, object]] = []
        self.item_metadata: list[dict[str, object]] = []
        self.show_metadata: dict[str, dict[str, object]] = {}
        self.metadata_available = False
        self.metadata_generated_at = ""
        self.metadata_generator = ""
        self.last_played_title = ""
        self.last_played_type = ""
        self.last_played_at = 0.0
        self.active_streams = 0
        self.recent_clients: dict[str, float] = {}
        self.storage_ready = False
        self.scan_library()

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

    def compose_url(self, host: str, port: int, suffix: str = "") -> str:
        safe_host = str(host or "").strip() or "127.0.0.1"
        safe_suffix = suffix if suffix.startswith("/") or not suffix else "/" + suffix
        if port in {80, 443}:
            return f"http://{safe_host}{safe_suffix}"
        return f"http://{safe_host}:{port}{safe_suffix}"

    def resolve_virtual_path(self, virtual_path: str) -> Path | None:
        normalized = normalize_virtual_path(virtual_path)
        if not normalized or not normalized.startswith(DEFAULT_MEDIA_ROOT):
            return None
        storage_root = Path(self.settings["storage_root"]).resolve(strict=False)
        candidate = (storage_root / normalized.lstrip("/")).resolve(strict=False)
        storage_text = str(storage_root).lower()
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
        storage_root = Path(self.settings["storage_root"])
        media_directory = Path(self.settings["media_directory"])
        scanned_items: list[dict[str, object]] = []
        storage_ready = media_directory.exists() and media_directory.is_dir()

        if storage_ready:
            for root, dirs, files in os.walk(media_directory):
                dirs[:] = [directory for directory in dirs if directory.lower() != ".nomadscreen"]
                for file_name in files:
                    actual_path = Path(root) / file_name
                    try:
                        virtual_path = "/" + actual_path.relative_to(storage_root).as_posix()
                    except ValueError:
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

        with self.lock:
            self.media_library = scanned_items
            self.item_metadata = item_entries
            self.show_metadata = show_entries
            self.metadata_generated_at = generated_at
            self.metadata_generator = generator
            self.metadata_available = bool(item_entries or show_entries)
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

    def status_payload(self) -> dict[str, object]:
        local_ip = self.best_local_ip()
        port = int(self.settings["http_port"])
        mdns_host = f"{self.settings['mdns_host']}.local"
        ip_app_url = self.compose_url(local_ip, port, DEFAULT_APP_PATH)
        mdns_url = self.compose_url(mdns_host, port, DEFAULT_APP_PATH)
        return {
            "device": self.settings["device_name"],
            "ssid": self.settings["ssid"],
            "password": self.settings["wifi_password"],
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
            "mediaRoot": DEFAULT_MEDIA_ROOT,
            "clients": self.active_client_count(),
            "lastPlayed": self.last_played_title,
            "lastPlayedType": self.last_played_type,
            "metadataAvailable": self.metadata_available,
            "metadataGeneratedAt": self.metadata_generated_at,
            "metadataGenerator": self.metadata_generator,
            "metadataItemCount": len(self.item_metadata),
            "metadataShowCount": len(self.show_metadata),
            "platform": "raspberry-pi-zero-w",
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


@app.get("/api/library")
def api_library() -> Response:
    if not state.storage_ready:
        return no_store_json({"error": "Media storage unavailable"}, 503)
    return no_store_json(state.library_payload())


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


@app.post("/api/rescan")
def api_rescan() -> Response:
    state.scan_library()
    return no_store_json({"ok": True})


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
