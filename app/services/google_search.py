"""Independent Google web search through SerpApi's Google engine."""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.core.errors import ServiceError
from app.models import GoogleSearchUsage
from app.services.base import Service

log = logging.getLogger(__name__)

SERPAPI_ENDPOINT = "https://serpapi.com/search.json"
SEARCH_TIMEOUT_SECONDS = 8
MAX_QUERY_LENGTH = 500
MAX_SEARCH_LIMIT = 10
DEFAULT_SEARCH_LIMIT = 5
MONTHLY_REQUEST_LIMIT = 200
COUNTRY_PATTERN = re.compile(r"[a-z]{2}\Z")
LANGUAGE_PATTERN = re.compile(r"[a-z]{2,3}(?:-[a-z]{2})?\Z")


def _validate_query(query: str) -> str:
    if (not isinstance(query, str) or not query.strip()
            or len(query) > MAX_QUERY_LENGTH
            or any(ord(char) < 32 or ord(char) == 127 for char in query)):
        raise ServiceError(f"Query must contain 1 to {MAX_QUERY_LENGTH} valid characters", 400)
    return query.strip()


def _validate_locale(gl: str, hl: str) -> tuple[str, str]:
    if not isinstance(gl, str) or not COUNTRY_PATTERN.fullmatch(gl):
        raise ServiceError("Country must be a two-letter code", 400)
    if not isinstance(hl, str) or not LANGUAGE_PATTERN.fullmatch(hl):
        raise ServiceError("Language must be a supported language code", 400)
    return gl, hl


def _valid_result_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    return bool(
        parsed.scheme in {"http", "https"}
        and parsed.hostname
        and not parsed.username
        and not parsed.password
    )


class GoogleSearchService(Service):
    name = "google_search"
    title = "Google Web Search"
    description = "Search Google web results with the independent Google Search service."
    home_endpoint = "google_search.page"

    def search(self, query: str, limit: int = DEFAULT_SEARCH_LIMIT,
               gl: str = "us", hl: str = "en") -> dict:
        query = _validate_query(query)
        if type(limit) is not int or not 1 <= limit <= MAX_SEARCH_LIMIT:
            raise ServiceError(f"Limit must be between 1 and {MAX_SEARCH_LIMIT}", 400)
        gl, hl = _validate_locale(gl, hl)

        api_key = self.config.secret("SERPAPI_API_KEY")
        if not api_key:
            raise ServiceError("Google Search is not configured", 503)

        cache_key = f"google_search:{gl}:{hl}:{limit}:{query}"
        cached = self.cache.get(cache_key)
        if cached is not None:
            return cached

        self._reserve_monthly_request()
        result = self._search(query, limit, gl, hl, api_key)
        self.cache.set(cache_key, result, ttl=self.config.cache_ttl)
        return result

    def _reserve_monthly_request(self) -> None:
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        table = GoogleSearchUsage.__table__
        dialect = self.ctx.database.engine.dialect.name
        if dialect == "sqlite":
            insert = sqlite_insert(table)
        elif dialect == "postgresql":
            insert = postgresql_insert(table)
        else:
            raise ServiceError(
                "Google Search quota accounting is unsupported for the configured database",
                503,
            )

        statement = insert.values(month=month, requests_used=1).on_conflict_do_update(
            index_elements=[table.c.month],
            set_={"requests_used": table.c.requests_used + 1},
            where=table.c.requests_used < MONTHLY_REQUEST_LIMIT,
        )
        with self.ctx.database.session_scope() as db_session:
            reserved = db_session.execute(statement)
            if reserved.rowcount != 1:
                raise ServiceError(
                    "Google Search monthly free-tier request limit reached",
                    429,
                )

    def _search(self, query: str, limit: int, gl: str, hl: str, api_key: str) -> dict:
        try:
            response = self.http.request(
                "GET",
                SERPAPI_ENDPOINT,
                timeout=SEARCH_TIMEOUT_SECONDS,
                params={
                    "engine": "google",
                    "q": query,
                    "num": limit,
                    "gl": gl,
                    "hl": hl,
                    "api_key": api_key,
                },
            )
        except ServiceError as error:
            if error.status == 504:
                raise ServiceError("Google Search provider timed out", 504) from error
            raise ServiceError("Google Search provider is temporarily unavailable", 502) from error

        if response.status_code == 429:
            raise ServiceError("Google Search provider rate limit reached", 429)
        if response.status_code >= 400:
            raise ServiceError("Google Search provider is temporarily unavailable", 502)
        try:
            data = response.json()
        except ValueError as error:
            raise ServiceError("Google Search provider returned invalid JSON", 502) from error
        if not isinstance(data, dict):
            raise ServiceError("Google Search provider returned an invalid response", 502)
        if "error" in data:
            provider_error = data.get("error")
            message = provider_error.lower() if isinstance(provider_error, str) else ""
            if any(term in message for term in (
                "monthly searches",
                "searches left",
                "out of searches",
                "run out of searches",
                "searches exhausted",
                "search limit",
                "quota",
            )):
                raise ServiceError("Google Search provider quota exhausted", 429)
            log.warning("Google Search provider rejected a request")
            raise ServiceError("Google Search provider is temporarily unavailable", 502)

        organic = data.get("organic_results")
        if not isinstance(organic, list):
            raise ServiceError("Google Search provider returned an invalid results list", 502)
        results = []
        for item in organic[:limit]:
            if (not isinstance(item, dict)
                    or not isinstance(item.get("title"), str)
                    or not item["title"].strip()
                    or not isinstance(item.get("link"), str)
                    or not _valid_result_url(item["link"])
                    or ("snippet" in item and not isinstance(item["snippet"], str))):
                raise ServiceError("Google Search provider returned an invalid result", 502)
            position = item.get("position")
            results.append({
                "title": item["title"],
                "url": item["link"],
                "snippet": item.get("snippet", ""),
                "position": position if type(position) is int and position > 0 else len(results) + 1,
                "source": "Google Search",
            })

        return {
            "query": query,
            "country": gl,
            "language": hl,
            "source": "Google Search",
            "count": len(results),
            "results": results,
        }

    def diagnostics(self) -> dict:
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        with self.ctx.database.session_scope() as db_session:
            used = db_session.scalar(
                select(GoogleSearchUsage.requests_used)
                .where(GoogleSearchUsage.month == month)
            ) or 0
        return {
            "service": self.name,
            "enabled": True,
            "provider": "SerpApi",
            "engine": "google",
            "integration": "third-party",
            "api_key_configured": bool(self.config.secret("SERPAPI_API_KEY")),
            "month": month,
            "monthly_request_limit": MONTHLY_REQUEST_LIMIT,
            "monthly_requests_used": used,
            "monthly_requests_remaining": max(0, MONTHLY_REQUEST_LIMIT - used),
            "timeout_seconds": SEARCH_TIMEOUT_SECONDS,
        }
