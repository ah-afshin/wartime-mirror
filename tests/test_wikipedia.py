import os
import unittest
from unittest.mock import patch

from app import create_app
from app.config import Config
from app.core.errors import ServiceError
from app.models import Base

API_TOKEN = "wikipedia-test-token"


def make_app(*, wikipedia_enabled=True):
    config = Config(
        env="testing",
        secret_key="s" * 40,
        api_token=API_TOKEN,
        database_url="sqlite:///:memory:",
        wikipedia_enabled=wikipedia_enabled,
    )
    app = create_app(config)
    app.testing = True
    database = app.extensions["database"]
    Base.metadata.create_all(database.engine)
    return app


def auth_headers():
    return {"Authorization": f"Bearer {API_TOKEN}"}


def search_response():
    return {
        "query": {
            "search": [{
                "pageid": 123,
                "title": "Neural network",
                "snippet": "A <span class=\"searchmatch\">neural network</span> is a model.",
                "size": 12500,
                "wordcount": 1800,
                "timestamp": "2025-01-02T03:04:05Z",
            }],
        },
    }


def page_response():
    return {
        "query": {
            "pages": [{
                "pageid": 123,
                "title": "Neural network",
                "canonicalurl": "https://en.wikipedia.org/wiki/Neural_network",
                "extract": "A neural network is a model inspired by the human brain.",
                "touched": "2025-01-02T03:04:05Z",
                "length": 12500,
                "contentmodel": "wikitext",
            }],
        },
    }


class WikipediaApiTests(unittest.TestCase):
    def setUp(self):
        self.app = make_app()
        self.addCleanup(self.app.extensions["database"].dispose)
        self.client = self.app.test_client()
        self.service = self.app.extensions["mirror"].services["wikipedia"]
        self.get_json = patch.object(self.service.http, "get_json")
        self.upstream = self.get_json.start()
        self.addCleanup(self.get_json.stop)

    def test_search_returns_plain_text_snippet_and_metadata(self):
        self.upstream.return_value = search_response()

        response = self.client.get(
            "/api/wikipedia/v1/search?q=neural+networks&lang=en&limit=5",
            headers=auth_headers(),
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["query"], "neural networks")
        self.assertEqual(payload["language"], "en")
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["results"][0], {
            "pageid": 123,
            "title": "Neural network",
            "url": "https://en.wikipedia.org/wiki/Neural_network",
            "snippet": "A neural network is a model.",
            "size": 12500,
            "wordcount": 1800,
            "timestamp": "2025-01-02T03:04:05Z",
        })
        self.assertNotIn("<span", payload["results"][0]["snippet"])
        args, kwargs = self.upstream.call_args
        self.assertEqual(args[0], "https://en.wikipedia.org/w/api.php")
        self.assertEqual(kwargs["params"]["srsearch"], "neural networks")
        self.assertEqual(kwargs["params"]["srlimit"], 5)

    def test_search_supports_persian_language(self):
        self.upstream.return_value = {
            "query": {
                "search": [{
                    "pageid": 10,
                    "title": "شبکه عصبی",
                    "snippet": "یک مدل محاسباتی",
                }],
            },
        }

        response = self.client.get(
            "/api/wikipedia/v1/search?q=شبکه+عصبی&lang=fa",
            headers=auth_headers(),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["language"], "fa")
        self.assertEqual(
            response.get_json()["results"][0]["url"],
            "https://fa.wikipedia.org/wiki/%D8%B4%D8%A8%DA%A9%D9%87_%D8%B9%D8%B5%D8%A8%DB%8C",
        )
        self.assertEqual(
            self.upstream.call_args.args[0],
            "https://fa.wikipedia.org/w/api.php",
        )

    def test_search_with_no_results_returns_empty_list(self):
        self.upstream.return_value = {"query": {"search": []}}

        response = self.client.get(
            "/api/wikipedia/v1/search?q=no+such+article",
            headers=auth_headers(),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["count"], 0)
        self.assertEqual(response.get_json()["results"], [])

    def test_page_returns_plain_text_extract_and_metadata(self):
        self.upstream.return_value = page_response()

        response = self.client.get(
            "/api/wikipedia/v1/page?title=Neural+network&lang=en",
            headers=auth_headers(),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["page"], {
            "pageid": 123,
            "title": "Neural network",
            "language": "en",
            "url": "https://en.wikipedia.org/wiki/Neural_network",
            "extract": "A neural network is a model inspired by the human brain.",
            "last_modified": "2025-01-02T03:04:05Z",
            "size": 12500,
            "content_model": "wikitext",
        })
        args, kwargs = self.upstream.call_args
        self.assertEqual(args[0], "https://en.wikipedia.org/w/api.php")
        self.assertEqual(kwargs["params"]["prop"], "info|extracts")
        self.assertEqual(kwargs["params"]["explaintext"], 1)

    def test_page_supports_persian_language(self):
        self.upstream.return_value = {
            "query": {
                "pages": [{
                    "pageid": 11,
                    "title": "شبکه عصبی",
                    "canonicalurl": "https://fa.wikipedia.org/wiki/شبکه_عصبی",
                    "extract": "متن مقاله",
                }],
            },
        }

        response = self.client.get(
            "/api/wikipedia/v1/page?title=شبکه+عصبی&lang=fa",
            headers=auth_headers(),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["page"]["language"], "fa")
        self.assertEqual(response.get_json()["page"]["extract"], "متن مقاله")

    def test_missing_page_returns_404(self):
        self.upstream.return_value = {
            "query": {"pages": [{"ns": 0, "title": "Missing article", "missing": True}]},
        }

        response = self.client.get(
            "/api/wikipedia/v1/page?title=Missing+article",
            headers=auth_headers(),
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json(), {
            "error": "Wikipedia page not found",
            "status": 404,
        })

    def test_invalid_parameters_return_400_without_upstream_request(self):
        paths = (
            "/api/wikipedia/v1/search",
            "/api/wikipedia/v1/search?q=%20",
            "/api/wikipedia/v1/search?q=valid&lang=bad_lang",
            "/api/wikipedia/v1/search?q=valid&limit=abc",
            "/api/wikipedia/v1/search?q=valid&limit=21",
            "/api/wikipedia/v1/page",
            "/api/wikipedia/v1/page?title=valid&lang=invalid.lang",
        )
        for path in paths:
            with self.subTest(path=path):
                response = self.client.get(path, headers=auth_headers())
                self.assertEqual(response.status_code, 400)
        self.upstream.assert_not_called()

    def test_unauthenticated_requests_are_denied(self):
        response = self.client.get("/api/wikipedia/v1/search?q=neural")

        self.assertEqual(response.status_code, 401)
        self.upstream.assert_not_called()

    def test_upstream_timeout_is_reported_as_504(self):
        self.upstream.side_effect = ServiceError("Upstream timed out", 504)

        response = self.client.get(
            "/api/wikipedia/v1/search?q=neural",
            headers=auth_headers(),
        )

        self.assertEqual(response.status_code, 504)
        self.assertEqual(response.get_json()["error"], "Upstream timed out")

    def test_malformed_upstream_payload_is_reported_as_502(self):
        self.upstream.return_value = {"query": {"unexpected": []}}

        response = self.client.get(
            "/api/wikipedia/v1/search?q=neural",
            headers=auth_headers(),
        )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.get_json()["error"], "Wikipedia returned an invalid search response")

    def test_invalid_upstream_json_is_reported_as_502(self):
        self.upstream.side_effect = ServiceError("Upstream returned invalid JSON", 502)

        response = self.client.get(
            "/api/wikipedia/v1/page?title=Neural",
            headers=auth_headers(),
        )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.get_json()["error"], "Upstream returned invalid JSON")

    def test_untrusted_canonical_url_is_rejected(self):
        response_data = page_response()
        response_data["query"]["pages"][0]["canonicalurl"] = "https://example.com/article"
        self.upstream.return_value = response_data

        response = self.client.get(
            "/api/wikipedia/v1/page?title=Neural",
            headers=auth_headers(),
        )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.get_json()["error"], "Wikipedia returned an invalid article URL")

    def test_search_results_are_cached(self):
        self.upstream.return_value = search_response()

        first = self.client.get(
            "/api/wikipedia/v1/search?q=neural",
            headers=auth_headers(),
        )
        second = self.client.get(
            "/api/wikipedia/v1/search?q=neural",
            headers=auth_headers(),
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.upstream.assert_called_once()

    def test_distinct_case_sensitive_queries_use_distinct_cache_entries(self):
        self.upstream.side_effect = [
            {"query": {"search": []}},
            {"query": {"search": []}},
        ]

        first = self.client.get(
            "/api/wikipedia/v1/search?q=Neural",
            headers=auth_headers(),
        )
        second = self.client.get(
            "/api/wikipedia/v1/search?q=neural",
            headers=auth_headers(),
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(self.upstream.call_count, 2)

    def test_diagnostics_reports_upstream(self):
        self.upstream.return_value = {
            "query": {"general": {"sitename": "Wikipedia"}},
        }

        response = self.client.get(
            "/api/wikipedia/v1/diagnostics",
            headers=auth_headers(),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {
            "service": "wikipedia",
            "enabled": True,
            "reachable": True,
            "upstream": "https://en.wikipedia.org/w/api.php",
            "site": "Wikipedia",
        })

    def test_disabled_service_is_not_instantiated_or_registered(self):
        app = make_app(wikipedia_enabled=False)
        self.addCleanup(app.extensions["database"].dispose)
        client = app.test_client()

        self.assertNotIn("wikipedia", app.extensions["mirror"].services)
        response = client.get(
            "/api/wikipedia/v1/search?q=neural",
            headers=auth_headers(),
        )

        self.assertEqual(response.status_code, 404)

    def test_feature_flag_loads_from_environment_and_defaults_off(self):
        self.assertFalse(Config(env="testing").wikipedia_enabled)
        with patch.dict(os.environ, {"WIKIPEDIA_ENABLED": "true"}):
            self.assertTrue(Config.from_env().wikipedia_enabled)


if __name__ == "__main__":
    unittest.main()
