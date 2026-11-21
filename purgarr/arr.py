"""Minimal Radarr/Sonarr v3 API client plus library search."""

import asyncio
import difflib
import logging
import re
import time
from dataclasses import dataclass

import aiohttp

log = logging.getLogger(__name__)

CACHE_SECONDS = 300


class ArrError(Exception):
    pass


class NotFound(ArrError):
    pass


@dataclass(frozen=True)
class Service:
    key: str
    name: str
    noun: str
    endpoint: str
    exclusion_param: str


RADARR = Service("radarr", "Radarr", "movie", "movie", "addImportExclusion")
SONARR = Service("sonarr", "Sonarr", "show", "series", "addImportListExclusion")
SERVICES = {s.key: s for s in (RADARR, SONARR)}


@dataclass(frozen=True)
class MediaItem:
    service: str
    arr_id: int
    external_id: int | None
    title: str
    year: int | None
    poster_url: str | None
    size_bytes: int
    detail: str
    link: str | None
    overview: str

    @property
    def label(self) -> str:
        return f"{self.title} ({self.year})" if self.year else self.title


def _poster(raw: dict) -> str | None:
    for image in raw.get("images") or []:
        if image.get("coverType") == "poster" and image.get("remoteUrl"):
            return image["remoteUrl"]
    return None


def to_item(service: Service, raw: dict) -> MediaItem:
    stats = raw.get("statistics") or {}
    size = stats.get("sizeOnDisk", raw.get("sizeOnDisk")) or 0

    if service is RADARR:
        external_id = raw.get("tmdbId")
        downloaded = bool(raw.get("hasFile")) or size > 0
        detail = "Downloaded" if downloaded else "Not downloaded"
        link = f"https://www.themoviedb.org/movie/{external_id}" if external_id else None
    else:
        external_id = raw.get("tvdbId")
        seasons = stats.get("seasonCount", 0)
        files = stats.get("episodeFileCount", 0)
        detail = f"{seasons} season{'s' if seasons != 1 else ''}, {files} episode file{'s' if files != 1 else ''}"
        link = f"https://www.thetvdb.com/dereferrer/series/{external_id}" if external_id else None

    if raw.get("imdbId"):
        link = f"https://www.imdb.com/title/{raw['imdbId']}/"

    return MediaItem(
        service=service.key,
        arr_id=raw["id"],
        external_id=external_id,
        title=raw.get("title") or "Unknown title",
        year=raw.get("year") or None,
        poster_url=_poster(raw),
        size_bytes=int(size),
        detail=detail,
        link=link,
        overview=raw.get("overview") or "",
    )


class ArrClient:
    def __init__(self, service: Service, base_url: str, api_key: str, session: aiohttp.ClientSession):
        self.service = service
        self._base_url = base_url
        self._api_key = api_key
        self._session = session
        self._cache: list[MediaItem] | None = None
        self._cache_time = 0.0
        self._lock = asyncio.Lock()

    async def _request(self, method: str, path: str, **params: str):
        url = f"{self._base_url}/api/v3/{path}"
        try:
            async with self._session.request(
                method,
                url,
                params=params or None,
                headers={"X-Api-Key": self._api_key},
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                if resp.status == 404:
                    raise NotFound(f"{self.service.name}: {path} not found")
                if resp.status == 401:
                    raise ArrError(f"{self.service.name} rejected the API key")
                if resp.status >= 400:
                    body = (await resp.text())[:300]
                    raise ArrError(f"{self.service.name} returned HTTP {resp.status}: {body}")
                if resp.content_type == "application/json":
                    return await resp.json()
                if method == "GET":
                    # A wrong URL base gets the web UI's HTML page with status 200.
                    raise ArrError(f"{self.service.name} didn't return API data, check {self.service.name.upper()}_URL")
                return None
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise ArrError(f"Could not reach {self.service.name} ({exc.__class__.__name__})") from exc

    async def library(self) -> list[MediaItem]:
        async with self._lock:
            if self._cache is None or time.monotonic() - self._cache_time > CACHE_SECONDS:
                raw = await self._request("GET", self.service.endpoint)
                self._cache = [to_item(self.service, entry) for entry in raw or []]
                self._cache_time = time.monotonic()
            return self._cache

    async def get(self, arr_id: int) -> MediaItem | None:
        try:
            raw = await self._request("GET", f"{self.service.endpoint}/{arr_id}")
        except NotFound:
            # Only trust the 404 if the API itself answers; a wrong URL or proxy 404s too.
            try:
                await self._request("GET", "system/status")
            except NotFound:
                raise ArrError(f"{self.service.name} API not found, check {self.service.name.upper()}_URL") from None
            return None
        return to_item(self.service, raw)

    async def delete(self, arr_id: int, add_exclusion: bool) -> None:
        """Deletes the movie/series and its files. Raises NotFound if it is already gone."""
        await self._request(
            "DELETE",
            f"{self.service.endpoint}/{arr_id}",
            deleteFiles="true",
            **{self.service.exclusion_param: "true" if add_exclusion else "false"},
        )
        self._cache = None

    async def recycle_bin(self) -> str:
        """The configured recycle bin path, or '' when deletes are permanent."""
        raw = await self._request("GET", "config/mediamanagement")
        return (raw or {}).get("recycleBin") or ""


_NON_WORD = re.compile(r"[^a-z0-9]+")
_TRAILING_YEAR = re.compile(r"\s*\(?((?:19|20)\d{2})\)?\s*$")


def normalize(text: str) -> str:
    return _NON_WORD.sub(" ", text.lower()).strip()


def _match(items: list[MediaItem], query: str, year: int | None, limit: int) -> list[MediaItem]:
    q = normalize(query)
    if not q:
        return []
    pool = [item for item in items if year is None or item.year == year]

    ranked = []
    for item in pool:
        title = normalize(item.title)
        if title == q:
            rank = 0
        elif title.startswith(q):
            rank = 1
        elif q in title:
            rank = 2
        else:
            continue
        ranked.append((rank, title, item.year or 0, item))

    if not ranked:
        # Nothing contains the query, so fall back to fuzzy matching for typos.
        by_title: dict[str, list[MediaItem]] = {}
        for item in pool:
            by_title.setdefault(normalize(item.title), []).append(item)
        close = difflib.get_close_matches(q, list(by_title), n=limit, cutoff=0.6)
        return [item for title in close for item in by_title[title]][:limit]

    ranked.sort(key=lambda entry: entry[:3])
    return [entry[3] for entry in ranked[:limit]]


def search(items: list[MediaItem], query: str, limit: int = 25) -> list[MediaItem]:
    """Finds library items by title. A trailing year ("Dune 2021" or "Dune (2021)") narrows the results."""
    year_match = _TRAILING_YEAR.search(query)
    if year_match and year_match.start() > 0:
        results = _match(items, query[: year_match.start()], int(year_match.group(1)), limit)
        if results:
            return results
    return _match(items, query, None, limit)
