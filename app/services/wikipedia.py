"""Independent Wikipedia search and article retrieval via the MediaWiki API."""
from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import quote, urlsplit

from app.core.errors import ServiceError
from app.services.base import Service

MAX_QUERY_LENGTH = 300
MAX_TITLE_LENGTH = 300
MAX_SEARCH_LIMIT = 20
DEFAULT_SEARCH_LIMIT = 5
DEFAULT_LANGUAGE = "en"
LANGUAGE_PATTERN = re.compile(r"[a-z][a-z0-9-]{0,11}\Z")


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _plain_text(value: str) -> str:
    parser = _TextExtractor()
    parser.feed(value)
    parser.close()
    return " ".join(" ".join(parser.parts).split())


def _clean_text(value: str, label: str, max_length: int) -> str:
    if not isinstance(value, str) or any(
        ord(char) < 32 or ord(char) == 127 for char in value
    ):
        raise ServiceError(f"Invalid {label}", 400)
    cleaned = value.strip()
    if not cleaned or len(cleaned) > max_length:
        raise ServiceError(f"Invalid {label}", 400)
    return cleaned


def _validate_language(language: str) -> str:
    if not isinstance(language, str) or not LANGUAGE_PATTERN.fullmatch(language):
        raise ServiceError("Invalid language code", 400)
    return language


def _article_url(language: str, title: str) -> str:
    encoded_title = quote(title.replace(" ", "_"), safe="()")
    return f"https://{language}.wikipedia.org/wiki/{encoded_title}"


class WikipediaService(Service):
    name = "wikipedia"
    title = "Wikipedia"
    description = "Search Wikipedia and retrieve readable article extracts."

    def _api_url(self, language: str) -> str:
        return f"https://{language}.wikipedia.org/w/api.php"

    def _request(self, language: str, params: dict) -> dict:
        data = self.http.get_json(self._api_url(language), params=params)
        if not isinstance(data, dict):
            raise ServiceError("Wikipedia returned an invalid response", 502)
        if "error" in data:
            raise ServiceError("Wikipedia rejected the request", 502)
        return data

    def search(self, query: str, language: str = DEFAULT_LANGUAGE,
               limit: int = DEFAULT_SEARCH_LIMIT) -> dict:
        query = _clean_text(query, "search query", MAX_QUERY_LENGTH)
        language = _validate_language(language)
        if type(limit) is not int or not 1 <= limit <= MAX_SEARCH_LIMIT:
            raise ServiceError(f"Limit must be between 1 and {MAX_SEARCH_LIMIT}", 400)

        cache_key = f"wikipedia:search:{language}:{limit}:{query}"
        return self.cache.get_or_set(
            cache_key,
            lambda: self._search(query, language, limit),
            ttl=self.config.cache_ttl,
        )

    def _search(self, query: str, language: str, limit: int) -> dict:
        data = self._request(language, {
            "action": "query",
            "list": "search",
            "srsearch": query,
            "srlimit": limit,
            "srprop": "snippet|size|wordcount|timestamp",
            "format": "json",
            "formatversion": 2,
        })
        query_data = data.get("query")
        results = query_data.get("search") if isinstance(query_data, dict) else None
        if not isinstance(results, list):
            raise ServiceError("Wikipedia returned an invalid search response", 502)

        pages = []
        for result in results:
            if (not isinstance(result, dict)
                    or type(result.get("pageid")) is not int
                    or not isinstance(result.get("title"), str)
                    or not result["title"].strip()
                    or not isinstance(result.get("snippet"), str)):
                raise ServiceError("Wikipedia returned an invalid search result", 502)
            pages.append({
                "pageid": result["pageid"],
                "title": result["title"],
                "url": _article_url(language, result["title"]),
                "snippet": _plain_text(result["snippet"]),
                "size": result.get("size") if type(result.get("size")) is int else None,
                "wordcount": (
                    result.get("wordcount")
                    if type(result.get("wordcount")) is int else None
                ),
                "timestamp": (
                    result.get("timestamp")
                    if isinstance(result.get("timestamp"), str) else None
                ),
            })

        return {
            "query": query,
            "language": language,
            "count": len(pages),
            "results": pages,
        }

    def page(self, title: str, language: str = DEFAULT_LANGUAGE) -> dict:
        title = _clean_text(title, "page title", MAX_TITLE_LENGTH)
        language = _validate_language(language)
        cache_key = f"wikipedia:page:{language}:{title}"
        return self.cache.get_or_set(
            cache_key,
            lambda: self._page(title, language),
            ttl=self.config.cache_ttl,
        )

    def _page(self, title: str, language: str) -> dict:
        data = self._request(language, {
            "action": "query",
            "prop": "info|extracts",
            "inprop": "url",
            "explaintext": 1,
            "exsectionformat": "plain",
            "redirects": 1,
            "titles": title,
            "format": "json",
            "formatversion": 2,
        })
        query_data = data.get("query")
        pages = query_data.get("pages") if isinstance(query_data, dict) else None
        if not isinstance(pages, list) or len(pages) != 1 or not isinstance(pages[0], dict):
            raise ServiceError("Wikipedia returned an invalid page response", 502)

        article = pages[0]
        if article.get("missing") is True or article.get("invalid") is True:
            raise ServiceError("Wikipedia page not found", 404)
        pageid = article.get("pageid")
        resolved_title = article.get("title")
        extract = article.get("extract")
        canonical_url = article.get("canonicalurl")
        if (type(pageid) is not int or not isinstance(resolved_title, str)
                or not resolved_title or not isinstance(extract, str)
                or not isinstance(canonical_url, str)):
            raise ServiceError("Wikipedia returned an invalid article", 502)
        canonical = urlsplit(canonical_url)
        if (canonical.scheme != "https"
                or canonical.netloc != f"{language}.wikipedia.org"
                or not canonical.path.startswith("/wiki/")):
            raise ServiceError("Wikipedia returned an invalid article URL", 502)

        return {
            "page": {
                "pageid": pageid,
                "title": resolved_title,
                "language": language,
                "url": canonical_url,
                "extract": extract,
                "last_modified": (
                    article.get("touched") if isinstance(article.get("touched"), str) else None
                ),
                "size": article.get("length") if type(article.get("length")) is int else None,
                "content_model": (
                    article.get("contentmodel")
                    if isinstance(article.get("contentmodel"), str) else None
                ),
            },
        }

    def diagnostics(self) -> dict:
        cache_key = "wikipedia:diagnostics"
        return self.cache.get_or_set(
            cache_key,
            self._check_upstream,
            ttl=60,
        )

    def _check_upstream(self) -> dict:
        data = self._request(DEFAULT_LANGUAGE, {
            "action": "query",
            "meta": "siteinfo",
            "siprop": "general",
            "format": "json",
            "formatversion": 2,
        })
        query_data = data.get("query")
        general = query_data.get("general") if isinstance(query_data, dict) else None
        if not isinstance(general, dict) or not isinstance(general.get("sitename"), str):
            raise ServiceError("Wikipedia returned an invalid diagnostics response", 502)
        return {
            "service": self.name,
            "enabled": True,
            "reachable": True,
            "upstream": self._api_url(DEFAULT_LANGUAGE),
            "site": general["sitename"],
        }
