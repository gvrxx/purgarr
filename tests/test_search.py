from purgarr.arr import RADARR, SONARR, search, to_item


def movie(id, title, year, **extra):
    return to_item(RADARR, {"id": id, "title": title, "year": year, "tmdbId": 1000 + id, **extra})


LIBRARY = [
    movie(1, "The Matrix", 1999),
    movie(2, "The Matrix Reloaded", 2003),
    movie(3, "The Matrix Resurrections", 2021),
    movie(4, "Dune", 1984),
    movie(5, "Dune", 2021),
    movie(6, "Blade Runner 2049", 2017),
    movie(7, "2012", 2009),
    movie(8, "Spider-Man: No Way Home", 2021),
]


def ids(results):
    return [item.arr_id for item in results]


def test_exact_title_ranks_first():
    assert ids(search(LIBRARY, "the matrix")) == [1, 2, 3]


def test_substring_match_ignores_case_and_punctuation():
    assert ids(search(LIBRARY, "SPIDER-MAN: no way")) == [8]
    assert ids(search(LIBRARY, "spiderman no way home")) == [8]  # fuzzy fallback


def test_trailing_year_narrows_results():
    assert ids(search(LIBRARY, "Dune 2021")) == [5]
    assert ids(search(LIBRARY, "Dune (1984)")) == [4]


def test_year_that_is_part_of_the_title_still_matches():
    assert ids(search(LIBRARY, "Blade Runner 2049")) == [6]
    assert ids(search(LIBRARY, "2012")) == [7]


def test_typo_falls_back_to_fuzzy_match():
    assert ids(search(LIBRARY, "the matrx"))[0] == 1


def test_no_match_and_empty_query():
    assert search(LIBRARY, "Totally Unknown Film") == []
    assert search(LIBRARY, "   ") == []


def test_radarr_item_fields():
    item = to_item(RADARR, {
        "id": 9, "title": "Heat", "year": 1995, "tmdbId": 949, "imdbId": "tt0113277", "hasFile": True,
        "statistics": {"sizeOnDisk": 5_000_000_000},
        "images": [
            {"coverType": "fanart", "remoteUrl": "https://image.tmdb.org/fanart.jpg"},
            {"coverType": "poster", "url": "/MediaCover/9/poster.jpg", "remoteUrl": "https://image.tmdb.org/poster.jpg"},
        ],
    })
    assert item.label == "Heat (1995)"
    assert item.external_id == 949
    assert item.size_bytes == 5_000_000_000
    assert item.poster_url == "https://image.tmdb.org/poster.jpg"
    assert item.link == "https://www.imdb.com/title/tt0113277/"
    assert item.detail == "Downloaded"


def test_radarr_legacy_size_field_and_missing_poster():
    item = to_item(RADARR, {"id": 1, "title": "Old", "year": 2000, "sizeOnDisk": 42, "images": []})
    assert item.size_bytes == 42
    assert item.poster_url is None
    assert item.detail == "Downloaded"


def test_sonarr_item_fields():
    item = to_item(SONARR, {
        "id": 3, "title": "Severance", "year": 2022, "tvdbId": 371980,
        "statistics": {"sizeOnDisk": 10, "seasonCount": 2, "episodeFileCount": 19},
    })
    assert item.external_id == 371980
    assert item.detail == "2 seasons, 19 episode files"
    assert item.link == "https://www.thetvdb.com/dereferrer/series/371980"
