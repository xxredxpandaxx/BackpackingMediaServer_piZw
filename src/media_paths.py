from __future__ import annotations

from pathlib import Path


DEFAULT_MEDIA_ROOT = "/media"


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


def path_is_relative_to(candidate: Path, root: Path) -> bool:
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False


def classify_media_type(path: str) -> str:
    lowered = str(path or "").lower()
    if lowered.endswith((".mp4", ".mkv", ".mov", ".webm", ".m4v", ".avi")):
        return "video"
    if lowered.endswith((".mp3", ".m4a", ".m4b", ".aac", ".wav", ".flac", ".ogg")):
        return "audio"
    if lowered.endswith((".jpg", ".jpeg", ".png", ".gif", ".webp")):
        return "image"
    if lowered.endswith((".pdf", ".txt", ".md", ".csv", ".gpx", ".kml", ".doc", ".docx")):
        return "document"
    return ""
