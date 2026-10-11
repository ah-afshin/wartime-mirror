"""One shared outbound HTTP client: timeouts, small retry, response size cap."""
from __future__ import annotations

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from app.core.errors import ServiceError


class HttpClient:
    def __init__(self, timeout: float, max_bytes: int, user_agent: str):
        self.timeout = timeout
        self.max_bytes = max_bytes
        self.session = requests.Session()
        self.session.headers["User-Agent"] = user_agent
        retry = Retry(total=2, backoff_factor=0.4, status_forcelist=(502, 503, 504),
                      allowed_methods=frozenset({"GET", "HEAD"}))
        adapter = HTTPAdapter(max_retries=retry, pool_maxsize=10)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self._plain = requests.Session()  # no retries (fast-fail probes)
        self._plain.headers["User-Agent"] = user_agent

    def request(self, method: str, url: str, *, timeout: float | None = None,
                max_bytes: int | None = None, retries: bool = True,
                **kwargs) -> requests.Response:
        limit = max_bytes or self.max_bytes
        session = self.session if retries else self._plain
        try:
            r = session.request(method, url, timeout=timeout or self.timeout, stream=True, **kwargs)
        except requests.Timeout as e:
            raise ServiceError("Upstream timed out", 504, detail=type(e).__name__)
        except requests.RequestException as e:
            raise ServiceError("Upstream unreachable", 502, detail=f"{type(e).__name__}: {str(e)[:160]}")
        body = bytearray()
        try:
            for chunk in r.iter_content(65536):
                body.extend(chunk)
                if len(body) > limit:
                    raise ServiceError("Upstream response too large", 502)
        except requests.RequestException as e:
            raise ServiceError("Upstream read failed", 502, detail=type(e).__name__)
        finally:
            r.close()
        r._content = bytes(body)  # lets .text/.json() work after streaming
        return r

    def _json(self, method: str, url: str, **kwargs):
        r = self.request(method, url, **kwargs)
        if r.status_code >= 400:
            raise ServiceError(f"Upstream returned HTTP {r.status_code}", 502)
        try:
            return r.json()
        except ValueError:
            raise ServiceError("Upstream returned invalid JSON", 502)

    def get_json(self, url: str, **kwargs):
        return self._json("GET", url, **kwargs)

    def post_json(self, url: str, payload: dict, **kwargs):
        return self._json("POST", url, json=payload, **kwargs)
