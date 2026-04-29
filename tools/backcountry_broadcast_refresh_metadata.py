from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import requests
from rapidfuzz import fuzz

SCRIPT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = SCRIPT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from audiobook_metadata import extract_audiobook_embedded_metadata


GENERATOR_NAME = "Backcountry Broadcast Python Metadata Builder"
MATCHER_VERSION = "title-matcher-v2"
TMDB_API_ROOT = "https://api.themoviedb.org/3"
TMDB_IMAGE_ROOT = "https://image.tmdb.org/t/p"
DEFAULT_STORAGE_ROOT = SCRIPT_ROOT / ".backcountry-broadcast-runtime"
LEGACY_STORAGE_ROOT = SCRIPT_ROOT / ".nomadscreen-runtime"
DEFAULT_RUNTIME_CONFIG_NAME = "backcountry-broadcast.config.json"
LEGACY_RUNTIME_CONFIG_NAME = "nomadscreen.config.json"
DEFAULT_RUNTIME_USER_CONFIG_NAME = "backcountry-broadcast.user.json"
LEGACY_RUNTIME_USER_CONFIG_NAME = "nomadscreen.user.json"
DEFAULT_METADATA_DIRECTORY_NAME = ".backcountry-broadcast"
LEGACY_METADATA_DIRECTORY_NAME = ".nomadscreen"

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".webm", ".m4v", ".avi"}
AUDIO_EXTENSIONS = {".mp3", ".m4a", ".m4b", ".aac", ".wav", ".flac", ".ogg"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
DOCUMENT_EXTENSIONS = {".pdf", ".txt", ".md", ".csv", ".gpx", ".kml", ".doc", ".docx"}


def log(message: str) -> None:
    print(f"[backcountry-broadcast-metadata] {message}")


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


def configure_sqlite_connection(connection: sqlite3.Connection) -> sqlite3.Connection:
    connection.execute("PRAGMA busy_timeout = 5000")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = FULL")
    connection.execute("PRAGMA wal_autocheckpoint = 200")
    return connection


def normalize_virtual_path(raw_path: str) -> str:
    normalized = str(raw_path or "").strip().replace("\\", "/")
    if not normalized:
        return ""
    if not normalized.startswith("/"):
        normalized = "/" + normalized
    while "//" in normalized:
        normalized = normalized.replace("//", "/")
    pieces: list[str] = []
    for piece in normalized.split("/"):
        if not piece or piece == ".":
            continue
        if piece == "..":
            if pieces:
                pieces.pop()
            continue
        pieces.append(piece)
    return "/" + "/".join(pieces)


def split_virtual_path(path: str) -> list[str]:
    return [segment for segment in normalize_virtual_path(path).split("/") if segment]


def normalize_spacing(value: str) -> str:
    cleaned = []
    previous_space = False
    for character in str(value or "").strip():
        if character in {" ", "_", "-", "."}:
            if cleaned and not previous_space:
                cleaned.append(" ")
            previous_space = True
            continue
        cleaned.append(character)
        previous_space = False
    return "".join(cleaned).strip()


def prettify_name(value: str) -> str:
    pretty = normalize_spacing(value)
    return pretty or str(value or "")


def slugify(value: str) -> str:
    output = []
    previous_dash = False
    for character in str(value or ""):
        if character.isalnum():
            output.append(character.lower())
            previous_dash = False
        elif output and not previous_dash:
            output.append("-")
            previous_dash = True
    return "".join(output).rstrip("-") or "library-item"


def get_media_type(path: Path) -> str:
    lowered = path.suffix.lower()
    if lowered in VIDEO_EXTENSIONS:
        return "video"
    if lowered in AUDIO_EXTENSIONS:
        return "audio"
    if lowered in IMAGE_EXTENSIONS:
        return "image"
    if lowered in DOCUMENT_EXTENSIONS:
        return "document"
    return ""


def get_media_section(virtual_path: str, media_type: str) -> str:
    lowered = normalize_virtual_path(virtual_path).lower()
    if lowered.startswith("/media/movies/"):
        return "movies"
    if lowered.startswith("/media/tv/"):
        return "tv"
    if lowered.startswith("/media/music/"):
        return "music"
    if lowered.startswith("/media/audiobooks/"):
        return "audiobooks"
    if lowered.startswith("/media/documents/"):
        return "documents"
    if media_type == "video":
        return "movies"
    if media_type == "audio":
        return "music"
    if media_type in {"image", "document"}:
        return "documents"
    return "library"


def get_year_from_text(value: str) -> str:
    current_year = datetime.now(timezone.utc).year
    matches = re.findall(r"(19\d{2}|20\d{2})", str(value or ""))
    candidates = [year for year in matches if 1900 <= int(year) <= current_year + 1]
    return candidates[-1] if candidates else ""


def parse_season_number(label: str) -> int:
    lowered = str(label or "").lower()
    if lowered.startswith("special"):
        return 0
    digits = "".join(character for character in lowered if character.isdigit())
    return int(digits) if digits else 1


def parse_episode_number(name: str) -> int:
    upper = str(name or "").upper()
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


def clean_lookup_title(filename: str) -> str:
    name = Path(str(filename or "")).stem
    name = re.sub(r"\b(2160p|1080p|720p|480p|4k|x264|x265|h264|h265|hdrip|brrip|bluray|webrip|web-dl|yts|rarbg)\b", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\[[^\]]*\]", "", name)
    name = re.sub(r"\((?!\d{4}\)).*?\)", "", name)
    name = re.sub(r"[._]+", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name.lower()


def strip_year_from_lookup_title(title: str, year: int | None) -> str:
    cleaned = str(title or "").strip().lower()
    if year:
        cleaned = re.sub(rf"(?<!\d)[\(\[]?\s*{year}\s*[\)\]]?(?!\d)", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(19\d{2}|20\d{2})\b", " ", cleaned)
    cleaned = re.sub(r"[\(\)\[\]]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


ROMAN_NUMERAL_PATTERN = r"(?:i|ii|iii|iv|v|vi|vii|viii|ix|x|xi|xii|xiii|xiv|xv|xvi|xvii|xviii|xix|xx)"


def normalize_match_text(value: object) -> str:
    output: list[str] = []
    for character in unicodedata.normalize("NFKD", str(value or "")):
        if unicodedata.category(character) == "Mn":
            continue
        if character.isalnum():
            output.append(character.lower())
        else:
            output.append(" ")
    return re.sub(r"\s+", " ", "".join(output)).strip()


def add_unique_text(values: list[str], seen: set[str], value: object) -> None:
    normalized = normalize_match_text(value)
    if normalized and normalized not in seen:
        values.append(normalized)
        seen.add(normalized)


def expanded_title_queries(title: str, year: int | None) -> list[str]:
    base = strip_year_from_lookup_title(title, year)
    candidates: list[str] = []
    seen: set[str] = set()

    add_unique_text(candidates, seen, base)

    without_episode = re.sub(
        rf"\bepisode\s+{ROMAN_NUMERAL_PATTERN}\b",
        " ",
        base,
        flags=re.IGNORECASE,
    )
    without_episode = re.sub(r"\bepisode\s+\d+\b", " ", without_episode, flags=re.IGNORECASE)
    add_unique_text(candidates, seen, without_episode)

    for separator in (" - ", ":"):
        if separator in base:
            pieces = [piece.strip() for piece in base.split(separator) if piece.strip()]
            for piece in pieces:
                add_unique_text(candidates, seen, piece)
            if len(pieces) > 1:
                add_unique_text(candidates, seen, pieces[-1])

    return candidates


def tmdb_search_queries(title: str, year: int | None) -> list[str]:
    return expanded_title_queries(title, year)


def get_runtime_minutes(file_path: Path) -> float:
    try:
        completed = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(file_path),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return 0.0
    if completed.returncode != 0:
        return 0.0
    try:
        return round(float(completed.stdout.strip()) / 60.0, 1)
    except (TypeError, ValueError):
        return 0.0


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


def default_runtime_config() -> dict[str, object]:
    return {
        "tmdbApiKey": "",
        "tmdbBearerToken": "",
        "language": "en-US",
        "country": "US",
        "downloadImages": True,
        "overwriteImages": False,
        "minimumMatchScore": 0.55,
    }


def read_runtime_config(config_path: Path) -> dict[str, object]:
    if not config_path.exists():
        return {}
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def merge_runtime_config_values(base: object, override: object) -> object:
    if isinstance(base, dict) and isinstance(override, dict):
        merged = dict(base)
        for key, value in override.items():
            merged[key] = merge_runtime_config_values(merged.get(key), value) if key in merged else value
        return merged
    return override


def runtime_config_path(storage_root: Path) -> Path:
    preferred = storage_root / DEFAULT_RUNTIME_CONFIG_NAME
    legacy = storage_root / LEGACY_RUNTIME_CONFIG_NAME
    if preferred.exists() or not legacy.exists():
        return preferred
    return legacy


def runtime_user_config_path(storage_root: Path) -> Path:
    preferred = storage_root / DEFAULT_RUNTIME_USER_CONFIG_NAME
    legacy = storage_root / LEGACY_RUNTIME_USER_CONFIG_NAME
    if preferred.exists() or not legacy.exists():
        return preferred
    return legacy


def metadata_root_path(media_root: Path) -> Path:
    return media_root / DEFAULT_METADATA_DIRECTORY_NAME


def title_values_from_movie_details(details: dict[str, object]) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    for key in ("title", "original_title"):
        add_unique_text(values, seen, details.get(key))

    alternative_titles = details.get("alternative_titles")
    if isinstance(alternative_titles, dict):
        title_entries = alternative_titles.get("titles")
        if isinstance(title_entries, list):
            for entry in title_entries:
                if isinstance(entry, dict):
                    add_unique_text(values, seen, entry.get("title"))
    return values


def title_similarity_score(local_titles: list[str], remote_titles: list[str]) -> float:
    score = 0.0
    for local_title in local_titles:
        for remote_title in remote_titles:
            if not local_title or not remote_title:
                continue
            score = max(
                score,
                fuzz.token_sort_ratio(local_title, remote_title) / 100.0,
                fuzz.token_set_ratio(local_title, remote_title) / 100.0,
                fuzz.partial_ratio(local_title, remote_title) / 100.0,
            )
    return score


class TmdbClient:
    def __init__(self, config: dict[str, object]) -> None:
        self.api_key = str(config.get("tmdbApiKey") or "").strip()
        self.bearer_token = str(config.get("tmdbBearerToken") or "").strip()
        self.language = str(config.get("language") or "en-US").strip() or "en-US"
        self.country = str(config.get("country") or "US").strip().upper() or "US"
        self.minimum_match_score = float(config.get("minimumMatchScore") or 0.55)
        self.download_images = config_bool(config.get("downloadImages"), True)
        self.overwrite_images = config_bool(config.get("overwriteImages"), False)
        self.enabled = bool(self.api_key or self.bearer_token)
        self.session = requests.Session()

    def request(self, path: str, params: dict[str, object] | None = None) -> dict[str, object]:
        query = dict(params or {})
        query.setdefault("language", self.language)
        headers: dict[str, str] = {}
        if self.bearer_token:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
        elif self.api_key:
            query["api_key"] = self.api_key
        response = self.session.get(f"{TMDB_API_ROOT}/{path.lstrip('/')}", params=query, headers=headers, timeout=20)
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    def search_movie(self, title: str, year: int | None) -> list[dict[str, object]]:
        if not title:
            return []
        collected: list[dict[str, object]] = []
        seen_ids: set[int] = set()
        for query_title in tmdb_search_queries(title, year):
            query_plans = [(query_title, year)] if year else []
            query_plans.append((query_title, None))
            for safe_query, safe_year in query_plans:
                params: dict[str, object] = {"query": safe_query}
                if safe_year:
                    params["year"] = safe_year
                payload = self.request("search/movie", params=params)
                results = payload.get("results")
                if not isinstance(results, list):
                    continue
                plan_count = 0
                for entry in results:
                    if not isinstance(entry, dict):
                        continue
                    movie_id = int(entry.get("id") or 0)
                    if movie_id > 0 and movie_id in seen_ids:
                        continue
                    if movie_id > 0:
                        seen_ids.add(movie_id)
                    collected.append(entry)
                    plan_count += 1
                    if plan_count >= 5:
                        break
                if len(collected) >= 30:
                    break
            if len(collected) >= 30:
                break
        return collected

    def search_tv(self, title: str) -> list[dict[str, object]]:
        if not title:
            return []
        collected: list[dict[str, object]] = []
        seen_ids: set[int] = set()
        for query_title in tmdb_search_queries(title, None):
            payload = self.request("search/tv", params={"query": query_title})
            results = payload.get("results")
            if not isinstance(results, list):
                continue
            for entry in results:
                if not isinstance(entry, dict):
                    continue
                show_id = int(entry.get("id") or 0)
                if show_id > 0 and show_id in seen_ids:
                    continue
                if show_id > 0:
                    seen_ids.add(show_id)
                collected.append(entry)
            if collected:
                break
        return collected

    def movie_details(self, movie_id: int) -> dict[str, object]:
        return self.request(
            f"movie/{movie_id}",
            params={"append_to_response": "credits,videos,keywords,release_dates,alternative_titles"},
        )

    def tv_details(self, show_id: int) -> dict[str, object]:
        return self.request(
            f"tv/{show_id}",
            params={"append_to_response": "content_ratings,credits,videos,keywords"},
        )


def extract_certification(details: dict[str, object], country: str) -> str:
    release_dates = details.get("release_dates")
    if not isinstance(release_dates, dict):
        return ""
    results = release_dates.get("results")
    if not isinstance(results, list):
        return ""
    target_country = str(country or "US").upper() or "US"
    for entry in results:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("iso_3166_1") or "").upper() != target_country:
            continue
        dates = entry.get("release_dates")
        if not isinstance(dates, list):
            continue
        for date_entry in dates:
            if not isinstance(date_entry, dict):
                continue
            certification = str(date_entry.get("certification") or "").strip()
            if certification:
                return certification
    return ""


def safe_image_extension(remote_path: str) -> str:
    suffix = Path(str(remote_path or "")).suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".webp"}:
        return suffix
    return ".jpg"


def comma_join(values: list[str]) -> str:
    return ", ".join(value for value in values if str(value or "").strip())


def names_from_entries(entries: object, key: str = "name", limit: int = 0) -> list[str]:
    if not isinstance(entries, list):
        return []
    values: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        value = str(entry.get(key) or "").strip()
        if value:
            values.append(value)
        if limit > 0 and len(values) >= limit:
            break
    return values


def extract_keywords(details: dict[str, object]) -> str:
    keywords_block = details.get("keywords")
    if not isinstance(keywords_block, dict):
        return ""
    values = keywords_block.get("keywords")
    if not isinstance(values, list):
        values = keywords_block.get("results")
    return comma_join(names_from_entries(values))


def extract_director(details: dict[str, object]) -> str:
    credits = details.get("credits")
    if not isinstance(credits, dict):
        return ""
    crew = credits.get("crew")
    if not isinstance(crew, list):
        return ""
    return comma_join(
        [
            str(entry.get("name") or "").strip()
            for entry in crew
            if isinstance(entry, dict) and str(entry.get("job") or "").strip().lower() == "director"
        ]
    )


def extract_cast(details: dict[str, object], limit: int = 5) -> str:
    credits = details.get("credits")
    if not isinstance(credits, dict):
        return ""
    return comma_join(names_from_entries(credits.get("cast"), limit=limit))


def extract_video_urls(details: dict[str, object]) -> str:
    videos = details.get("videos")
    if not isinstance(videos, dict):
        return ""
    results = videos.get("results")
    if not isinstance(results, list):
        return ""
    urls: list[str] = []
    for entry in results:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("site") or "").strip().lower() != "youtube":
            continue
        video_key = str(entry.get("key") or "").strip()
        if video_key:
            urls.append(f"https://www.youtube.com/watch?v={video_key}")
    return comma_join(urls)


def extract_tv_certification(details: dict[str, object], country: str) -> str:
    content_ratings = details.get("content_ratings")
    if not isinstance(content_ratings, dict):
        return ""
    results = content_ratings.get("results")
    if not isinstance(results, list):
        return ""
    target_country = str(country or "US").upper() or "US"
    for entry in results:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("iso_3166_1") or "").upper() != target_country:
            continue
        rating = str(entry.get("rating") or "").strip()
        if rating:
            return rating
    return ""


def extract_networks(details: dict[str, object]) -> str:
    return comma_join(names_from_entries(details.get("networks")))


def extract_creators(details: dict[str, object]) -> str:
    return comma_join(names_from_entries(details.get("created_by")))


def detect_resolution_label(filename: str) -> str:
    match = re.search(r"(2160p|1080p|720p|480p)", str(filename or ""), flags=re.IGNORECASE)
    return str(match.group(1) or "").upper() if match else ""


def save_tmdb_image(
    client: TmdbClient,
    remote_path: str,
    actual_directory: Path,
    virtual_directory: str,
    base_name: str,
    size: str,
) -> str:
    clean_remote_path = str(remote_path or "").strip()
    if not clean_remote_path:
        return ""
    actual_directory.mkdir(parents=True, exist_ok=True)
    file_name = f"{base_name}{safe_image_extension(clean_remote_path)}"
    actual_path = actual_directory / file_name
    virtual_path = normalize_virtual_path(f"{virtual_directory}/{file_name}")
    if actual_path.exists() and not client.overwrite_images:
        return virtual_path
    if not client.download_images:
        return virtual_path if actual_path.exists() else ""
    response = client.session.get(f"{TMDB_IMAGE_ROOT}/{size}{clean_remote_path}", stream=True, timeout=20)
    response.raise_for_status()
    with actual_path.open("wb") as handle:
        for chunk in response.iter_content(8192):
            if chunk:
                handle.write(chunk)
    return virtual_path


def build_base_item(file_path: Path, media_root: Path) -> dict[str, object]:
    relative = file_path.resolve(strict=False).relative_to(media_root.resolve(strict=False)).as_posix()
    virtual_path = normalize_virtual_path(f"/media/{relative}")
    media_type = get_media_type(file_path)
    section = get_media_section(virtual_path, media_type)
    title = prettify_name(file_path.stem)
    item: dict[str, object] = {
        "path": virtual_path,
        "type": media_type,
        "section": section,
        "extension": file_path.suffix.lstrip(".").upper(),
        "bytes": int(file_path.stat().st_size),
        "title": title,
        "sortTitle": title,
        "overview": "",
        "tagline": "",
        "year": get_year_from_text(file_path.name),
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
        "source": "local",
        "tmdbRating": 0.0,
        "runtimeMinutes": 0.0,
        "matchConfidence": 0.0,
        "showTitle": "",
        "showSlug": "",
        "seasonLabel": "",
        "seasonNumber": 0,
        "episodeNumber": 0,
    }
    parts = split_virtual_path(virtual_path)
    if section == "tv":
        show_title = prettify_name(parts[2]) if len(parts) >= 3 else "Unknown Show"
        season_label = prettify_name(parts[3]) if len(parts) >= 4 else "Season 1"
        item["showTitle"] = show_title
        item["showSlug"] = slugify(show_title)
        item["year"] = get_year_from_text(show_title) or str(item.get("year") or "")
        item["seasonLabel"] = season_label
        item["seasonNumber"] = parse_season_number(season_label)
        item["episodeNumber"] = parse_episode_number(file_path.name)
    elif section in {"music", "audiobooks"}:
        if len(parts) >= 4:
            item["artist"] = prettify_name(parts[2])
            item["album"] = prettify_name(parts[3])
        elif len(parts) >= 3:
            item["artist"] = prettify_name(parts[2])
    if section == "audiobooks":
        embedded = extract_audiobook_embedded_metadata(file_path, metadata_root_path(media_root))
        for key, value in embedded.items():
            if isinstance(value, str):
                if value:
                    item[key] = value
            elif isinstance(value, (int, float)):
                if float(value) > 0:
                    item[key] = value
            elif value:
                item[key] = value
    return item


def compute_movie_score(
    local_title: str,
    local_year: int | None,
    local_runtime: float,
    details: dict[str, object],
) -> float:
    local_title_candidates = tmdb_search_queries(local_title, local_year)
    if not local_title_candidates:
        local_title_candidates = [normalize_match_text(local_title)]
    title_similarity = title_similarity_score(local_title_candidates, title_values_from_movie_details(details))

    score = 0.0
    if title_similarity >= 0.9:
        score += 0.5
    elif title_similarity >= 0.8:
        score += 0.3
    elif title_similarity >= 0.7:
        score += 0.15

    release_date = str(details.get("release_date") or "")
    tmdb_year = int(release_date[:4]) if len(release_date) >= 4 and release_date[:4].isdigit() else None
    if local_year and tmdb_year:
        if local_year == tmdb_year:
            score += 0.3
        elif abs(local_year - tmdb_year) <= 1:
            score += 0.2

    tmdb_runtime = float(details.get("runtime") or 0.0)
    if local_runtime > 0 and tmdb_runtime > 0:
        difference = abs(local_runtime - tmdb_runtime)
        if difference <= 1:
            score += 0.4
        elif difference <= 5:
            score += 0.2
    return round(score, 2)


def compute_tv_score(
    local_title: str,
    local_year: int | None,
    details: dict[str, object],
) -> float:
    local_title_candidates = tmdb_search_queries(local_title, local_year)
    if not local_title_candidates:
        local_title_candidates = [normalize_match_text(local_title)]
    title_similarity = title_similarity_score(
        local_title_candidates,
        [normalize_match_text(details.get("name")), normalize_match_text(details.get("original_name"))],
    )
    score = 0.0
    if title_similarity >= 0.9:
        score += 0.7
    elif title_similarity >= 0.8:
        score += 0.5
    elif title_similarity >= 0.7:
        score += 0.25

    first_air_date = str(details.get("first_air_date") or "")
    tmdb_year = int(first_air_date[:4]) if len(first_air_date) >= 4 and first_air_date[:4].isdigit() else None
    if local_year and tmdb_year:
        if local_year == tmdb_year:
            score += 0.3
        elif abs(local_year - tmdb_year) <= 1:
            score += 0.15
    return round(score, 2)


def enrich_movie_item(
    item: dict[str, object],
    file_path: Path,
    client: TmdbClient,
    metadata_root: Path,
    unmatched: list[dict[str, object]],
) -> None:
    local_title = clean_lookup_title(file_path.name) or str(item["title"]).lower()
    local_year_text = str(item.get("year") or "")
    local_year = int(local_year_text) if local_year_text.isdigit() else None
    local_runtime = get_runtime_minutes(file_path)
    if local_runtime > 0:
        item["runtimeMinutes"] = local_runtime

    candidates = client.search_movie(local_title, local_year)[:30]
    best_details: dict[str, object] | None = None
    best_score = 0.0
    close_candidates: list[dict[str, object]] = []

    for candidate in candidates:
        movie_id = int(candidate.get("id") or 0)
        if movie_id <= 0:
            continue
        try:
            details = client.movie_details(movie_id)
        except requests.RequestException:
            continue
        score = compute_movie_score(local_title, local_year, local_runtime, details)
        close_candidates.append(
            {
                "title": str(details.get("title") or ""),
                "score": score,
            }
        )
        if score > best_score:
            best_score = score
            best_details = details

    if best_details is None or best_score < client.minimum_match_score:
        unmatched.append(
            {
                "path": item["path"],
                "section": item["section"],
                "matcherVersion": MATCHER_VERSION,
                "query": local_title,
                "searchQueries": tmdb_search_queries(local_title, local_year),
                "year": local_year,
                "runtimeMinutes": local_runtime,
                "candidates": close_candidates,
            }
        )
        return

    release_date = str(best_details.get("release_date") or "")
    genres_text = comma_join(
        names_from_entries(best_details.get("genres"))
    )
    original_title = str(best_details.get("original_title") or "").strip()
    imdb_id = str(best_details.get("imdb_id") or "").strip()
    status = str(best_details.get("status") or "").strip()
    original_language = str(best_details.get("original_language") or "").strip()
    keywords = extract_keywords(best_details)
    production_companies = comma_join(names_from_entries(best_details.get("production_companies")))
    production_countries = comma_join(names_from_entries(best_details.get("production_countries")))
    spoken_languages = comma_join(names_from_entries(best_details.get("spoken_languages"), key="english_name"))
    if not spoken_languages:
        spoken_languages = comma_join(names_from_entries(best_details.get("spoken_languages"), key="name"))
    cast_members = extract_cast(best_details, limit=5)
    director = extract_director(best_details)
    videos = extract_video_urls(best_details)
    resolution = detect_resolution_label(file_path.name)

    item["title"] = str(best_details.get("title") or item["title"])
    item["sortTitle"] = item["title"]
    item["overview"] = str(best_details.get("overview") or "")
    item["tagline"] = str(best_details.get("tagline") or "")
    item["year"] = release_date[:4] if len(release_date) >= 4 else str(item.get("year") or "")
    item["releaseDate"] = release_date
    item["genres"] = genres_text
    item["contentRating"] = extract_certification(best_details, client.country)
    item["source"] = "tmdb"
    item["tmdbRating"] = round(float(best_details.get("vote_average") or 0.0), 1)
    if not item["runtimeMinutes"] and float(best_details.get("runtime") or 0.0) > 0:
        item["runtimeMinutes"] = round(float(best_details.get("runtime") or 0.0), 1)
    item["matchConfidence"] = best_score
    item["tmdbId"] = int(best_details.get("id") or 0)
    item["imdbId"] = imdb_id
    item["originalTitle"] = original_title
    item["status"] = status
    item["originalLanguage"] = original_language
    item["keywords"] = keywords
    item["productionCompanies"] = production_companies
    item["productionCountries"] = production_countries
    item["spokenLanguages"] = spoken_languages
    item["cast"] = cast_members
    item["director"] = director
    item["videos"] = videos
    item["budget"] = int(best_details.get("budget") or 0)
    item["revenue"] = int(best_details.get("revenue") or 0)
    item["popularity"] = round(float(best_details.get("popularity") or 0.0), 3)
    item["voteCount"] = int(best_details.get("vote_count") or 0)
    item["resolution"] = resolution

    movie_id = int(best_details.get("id") or 0)
    if movie_id > 0:
        poster_path = save_tmdb_image(
            client,
            str(best_details.get("poster_path") or ""),
            metadata_root / "posters",
            "/media/.backcountry-broadcast/posters",
            f"movie-{movie_id}",
            "w500",
        )
        backdrop_path = save_tmdb_image(
            client,
            str(best_details.get("backdrop_path") or ""),
            metadata_root / "backdrops",
            "/media/.backcountry-broadcast/backdrops",
            f"movie-{movie_id}",
            "w780",
        )
        if poster_path:
            item["posterPath"] = poster_path
        if backdrop_path:
            item["backdropPath"] = backdrop_path


def enrich_show_record(
    show: dict[str, object],
    client: TmdbClient,
    metadata_root: Path,
    unmatched: list[dict[str, object]],
) -> None:
    local_title = clean_lookup_title(str(show.get("title") or "")) or str(show.get("title") or "").strip().lower()
    local_year_text = str(show.get("year") or "")
    local_year = int(local_year_text) if local_year_text.isdigit() else None

    candidates = client.search_tv(local_title)[:8]
    best_details: dict[str, object] | None = None
    best_score = 0.0
    close_candidates: list[dict[str, object]] = []

    for candidate in candidates:
        show_id = int(candidate.get("id") or 0)
        if show_id <= 0:
            continue
        try:
            details = client.tv_details(show_id)
        except requests.RequestException:
            continue
        score = compute_tv_score(local_title, local_year, details)
        close_candidates.append(
            {
                "title": str(details.get("name") or ""),
                "score": score,
            }
        )
        if score > best_score:
            best_score = score
            best_details = details

    if best_details is None or best_score < client.minimum_match_score:
        unmatched.append(
            {
                "path": f"/media/tv/{slugify(str(show.get('title') or 'show'))}",
                "section": "tv",
                "matcherVersion": MATCHER_VERSION,
                "query": local_title,
                "searchQueries": tmdb_search_queries(local_title, local_year),
                "year": local_year,
                "candidates": close_candidates,
            }
        )
        return

    first_air_date = str(best_details.get("first_air_date") or "")
    original_name = str(best_details.get("original_name") or "").strip()
    status = str(best_details.get("status") or "").strip()
    original_language = str(best_details.get("original_language") or "").strip()
    genres_text = comma_join(names_from_entries(best_details.get("genres")))
    keywords = extract_keywords(best_details)
    cast_members = extract_cast(best_details, limit=8)
    creators = extract_creators(best_details)
    networks = extract_networks(best_details)
    videos = extract_video_urls(best_details)

    show["title"] = str(best_details.get("name") or show.get("title") or "")
    show["overview"] = str(best_details.get("overview") or "")
    show["year"] = first_air_date[:4] if len(first_air_date) >= 4 else str(show.get("year") or "")
    show["genres"] = genres_text
    show["contentRating"] = extract_tv_certification(best_details, client.country)
    show["source"] = "tmdb"
    show["tmdbRating"] = round(float(best_details.get("vote_average") or 0.0), 1)
    show["matchConfidence"] = best_score
    show["tmdbId"] = int(best_details.get("id") or 0)
    show["originalTitle"] = original_name
    show["firstAirDate"] = first_air_date
    show["status"] = status
    show["originalLanguage"] = original_language
    show["keywords"] = keywords
    show["cast"] = cast_members
    show["creators"] = creators
    show["networks"] = networks
    show["videos"] = videos
    show["seasonCount"] = int(best_details.get("number_of_seasons") or show.get("seasonCount") or 0)
    show["episodeCount"] = int(best_details.get("number_of_episodes") or show.get("episodeCount") or 0)
    show["popularity"] = round(float(best_details.get("popularity") or 0.0), 3)
    show["voteCount"] = int(best_details.get("vote_count") or 0)

    show_id = int(best_details.get("id") or 0)
    if show_id > 0:
        poster_path = save_tmdb_image(
            client,
            str(best_details.get("poster_path") or ""),
            metadata_root / "posters",
            "/media/.backcountry-broadcast/posters",
            f"show-{show_id}",
            "w500",
        )
        backdrop_path = save_tmdb_image(
            client,
            str(best_details.get("backdrop_path") or ""),
            metadata_root / "backdrops",
            "/media/.backcountry-broadcast/backdrops",
            f"show-{show_id}",
            "w780",
        )
        if poster_path:
            show["posterPath"] = poster_path
        if backdrop_path:
            show["backdropPath"] = backdrop_path


def apply_show_metadata_to_items(items: list[dict[str, object]], shows: list[dict[str, object]]) -> None:
    show_map = {
        str(show.get("slug") or ""): show
        for show in shows
        if str(show.get("slug") or "")
    }
    for item in items:
        if str(item.get("section") or "") != "tv":
            continue
        show = show_map.get(str(item.get("showSlug") or ""))
        if show is None:
            continue
        item["showTitle"] = str(show.get("title") or item.get("showTitle") or "")
        if not str(item.get("year") or ""):
            item["year"] = str(show.get("year") or "")
        item["overview"] = str(show.get("overview") or item.get("overview") or "")
        item["genres"] = str(show.get("genres") or item.get("genres") or "")
        item["contentRating"] = str(show.get("contentRating") or item.get("contentRating") or "")
        item["source"] = str(show.get("source") or item.get("source") or "local")
        if float(item.get("tmdbRating") or 0.0) <= 0 and float(show.get("tmdbRating") or 0.0) > 0:
            item["tmdbRating"] = float(show.get("tmdbRating") or 0.0)
        if float(item.get("matchConfidence") or 0.0) <= 0 and float(show.get("matchConfidence") or 0.0) > 0:
            item["matchConfidence"] = float(show.get("matchConfidence") or 0.0)
        if not str(item.get("posterPath") or "") and str(show.get("posterPath") or ""):
            item["posterPath"] = str(show.get("posterPath") or "")
        if not str(item.get("backdropPath") or "") and str(show.get("backdropPath") or ""):
            item["backdropPath"] = str(show.get("backdropPath") or "")


def build_show_records(items: list[dict[str, object]]) -> list[dict[str, object]]:
    show_map: dict[str, dict[str, object]] = {}
    for item in items:
        if str(item.get("section") or "") != "tv":
            continue
        slug = str(item.get("showSlug") or "")
        if not slug:
            continue
        record = show_map.setdefault(
            slug,
            {
                "slug": slug,
                "title": str(item.get("showTitle") or ""),
                "year": str(item.get("year") or ""),
                "overview": "",
                "genres": str(item.get("genres") or ""),
                "contentRating": str(item.get("contentRating") or ""),
                "posterPath": str(item.get("posterPath") or ""),
                "backdropPath": str(item.get("backdropPath") or ""),
                "source": str(item.get("source") or "local"),
                "tmdbRating": float(item.get("tmdbRating") or 0.0),
                "matchConfidence": float(item.get("matchConfidence") or 0.0),
                "seasonCount": 0,
                "episodeCount": 0,
                "_seasonKeys": set(),
            },
        )
        season_key = f"{int(item.get('seasonNumber') or 0)}|{str(item.get('seasonLabel') or '').strip().lower()}"
        if season_key not in record["_seasonKeys"]:
            record["_seasonKeys"].add(season_key)
            record["seasonCount"] = int(record.get("seasonCount") or 0) + 1
        record["episodeCount"] = int(record.get("episodeCount") or 0) + 1
        if not record["year"] and item.get("year"):
            record["year"] = str(item.get("year") or "")
        if not record["genres"] and item.get("genres"):
            record["genres"] = str(item.get("genres") or "")
        if not record["contentRating"] and item.get("contentRating"):
            record["contentRating"] = str(item.get("contentRating") or "")
        if not record["posterPath"] and item.get("posterPath"):
            record["posterPath"] = str(item.get("posterPath") or "")
        if not record["backdropPath"] and item.get("backdropPath"):
            record["backdropPath"] = str(item.get("backdropPath") or "")
        if float(record["tmdbRating"] or 0.0) <= 0 and float(item.get("tmdbRating") or 0.0) > 0:
            record["tmdbRating"] = float(item.get("tmdbRating") or 0.0)
        if float(record["matchConfidence"] or 0.0) <= 0 and float(item.get("matchConfidence") or 0.0) > 0:
            record["matchConfidence"] = float(item.get("matchConfidence") or 0.0)
        if str(item.get("source") or "") == "tmdb":
            record["source"] = "tmdb"
    for record in show_map.values():
        record.pop("_seasonKeys", None)
    return sorted(show_map.values(), key=lambda entry: str(entry.get("title") or "").lower())


def write_movie_metadata_database(metadata_root: Path, items: list[dict[str, object]]) -> Path:
    db_path = metadata_root / "library.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    rows = [
        (
            str(item.get("path") or ""),
            int(item.get("tmdbId") or 0),
            str(item.get("imdbId") or ""),
            str(item.get("title") or ""),
            str(item.get("originalTitle") or ""),
            str(item.get("tagline") or ""),
            str(item.get("overview") or ""),
            str(item.get("year") or ""),
            str(item.get("releaseDate") or ""),
            float(item.get("runtimeMinutes") or 0.0),
            str(item.get("status") or ""),
            str(item.get("originalLanguage") or ""),
            str(item.get("genres") or ""),
            str(item.get("keywords") or ""),
            str(item.get("productionCompanies") or ""),
            str(item.get("productionCountries") or ""),
            str(item.get("spokenLanguages") or ""),
            str(item.get("cast") or ""),
            str(item.get("director") or ""),
            str(item.get("videos") or ""),
            int(item.get("budget") or 0),
            int(item.get("revenue") or 0),
            float(item.get("popularity") or 0.0),
            float(item.get("tmdbRating") or 0.0),
            int(item.get("voteCount") or 0),
            str(item.get("contentRating") or ""),
            str(item.get("resolution") or ""),
            float(item.get("matchConfidence") or 0.0),
            str(item.get("posterPath") or ""),
            str(item.get("backdropPath") or ""),
            str(item.get("source") or ""),
            isoformat_now(),
        )
        for item in items
        if str(item.get("section") or "") == "movies"
    ]

    with sqlite3.connect(db_path) as connection:
        configure_sqlite_connection(connection)
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS movie_metadata (
                path TEXT PRIMARY KEY,
                tmdb_id INTEGER NOT NULL,
                imdb_id TEXT NOT NULL,
                title TEXT NOT NULL,
                original_title TEXT NOT NULL,
                tagline TEXT NOT NULL,
                overview TEXT NOT NULL,
                year TEXT NOT NULL,
                release_date TEXT NOT NULL,
                runtime_minutes REAL NOT NULL,
                status TEXT NOT NULL,
                original_language TEXT NOT NULL,
                genres TEXT NOT NULL,
                keywords TEXT NOT NULL,
                production_companies TEXT NOT NULL,
                production_countries TEXT NOT NULL,
                spoken_languages TEXT NOT NULL,
                cast TEXT NOT NULL,
                director TEXT NOT NULL,
                videos TEXT NOT NULL,
                budget INTEGER NOT NULL,
                revenue INTEGER NOT NULL,
                popularity REAL NOT NULL,
                vote_average REAL NOT NULL,
                vote_count INTEGER NOT NULL,
                certification TEXT NOT NULL,
                resolution TEXT NOT NULL,
                confidence REAL NOT NULL,
                poster_path TEXT NOT NULL,
                backdrop_path TEXT NOT NULL,
                source TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_movie_metadata_tmdb_id ON movie_metadata(tmdb_id)")
        connection.execute("DELETE FROM movie_metadata")
        if rows:
            connection.executemany(
                """
                INSERT INTO movie_metadata (
                    path, tmdb_id, imdb_id, title, original_title, tagline, overview,
                    year, release_date, runtime_minutes, status, original_language, genres,
                    keywords, production_companies, production_countries, spoken_languages,
                    cast, director, videos, budget, revenue, popularity, vote_average,
                    vote_count, certification, resolution, confidence, poster_path,
                    backdrop_path, source, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        connection.commit()

    return db_path


def write_show_metadata_database(metadata_root: Path, shows: list[dict[str, object]]) -> Path:
    db_path = metadata_root / "library.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    rows = [
        (
            str(show.get("slug") or ""),
            int(show.get("tmdbId") or 0),
            str(show.get("title") or ""),
            str(show.get("originalTitle") or ""),
            str(show.get("overview") or ""),
            str(show.get("year") or ""),
            str(show.get("firstAirDate") or ""),
            str(show.get("status") or ""),
            str(show.get("originalLanguage") or ""),
            str(show.get("genres") or ""),
            str(show.get("keywords") or ""),
            str(show.get("creators") or ""),
            str(show.get("cast") or ""),
            str(show.get("networks") or ""),
            str(show.get("videos") or ""),
            int(show.get("seasonCount") or 0),
            int(show.get("episodeCount") or 0),
            float(show.get("popularity") or 0.0),
            float(show.get("tmdbRating") or 0.0),
            int(show.get("voteCount") or 0),
            str(show.get("contentRating") or ""),
            float(show.get("matchConfidence") or 0.0),
            str(show.get("posterPath") or ""),
            str(show.get("backdropPath") or ""),
            str(show.get("source") or ""),
            isoformat_now(),
        )
        for show in shows
        if str(show.get("slug") or "")
    ]

    with sqlite3.connect(db_path) as connection:
        configure_sqlite_connection(connection)
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS show_metadata (
                slug TEXT PRIMARY KEY,
                tmdb_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                original_title TEXT NOT NULL,
                overview TEXT NOT NULL,
                year TEXT NOT NULL,
                first_air_date TEXT NOT NULL,
                status TEXT NOT NULL,
                original_language TEXT NOT NULL,
                genres TEXT NOT NULL,
                keywords TEXT NOT NULL,
                creators TEXT NOT NULL,
                cast TEXT NOT NULL,
                networks TEXT NOT NULL,
                videos TEXT NOT NULL,
                season_count INTEGER NOT NULL,
                episode_count INTEGER NOT NULL,
                popularity REAL NOT NULL,
                vote_average REAL NOT NULL,
                vote_count INTEGER NOT NULL,
                certification TEXT NOT NULL,
                confidence REAL NOT NULL,
                poster_path TEXT NOT NULL,
                backdrop_path TEXT NOT NULL,
                source TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_show_metadata_tmdb_id ON show_metadata(tmdb_id)")
        connection.execute("DELETE FROM show_metadata")
        if rows:
            connection.executemany(
                """
                INSERT INTO show_metadata (
                    slug, tmdb_id, title, original_title, overview, year, first_air_date,
                    status, original_language, genres, keywords, creators, cast,
                    networks, videos, season_count, episode_count, popularity,
                    vote_average, vote_count, certification, confidence, poster_path,
                    backdrop_path, source, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        connection.commit()

    return db_path


def isoformat_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def build_library(storage_root: Path, media_root: Path, verbose: bool) -> dict[str, object]:
    config = merge_runtime_config_values(
        default_runtime_config(),
        merge_runtime_config_values(
            read_runtime_config(runtime_config_path(storage_root)),
            read_runtime_config(runtime_user_config_path(storage_root)),
        ),
    )
    client = TmdbClient(config)
    metadata_root = metadata_root_path(media_root)
    metadata_root.mkdir(parents=True, exist_ok=True)
    (metadata_root / "posters").mkdir(parents=True, exist_ok=True)
    (metadata_root / "backdrops").mkdir(parents=True, exist_ok=True)

    items: list[dict[str, object]] = []
    unmatched: list[dict[str, object]] = []

    for file_path in sorted(media_root.rglob("*")):
        if not file_path.is_file():
            continue
        hidden_parts = {part.casefold() for part in file_path.parts}
        if DEFAULT_METADATA_DIRECTORY_NAME.casefold() in hidden_parts or LEGACY_METADATA_DIRECTORY_NAME.casefold() in hidden_parts:
            continue
        media_type = get_media_type(file_path)
        if not media_type:
            continue
        item = build_base_item(file_path, media_root)
        if verbose:
            log(f"Indexed {item['path']} ({item['section']})")
        if client.enabled and str(item.get("section") or "") == "movies":
            try:
                enrich_movie_item(item, file_path, client, metadata_root, unmatched)
            except requests.RequestException as error:
                log(f"TMDb lookup failed for {item['path']}: {error}")
        items.append(item)

    items.sort(key=lambda entry: str(entry.get("path") or "").lower())
    shows = build_show_records(items)
    if client.enabled:
        for show in shows:
            try:
                enrich_show_record(show, client, metadata_root, unmatched)
            except requests.RequestException as error:
                log(f"TMDb lookup failed for show {show.get('title')}: {error}")
        apply_show_metadata_to_items(items, shows)
    metadata_db_path = write_movie_metadata_database(metadata_root, items)
    write_show_metadata_database(metadata_root, shows)
    library = {
        "version": 1,
        "generatedAt": isoformat_now(),
        "generator": GENERATOR_NAME,
        "matcherVersion": MATCHER_VERSION,
        "shows": shows,
        "items": items,
    }
    atomic_write_text(metadata_root / "library.json", json.dumps(library, indent=2, ensure_ascii=False) + "\n")
    atomic_write_text(metadata_root / "unmatched.json", json.dumps(unmatched, indent=2, ensure_ascii=False) + "\n")
    return {
        "library": library,
        "unmatched": unmatched,
        "tmdbEnabled": client.enabled,
        "metadataDbPath": metadata_db_path,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build Backcountry Broadcast metadata and library.json.")
    parser.add_argument(
        "--storage-root",
        default="",
        help="Path to the runtime storage root containing backcountry-broadcast.config.json.",
    )
    parser.add_argument("--media-root", default="", help="Path to the real media root")
    parser.add_argument("--verbose", action="store_true", help="Print each indexed path")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    default_storage_root = DEFAULT_STORAGE_ROOT
    if not args.storage_root and not default_storage_root.exists() and LEGACY_STORAGE_ROOT.exists():
        default_storage_root = LEGACY_STORAGE_ROOT
    storage_root = Path(args.storage_root).expanduser() if args.storage_root else default_storage_root
    media_root = Path(args.media_root).expanduser() if args.media_root else storage_root / "media"
    storage_root = storage_root.resolve(strict=False)
    media_root = media_root.resolve(strict=False)

    if not media_root.exists():
        log(f"Media root does not exist yet: {media_root}")
        return 1

    result = build_library(storage_root, media_root, bool(args.verbose))
    library = result["library"]
    unmatched = result["unmatched"]
    log(f"Metadata written to {metadata_root_path(media_root) / 'library.json'}")
    log(f"Movie and show metadata written to {result['metadataDbPath']}")
    log(f"Indexed {len(library['items'])} item(s) and {len(library['shows'])} show record(s).")
    if unmatched:
        log(f"{len(unmatched)} movie item(s) need review. See {metadata_root_path(media_root) / 'unmatched.json'}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
