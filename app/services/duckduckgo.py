"""Unofficial DuckDuckGo web search using the ddgs package's direct backend."""
from __future__ import annotations

import logging
import re
from importlib.metadata import version
from urllib.parse import urlsplit

from ddgs import DDGS
from ddgs.exceptions import DDGSException, RatelimitException, TimeoutException

from app.core.errors import ServiceError
from app.services.base import Service

log = logging.getLogger(__name__)

MAX_QUERY_LENGTH = 500
MAX_SEARCH_LIMIT = 20
DEFAULT_SEARCH_LIMIT = 5
DEFAULT_REGION = "us-en"
DEFAULT_SAFESEARCH = "moderate"
SEARCH_TIMEOUT_SECONDS = 6
REGION_PATTERN = re.compile(r"[a-z]{2,3}-[a-z]{2,3}\Z")
LANGUAGE_PATTERN = re.compile(r"[a-z]{2,3}\Z")
SAFESEARCH_OPTIONS = frozenset({"on", "moderate", "off"})


def _validate_query(query: str) -> str:
    if (not isinstance(query, str) or not query.strip()
            or len(query) > MAX_QUERY_LENGTH
            or any(ord(char) < 32 or ord(char) == 127 for char in query)):
        raise ServiceError(f"Query must contain 1 to {MAX_QUERY_LENGTH} valid characters", 400)
    return query.strip()


def _validate_region(region: str, language: str | None) -> str:
    if not isinstance(region, str) or not REGION_PATTERN.fullmatch(region):
        raise ServiceError("Region must use a supported country-language code such as us-en", 400)
    if language is not None:
        if not isinstance(language, str) or not LANGUAGE_PATTERN.fullmatch(language):
            raise ServiceError("Language must be a two- or three-letter code", 400)
        region = f"{region.split('-', 1)[0]}-{language}"
    return region


class DuckDuckGoService(Service):
    name = "duckduckgo"
    title = "DuckDuckGo Web Search"
    description = "Unofficial DuckDuckGo text search using the direct DuckDuckGo backend."

    def search(self, query: str, limit: int = DEFAULT_SEARCH_LIMIT,
               region: str = DEFAULT_REGION, language: str | None = None,
               safesearch: str = DEFAULT_SAFESEARCH) -> dict:
        query = _validate_query(query)
        if type(limit) is not int or not 1 <= limit <= MAX_SEARCH_LIMIT:
            raise ServiceError(f"Limit must be between 1 and {MAX_SEARCH_LIMIT}", 400)
        region = _validate_region(region, language)
        if safesearch not in SAFESEARCH_OPTIONS:
            raise ServiceError("Safesearch must be on, moderate, or off", 400)

        cache_key = f"duckduckgo:search:{region}:{safesearch}:{limit}:{query}"
        return self.cache.get_or_set(
            cache_key,
            lambda: self._search(query, limit, region, safesearch),
            ttl=self.config.cache_ttl,
        )

    def _search(self, query: str, limit: int, region: str, safesearch: str) -> dict:
        try:
            with DDGS(timeout=SEARCH_TIMEOUT_SECONDS) as ddgs:
                results = ddgs.text(
                    query,
                    region=region,
                    safesearch=safesearch,
                    max_results=limit,
                    backend="duckduckgo",
                )
        except RatelimitException as error:
            raise ServiceError("DuckDuckGo temporarily rate-limited the search", 429) from error
        except TimeoutException as error:
            raise ServiceError("DuckDuckGo search timed out", 504) from error
        except DDGSException as error:
            message = str(error).strip().lower()
            if message == "no results found.":
                results = []
            elif "ratelimit" in message or "rate limit" in message:
                raise ServiceError(
                    "DuckDuckGo temporarily rate-limited the search", 429,
                ) from error
            elif "timed out" in message or "timeout" in message:
                raise ServiceError("DuckDuckGo search timed out", 504) from error
            else:
                log.warning("DuckDuckGo provider search failed: %s", type(error).__name__)
                raise ServiceError("DuckDuckGo search is temporarily unavailable", 502) from error
        except Exception as error:
            log.error(
                "Unexpected DuckDuckGo provider failure (%s)",
                type(error).__name__,
            )
            raise ServiceError("DuckDuckGo search is temporarily unavailable", 502) from error

        if not isinstance(results, list):
            raise ServiceError("DuckDuckGo returned an invalid response", 502)
        if len(results) > limit:
            results = results[:limit]

        normalized = []
        for result in results:
            if (not isinstance(result, dict)
                    or not isinstance(result.get("title"), str)
                    or not isinstance(result.get("href"), str)
                    or not isinstance(result.get("body"), str)):
                raise ServiceError("DuckDuckGo returned an invalid search result", 502)
            try:
                url = urlsplit(result["href"])
            except ValueError as error:
                raise ServiceError("DuckDuckGo returned an invalid result URL", 502) from error
            if (url.scheme not in {"http", "https"} or not url.hostname
                    or url.username or url.password):
                raise ServiceError("DuckDuckGo returned an invalid result URL", 502)
            normalized.append({
                "title": result["title"],
                "url": result["href"],
                "snippet": result["body"],
                "source": "DuckDuckGo",
            })

        return {
            "query": query,
            "region": region,
            "safesearch": safesearch,
            "source": "DuckDuckGo",
            "count": len(normalized),
            "results": normalized,
        }

    def diagnostics(self) -> dict:
        return {
            "service": self.name,
            "enabled": True,
            "provider": "DuckDuckGo",
            "integration": "unofficial",
            "backend": "duckduckgo",
            "package_version": version("ddgs"),
            "timeout_seconds": SEARCH_TIMEOUT_SECONDS,
        }
