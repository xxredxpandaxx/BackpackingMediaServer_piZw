from __future__ import annotations

import hashlib
import os
import re
import tempfile
from pathlib import Path
from typing import Any

try:
    from mutagen import File as MutagenFile
    from mutagen.mp4 import MP4, MP4Cover
except Exception:  # pragma: no cover - optional dependency during local editing
    MutagenFile = None
    MP4 = None
    MP4Cover = None


LANGUAGE_LABELS = {
    "en": "English",
    "eng": "English",
    "en-us": "English",
    "en-gb": "English",
    "es": "Spanish",
    "spa": "Spanish",
    "fr": "French",
    "fra": "French",
    "fre": "French",
    "de": "German",
    "deu": "German",
    "ger": "German",
    "it": "Italian",
    "ita": "Italian",
    "pt": "Portuguese",
    "por": "Portuguese",
    "ja": "Japanese",
    "jpn": "Japanese",
    "ko": "Korean",
    "kor": "Korean",
    "zh": "Chinese",
    "zho": "Chinese",
    "chi": "Chinese",
}


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


def atomic_write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
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


def decode_text_bytes(value: bytes) -> str:
    for encoding in ("utf-8", "utf-16", "utf-16-le", "utf-16-be", "latin-1"):
        try:
            return value.decode(encoding).replace("\x00", " ").strip()
        except UnicodeDecodeError:
            continue
    return ""


def normalize_spaces(value: object) -> str:
    return " ".join(str(value or "").replace("\x00", " ").split()).strip()


def flatten_text_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, bytes):
        decoded = normalize_spaces(decode_text_bytes(value))
        return [decoded] if decoded else []
    if isinstance(value, str):
        normalized = normalize_spaces(value)
        return [normalized] if normalized else []
    if isinstance(value, (list, tuple, set)):
        values: list[str] = []
        for item in value:
            values.extend(flatten_text_values(item))
        return values
    if isinstance(value, (int, float)):
        return [normalize_spaces(value)]
    text = normalize_spaces(value)
    return [text] if text else []


def unique_text(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for raw in values:
        normalized = normalize_spaces(raw)
        lowered = normalized.lower()
        if not normalized or lowered in seen:
            continue
        seen.add(lowered)
        output.append(normalized)
    return output


def first_text(values: list[str]) -> str:
    for value in values:
        normalized = normalize_spaces(value)
        if normalized:
            return normalized
    return ""


def join_text(values: list[str], separator: str = ", ") -> str:
    return separator.join(unique_text(values))


def split_tag_values(values: list[str]) -> list[str]:
    parts: list[str] = []
    for value in values:
        for piece in re.split(r"[|;/]+|,\s*", value):
            normalized = normalize_spaces(piece)
            if normalized:
                parts.append(normalized)
    return unique_text(parts)


def split_people_values(values: list[str]) -> list[str]:
    parts: list[str] = []
    for value in values:
        for piece in re.split(r"[|;/]+|\n+|\s+\band\b\s+", value, flags=re.IGNORECASE):
            normalized = normalize_spaces(piece)
            if normalized:
                parts.append(normalized)
    return unique_text(parts)


def normalize_freeform_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


def mp4_freeform_values(tags: Any) -> dict[str, list[str]]:
    output: dict[str, list[str]] = {}
    if not hasattr(tags, "items"):
        return output
    for key, value in tags.items():
        if not str(key).startswith("----:"):
            continue
        name = normalize_freeform_name(str(key).split(":")[-1])
        if not name:
            continue
        values = flatten_text_values(value)
        if values:
            output.setdefault(name, []).extend(values)
    return {key: unique_text(values) for key, values in output.items()}


def text_from_sources(tags: Any, freeform: dict[str, list[str]], standard_keys: tuple[str, ...], freeform_keys: tuple[str, ...]) -> list[str]:
    values: list[str] = []
    for key in standard_keys:
        values.extend(flatten_text_values(tags.get(key) if hasattr(tags, "get") else None))
    for key in freeform_keys:
        values.extend(freeform.get(normalize_freeform_name(key), []))
    return unique_text(values)


def parse_year(value: str) -> str:
    match = re.search(r"(19\d{2}|20\d{2})", str(value or ""))
    return match.group(1) if match else ""


def normalize_language(value: str) -> str:
    normalized = normalize_spaces(value)
    if not normalized:
        return ""
    lowered = normalized.lower()
    if lowered in LANGUAGE_LABELS:
        return LANGUAGE_LABELS[lowered]
    if "-" in lowered:
        primary = lowered.split("-", 1)[0]
        if primary in LANGUAGE_LABELS:
            return LANGUAGE_LABELS[primary]
    return normalized


def normalize_series_index(value: object) -> str:
    def normalize_numeric_text(raw_value: object) -> str:
        try:
            numeric = float(str(raw_value or "").strip())
        except (TypeError, ValueError):
            return ""
        if numeric <= 0:
            return ""
        return str(int(numeric)) if numeric.is_integer() else str(numeric).rstrip("0").rstrip(".")

    if isinstance(value, (int, float)) and float(value) > 0:
        numeric = float(value)
        return str(int(numeric)) if numeric.is_integer() else str(numeric).rstrip("0").rstrip(".")
    for candidate in flatten_text_values(value):
        direct_match = re.fullmatch(r"\d+(?:\.\d+)?", candidate)
        if direct_match:
            return normalize_numeric_text(direct_match.group(0))
        match = re.search(r"(\d+(?:\.\d+)?)", candidate)
        if match:
            return normalize_numeric_text(match.group(1))
    return ""


def embedded_series_index(value: object) -> str:
    for candidate in flatten_text_values(value):
        match = re.search(r"#\s*(\d+(?:\.\d+)?)\s*$", candidate)
        if match:
            return normalize_series_index(match.group(1))
    return ""


def normalize_for_compare(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def detect_cover_extension(image_data: bytes, cover: object) -> str:
    if image_data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if image_data.startswith(b"GIF87a") or image_data.startswith(b"GIF89a"):
        return ".gif"
    if image_data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if MP4Cover is not None and hasattr(cover, "imageformat"):
        if cover.imageformat == getattr(MP4Cover, "FORMAT_PNG", object()):
            return ".png"
        if cover.imageformat == getattr(MP4Cover, "FORMAT_JPEG", object()):
            return ".jpg"
    return ".jpg"


def save_embedded_cover(metadata_root: Path, cover: object) -> str:
    try:
        image_data = bytes(cover)
    except Exception:
        return ""
    if not image_data:
        return ""
    extension = detect_cover_extension(image_data, cover)
    digest = hashlib.sha1(image_data).hexdigest()[:20]
    file_name = f"embedded-audiobook-{digest}{extension}"
    actual_path = metadata_root / "posters" / file_name
    if not actual_path.exists():
        atomic_write_bytes(actual_path, image_data)
    return normalize_virtual_path(f"/media/.backcountry-broadcast/posters/{file_name}")


def extract_audiobook_embedded_metadata(file_path: Path, metadata_root: Path) -> dict[str, object]:
    if MutagenFile is None:
        return {}
    try:
        audio = MutagenFile(str(file_path), easy=False)
    except Exception:
        return {}
    if audio is None:
        return {}

    metadata: dict[str, object] = {}
    runtime_minutes = round(float(getattr(getattr(audio, "info", None), "length", 0.0) or 0.0) / 60.0, 1)
    if runtime_minutes > 0:
        metadata["runtimeMinutes"] = runtime_minutes

    if MP4 is None or not isinstance(audio, MP4):
        if metadata:
            metadata["metadataSource"] = "embedded"
            metadata["source"] = "embedded"
        return metadata

    tags = audio.tags or {}
    freeform = mp4_freeform_values(tags)

    title = first_text(text_from_sources(tags, freeform, ("\xa9nam",), ("title", "booktitle")))
    author = first_text(
        text_from_sources(
            tags,
            freeform,
            ("\xa9ART", "aART", "\xa9wrt"),
            ("author", "authors", "writer", "writers", "albumartist"),
        )
    )
    album = first_text(text_from_sources(tags, freeform, ("\xa9alb",), ("album", "collection")))
    narrators = join_text(
        split_people_values(
            text_from_sources(
                tags,
                freeform,
                (),
                ("narrator", "narrators", "reader", "readby", "narratedby", "performedby"),
            )
        )
    )
    publisher = first_text(text_from_sources(tags, freeform, (), ("publisher", "publishingcompany", "organization")))
    language = normalize_language(
        first_text(text_from_sources(tags, freeform, (), ("language", "languages", "lang")))
    )
    release_date = first_text(
        text_from_sources(
            tags,
            freeform,
            ("\xa9day",),
            ("releasedate", "publishdate", "publisheddate", "year", "date"),
        )
    )
    year = parse_year(release_date)
    overview = first_text(
        text_from_sources(
            tags,
            freeform,
            ("ldes", "desc"),
            ("description", "summary", "synopsis", "comment"),
        )
    )
    genres = join_text(
        split_tag_values(text_from_sources(tags, freeform, ("\xa9gen", "gnre"), ("genre", "genres")))
    )
    grouping = text_from_sources(tags, freeform, ("\xa9grp",), ("grouping",))
    tags_text = join_text(
        split_tag_values(
            grouping
            + text_from_sources(tags, freeform, (), ("tag", "tags", "keyword", "keywords"))
        )
    )

    series_name = first_text(
        text_from_sources(
            tags,
            freeform,
            (),
            (
                "series",
                "seriesname",
                "series title",
                "audiobookseries",
                "bookseries",
                "seriesgroup",
            ),
        )
    )
    if not series_name:
        series_name = first_text(grouping)
    if not series_name and album and normalize_for_compare(album) != normalize_for_compare(title):
        series_name = album
    embedded_index = embedded_series_index(series_name) or embedded_series_index(album)

    series_index = normalize_series_index(
        text_from_sources(
            tags,
            freeform,
            (),
            (
                "seriespart",
                "seriesindex",
                "seriesnumber",
                "seriessequence",
                "seriessequencenumber",
                "booknumber",
                "part",
                "partnumber",
                "sequence",
                "sequencenumber",
                "volume",
                "volumenumber",
            ),
        )
    )
    if embedded_index:
        series_index = embedded_index
    elif not series_index:
        track_numbers = tags.get("trkn") if hasattr(tags, "get") else None
        if isinstance(track_numbers, list) and track_numbers:
            first_track = track_numbers[0]
            if isinstance(first_track, tuple) and first_track:
                series_index = normalize_series_index(first_track[0])

    cover_path = ""
    covers = tags.get("covr") if hasattr(tags, "get") else None
    if isinstance(covers, list) and covers:
        cover_path = save_embedded_cover(metadata_root, covers[0])

    if title:
        metadata["title"] = title
        metadata["sortTitle"] = title
    if author:
        metadata["artist"] = author
    if album:
        metadata["album"] = album
    if narrators:
        metadata["narrators"] = narrators
    if publisher:
        metadata["publisher"] = publisher
    if language:
        metadata["language"] = language
    if release_date:
        metadata["releaseDate"] = release_date
    if year:
        metadata["year"] = year
    if overview:
        metadata["overview"] = overview
    if genres:
        metadata["genres"] = genres
    if tags_text:
        metadata["tags"] = tags_text
    if series_name:
        metadata["seriesName"] = series_name
    if series_index:
        metadata["seriesIndex"] = series_index
    if cover_path:
        metadata["posterPath"] = cover_path

    if metadata:
        metadata["metadataSource"] = "embedded"
        metadata["source"] = "embedded"
    return metadata
