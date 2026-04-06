from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import requests
from rapidfuzz import fuzz


GENERATOR_NAME = "Nomad Screen Python Metadata Builder"
TMDB_API_ROOT = "https://api.themoviedb.org/3"
TMDB_IMAGE_ROOT = "https://image.tmdb.org/t/p"

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".webm", ".m4v", ".avi"}
AUDIO_EXTENSIONS = {".mp3", ".m4a", ".m4b", ".aac", ".wav", ".flac", ".ogg"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
DOCUMENT_EXTENSIONS = {".pdf", ".txt", ".md", ".csv", ".gpx", ".kml", ".doc", ".docx"}


def log(message: str) -> None:
    print(f"[nomadscreen-metadata] {message}")


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


def read_runtime_config(config_path: Path) -> dict[str, object]:
    defaults: dict[str, object] = {
        "tmdbApiKey": "",
        "tmdbBearerToken": "",
        "language": "en-US",
        "country": "US",
        "downloadImages": True,
        "overwriteImages": False,
        "minimumMatchScore": 0.55,
    }
    if not config_path.exists():
        return defaults
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return defaults
    merged = dict(defaults)
    merged.update(raw)
    return merged


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
        params: dict[str, object] = {"query": title}
        if year:
            params["year"] = year
        payload = self.request("search/movie", params=params)
        results = payload.get("results")
        if isinstance(results, list) and results:
            return [entry for entry in results if isinstance(entry, dict)]
        if year:
            payload = self.request("search/movie", params={"query": title})
            results = payload.get("results")
            if isinstance(results, list):
                return [entry for entry in results if isinstance(entry, dict)]
        return []

    def movie_details(self, movie_id: int) -> dict[str, object]:
        return self.request(
            f"movie/{movie_id}",
            params={"append_to_response": "release_dates"},
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
        item["seasonLabel"] = season_label
        item["seasonNumber"] = parse_season_number(season_label)
        item["episodeNumber"] = parse_episode_number(file_path.name)
    elif section in {"music", "audiobooks"}:
        if len(parts) >= 4:
            item["artist"] = prettify_name(parts[2])
            item["album"] = prettify_name(parts[3])
        elif len(parts) >= 3:
            item["artist"] = prettify_name(parts[2])
    return item


def compute_movie_score(
    local_title: str,
    local_year: int | None,
    local_runtime: float,
    details: dict[str, object],
) -> float:
    tmdb_title = str(details.get("title") or "")
    tmdb_original_title = str(details.get("original_title") or "")
    title_similarity = max(
        fuzz.token_sort_ratio(local_title, tmdb_title.lower()) / 100.0 if tmdb_title else 0.0,
        fuzz.token_sort_ratio(local_title, tmdb_original_title.lower()) / 100.0 if tmdb_original_title else 0.0,
    )
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

    candidates = client.search_movie(local_title, local_year)[:8]
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
                "query": local_title,
                "candidates": close_candidates,
            }
        )
        return

    release_date = str(best_details.get("release_date") or "")
    item["title"] = str(best_details.get("title") or item["title"])
    item["sortTitle"] = item["title"]
    item["overview"] = str(best_details.get("overview") or "")
    item["tagline"] = str(best_details.get("tagline") or "")
    item["year"] = release_date[:4] if len(release_date) >= 4 else str(item.get("year") or "")
    item["releaseDate"] = release_date
    item["genres"] = ", ".join(
        str(entry.get("name") or "").strip()
        for entry in best_details.get("genres", [])
        if isinstance(entry, dict) and str(entry.get("name") or "").strip()
    )
    item["contentRating"] = extract_certification(best_details, client.country)
    item["source"] = "tmdb"
    item["tmdbRating"] = round(float(best_details.get("vote_average") or 0.0), 1)
    if not item["runtimeMinutes"] and float(best_details.get("runtime") or 0.0) > 0:
        item["runtimeMinutes"] = round(float(best_details.get("runtime") or 0.0), 1)
    item["matchConfidence"] = best_score

    movie_id = int(best_details.get("id") or 0)
    if movie_id > 0:
        poster_path = save_tmdb_image(
            client,
            str(best_details.get("poster_path") or ""),
            metadata_root / "posters",
            "/media/.nomadscreen/posters",
            f"movie-{movie_id}",
            "w500",
        )
        backdrop_path = save_tmdb_image(
            client,
            str(best_details.get("backdrop_path") or ""),
            metadata_root / "backdrops",
            "/media/.nomadscreen/backdrops",
            f"movie-{movie_id}",
            "w780",
        )
        if poster_path:
            item["posterPath"] = poster_path
        if backdrop_path:
            item["backdropPath"] = backdrop_path


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
            },
        )
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
    return sorted(show_map.values(), key=lambda entry: str(entry.get("title") or "").lower())


def build_library(storage_root: Path, media_root: Path, verbose: bool) -> dict[str, object]:
    config = read_runtime_config(storage_root / "nomadscreen.config.json")
    client = TmdbClient(config)
    metadata_root = media_root / ".nomadscreen"
    metadata_root.mkdir(parents=True, exist_ok=True)
    (metadata_root / "posters").mkdir(parents=True, exist_ok=True)
    (metadata_root / "backdrops").mkdir(parents=True, exist_ok=True)

    items: list[dict[str, object]] = []
    unmatched: list[dict[str, object]] = []

    for file_path in sorted(media_root.rglob("*")):
        if not file_path.is_file():
            continue
        if ".nomadscreen" in file_path.parts:
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
    library = {
        "version": 1,
        "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "generator": GENERATOR_NAME,
        "shows": shows,
        "items": items,
    }
    (metadata_root / "library.json").write_text(json.dumps(library, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (metadata_root / "unmatched.json").write_text(json.dumps(unmatched, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {
        "library": library,
        "unmatched": unmatched,
        "tmdbEnabled": client.enabled,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build Nomad Screen metadata and library.json.")
    parser.add_argument("--storage-root", default="", help="Path to the runtime storage root containing nomadscreen.config.json")
    parser.add_argument("--media-root", default="", help="Path to the real media root")
    parser.add_argument("--verbose", action="store_true", help="Print each indexed path")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    script_root = Path(__file__).resolve().parents[1]
    default_storage_root = script_root / "sdcard-template"
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
    log(f"Metadata written to {media_root / '.nomadscreen' / 'library.json'}")
    log(f"Indexed {len(library['items'])} item(s) and {len(library['shows'])} show record(s).")
    if unmatched:
        log(f"{len(unmatched)} movie item(s) need review. See {media_root / '.nomadscreen' / 'unmatched.json'}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
