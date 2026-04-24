import importlib
import sys
from pathlib import Path


def load_main(tmp_path, monkeypatch):
    storage_root = tmp_path / "storage"
    media_root = tmp_path / "media"
    monkeypatch.setenv("NOMADSCREEN_STORAGE_ROOT", str(storage_root))
    monkeypatch.setenv("NOMADSCREEN_MEDIA_ROOT", str(media_root))
    monkeypatch.setenv("NOMADSCREEN_PORT", "8080")
    sys.modules.pop("main", None)
    return importlib.import_module("main")


def test_resolve_virtual_path_stays_under_media_root(tmp_path, monkeypatch) -> None:
    main = load_main(tmp_path, monkeypatch)
    media_root = Path(main.state.settings["media_directory"]).resolve(strict=False)

    assert main.state.resolve_virtual_path("/media/movies/demo.mp4") == media_root / "movies" / "demo.mp4"
    assert main.state.resolve_virtual_path("/media/../../etc/passwd") is None
    assert main.state.resolve_virtual_path("/not-media/demo.mp4") is None


def test_runtime_config_merges_nested_values(tmp_path, monkeypatch) -> None:
    main = load_main(tmp_path, monkeypatch)
    merged = main.merge_runtime_config_values(
        {"display": {"enabled": False, "model": "waveshare-1.69"}, "deviceName": "Base"},
        {"display": {"enabled": True}},
    )
    assert merged == {
        "display": {"enabled": True, "model": "waveshare-1.69"},
        "deviceName": "Base",
    }
