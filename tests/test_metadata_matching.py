import importlib.util
from pathlib import Path


def load_metadata_builder():
    module_path = Path(__file__).resolve().parents[1] / "tools" / "backcountry_broadcast_refresh_metadata.py"
    spec = importlib.util.spec_from_file_location("backcountry_broadcast_refresh_metadata", module_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_movie_score_uses_alternative_titles() -> None:
    metadata = load_metadata_builder()

    score = metadata.compute_movie_score(
        "harry potter and the sorcerer's stone (2001)",
        2001,
        0,
        {
            "title": "Harry Potter and the Philosopher's Stone",
            "original_title": "Harry Potter and the Philosopher's Stone",
            "release_date": "2001-11-16",
            "alternative_titles": {
                "titles": [
                    {"iso_3166_1": "US", "title": "Harry Potter and the Sorcerer's Stone"},
                ],
            },
        },
    )

    assert score >= 0.8


def test_movie_score_accepts_filename_franchise_prefixes() -> None:
    metadata = load_metadata_builder()

    score = metadata.compute_movie_score(
        "indiana jones and the raiders of the lost ark (1981)",
        1981,
        0,
        {
            "title": "Raiders of the Lost Ark",
            "original_title": "Raiders of the Lost Ark",
            "release_date": "1981-06-12",
        },
    )

    assert score >= 0.8


def test_movie_score_accepts_episode_subtitle_filenames() -> None:
    metadata = load_metadata_builder()

    score = metadata.compute_movie_score(
        "star wars episode v - the empire strikes back (1980)",
        1980,
        0,
        {
            "title": "The Empire Strikes Back",
            "original_title": "The Empire Strikes Back",
            "release_date": "1980-05-20",
        },
    )

    assert score >= 0.8


def test_tmdb_search_queries_include_episode_subtitles() -> None:
    metadata = load_metadata_builder()

    assert metadata.tmdb_search_queries("star wars episode iv - a new hope (1977)", 1977) == [
        "star wars episode iv a new hope",
        "star wars a new hope",
        "star wars episode iv",
        "a new hope",
    ]
