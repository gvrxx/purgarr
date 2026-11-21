import aiohttp
import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestServer

from purgarr.arr import RADARR, SONARR, ArrClient, ArrError, NotFound

MATRIX = {"id": 1, "title": "The Matrix", "year": 1999, "tmdbId": 603,
          "images": [{"coverType": "poster", "remoteUrl": "https://image.tmdb.org/matrix.jpg"}]}


@pytest_asyncio.fixture
async def fake_arr():
    """A tiny stand-in for Radarr/Sonarr mounted under a URL base, like http://host:7878/radarr."""
    calls = []

    def authorized(request):
        calls.append((request.method, request.path, dict(request.query)))
        return request.headers.get("X-Api-Key") == "secret"

    async def list_movies(request):
        if not authorized(request):
            return web.Response(status=401)
        return web.json_response([MATRIX])

    async def get_movie(request):
        authorized(request)
        if request.match_info["id"] != "1":
            return web.json_response({"message": "NotFound"}, status=404)
        return web.json_response(MATRIX)

    async def delete_item(request):
        authorized(request)
        if request.match_info["id"] == "500":
            return web.Response(status=500, text="database is locked")
        return web.Response(status=200)

    async def media_management(request):
        authorized(request)
        return web.json_response({"recycleBin": "/mnt/user/recycle", "recycleBinCleanupDays": 7})

    app = web.Application()
    app.router.add_get("/radarr/api/v3/movie", list_movies)
    app.router.add_get("/radarr/api/v3/movie/{id}", get_movie)
    app.router.add_delete("/radarr/api/v3/movie/{id}", delete_item)
    app.router.add_delete("/sonarr/api/v3/series/{id}", delete_item)
    app.router.add_get("/radarr/api/v3/config/mediamanagement", media_management)
    async def system_status(request):
        return web.json_response({"appName": "Radarr"})

    async def web_ui_page(request):
        return web.Response(text="<html></html>", content_type="text/html")

    app.router.add_get("/radarr/api/v3/system/status", system_status)
    app.router.add_get("/webui/{tail:.*}", web_ui_page)

    server = TestServer(app)
    await server.start_server()
    async with aiohttp.ClientSession() as session:
        yield server, session, calls
    await server.close()


def base(server, path):
    return str(server.make_url(path))


@pytest.mark.asyncio
async def test_library_is_parsed_and_cached(fake_arr):
    server, session, calls = fake_arr
    client = ArrClient(RADARR, base(server, "/radarr"), "secret", session)

    items = await client.library()
    await client.library()
    assert [item.label for item in items] == ["The Matrix (1999)"]
    assert items[0].poster_url == "https://image.tmdb.org/matrix.jpg"
    assert [c for c in calls if c[0] == "GET"] == [("GET", "/radarr/api/v3/movie", {})]


@pytest.mark.asyncio
async def test_get_returns_none_when_missing(fake_arr):
    server, session, _ = fake_arr
    client = ArrClient(RADARR, base(server, "/radarr"), "secret", session)
    assert (await client.get(1)).external_id == 603
    assert await client.get(2) is None


@pytest.mark.asyncio
async def test_radarr_delete_parameters_and_cache_reset(fake_arr):
    server, session, calls = fake_arr
    client = ArrClient(RADARR, base(server, "/radarr"), "secret", session)
    await client.library()

    await client.delete(1, add_exclusion=False)
    await client.delete(1, add_exclusion=True)
    deletes = [c for c in calls if c[0] == "DELETE"]
    assert deletes == [
        ("DELETE", "/radarr/api/v3/movie/1", {"deleteFiles": "true", "addImportExclusion": "false"}),
        ("DELETE", "/radarr/api/v3/movie/1", {"deleteFiles": "true", "addImportExclusion": "true"}),
    ]

    await client.library()
    assert len([c for c in calls if c[:2] == ("GET", "/radarr/api/v3/movie")]) == 2


@pytest.mark.asyncio
async def test_sonarr_uses_its_own_exclusion_parameter(fake_arr):
    server, session, calls = fake_arr
    client = ArrClient(SONARR, base(server, "/sonarr"), "secret", session)
    await client.delete(7, add_exclusion=True)
    assert calls[-1] == ("DELETE", "/sonarr/api/v3/series/7",
                         {"deleteFiles": "true", "addImportListExclusion": "true"})


@pytest.mark.asyncio
async def test_errors_are_readable(fake_arr):
    server, session, _ = fake_arr
    bad_key = ArrClient(RADARR, base(server, "/radarr"), "wrong", session)
    with pytest.raises(ArrError, match="rejected the API key"):
        await bad_key.library()

    client = ArrClient(RADARR, base(server, "/radarr"), "secret", session)
    with pytest.raises(ArrError, match="HTTP 500: database is locked"):
        await client.delete(500, add_exclusion=False)
    with pytest.raises(NotFound):
        await ArrClient(RADARR, base(server, "/nothing-here"), "secret", session).delete(1, False)


@pytest.mark.asyncio
async def test_recycle_bin(fake_arr):
    server, session, _ = fake_arr
    client = ArrClient(RADARR, base(server, "/radarr"), "secret", session)
    assert await client.recycle_bin() == "/mnt/user/recycle"


@pytest.mark.asyncio
async def test_unreachable_server():
    async with aiohttp.ClientSession() as session:
        client = ArrClient(RADARR, "http://127.0.0.1:9", "secret", session)
        with pytest.raises(ArrError, match="Could not reach Radarr"):
            await client.library()


@pytest.mark.asyncio
async def test_web_page_instead_of_api_is_an_error(fake_arr):
    server, session, _ = fake_arr
    client = ArrClient(RADARR, base(server, "/webui"), "secret", session)
    with pytest.raises(ArrError, match="check RADARR_URL"):
        await client.library()
    with pytest.raises(ArrError, match="check RADARR_URL"):
        await client.recycle_bin()


@pytest.mark.asyncio
async def test_missing_api_is_not_mistaken_for_a_missing_item(fake_arr):
    server, session, _ = fake_arr
    client = ArrClient(RADARR, base(server, "/nothing-here"), "secret", session)
    with pytest.raises(ArrError, match="API not found") as info:
        await client.get(1)
    assert not isinstance(info.value, NotFound)
