"""Checks which outside sites this server can reach. Very useful on PythonAnywhere,
whose free plan only allows a whitelist of outbound hosts."""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

from app.core.errors import ServiceError
from app.services.base import Service

TARGETS = (
    ("Wikipedia", "https://en.wikipedia.org/robots.txt"),
    ("Google", "https://www.google.com/robots.txt"),
    ("YouTube", "https://www.youtube.com/robots.txt"),
    ("DuckDuckGo", "https://duckduckgo.com/robots.txt"),
    ("Gemini API", "https://generativelanguage.googleapis.com/"),
    ("Groq API", "https://api.groq.com/openai/v1/models"),
    ("OpenRouter API", "https://openrouter.ai/api/v1/models"),
    ("Cerebras API", "https://api.cerebras.ai/v1/models"),
    ("Mistral API", "https://api.mistral.ai/v1/models"),
    ("Hugging Face", "https://huggingface.co/robots.txt"),
)


class DiagnosticsService(Service):
    name = "diagnostics"
    title = "Network diagnostics"
    description = "Which outside sites can this server reach?"
    home_endpoint = "diagnostics.page"

    def _probe(self, target):
        label, url = target
        start = time.monotonic()
        row = {"label": label, "host": urlsplit(url).hostname, "reachable": False, "status": None, "error": None}
        try:
            # Any HTTP answer (even 401/404) proves the host is reachable.
            r = self.http.request("HEAD", url, timeout=6, max_bytes=2048, retries=False)
            row.update(reachable=True, status=r.status_code)
        except ServiceError as e:
            row["error"] = e.detail or e.message
        row["ms"] = int((time.monotonic() - start) * 1000)
        return row

    def _run(self):
        with ThreadPoolExecutor(max_workers=len(TARGETS)) as pool:
            return list(pool.map(self._probe, TARGETS))

    def check(self):
        return self.cache.get_or_set("diagnostics:outbound", self._run, ttl=60)
