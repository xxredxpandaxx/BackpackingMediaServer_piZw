from pathlib import Path

from media_paths import classify_media_type, normalize_virtual_path, path_is_relative_to


def test_normalize_virtual_path_collapses_relative_segments() -> None:
    assert normalize_virtual_path("media/movies/../tv//Show/E01.mkv") == "/media/tv/Show/E01.mkv"
    assert normalize_virtual_path("/media/./documents/maps/") == "/media/documents/maps"
    assert normalize_virtual_path("") == ""


def test_classify_media_type() -> None:
    assert classify_media_type("/media/movies/example.MP4") == "video"
    assert classify_media_type("/media/audiobooks/book.m4b") == "audio"
    assert classify_media_type("/media/documents/map.gpx") == "document"
    assert classify_media_type("/media/documents/photo.webp") == "image"
    assert classify_media_type("/media/unknown/archive.zip") == ""


def test_path_is_relative_to() -> None:
    root = Path("/srv/backcountry/media")
    assert path_is_relative_to(root / "movies" / "film.mp4", root)
    assert not path_is_relative_to(Path("/srv/backcountry-other/media/film.mp4"), root)
