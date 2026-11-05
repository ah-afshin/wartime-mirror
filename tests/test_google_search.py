import os
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from app import create_app
from app.config import Config
from app.core.errors import ServiceError
from app.models import Base, GoogleSearchUsage

API_TOKEN = "google-search-test-token"
API_KEY = "serpapi-test-secret"


def make_app(*, google_search_enabled=True):
    config = Config(
        env="testing",
        secret_key="s" * 40,
        api_token=API_TOKEN,
        database_url="sqlite:///:memory:",
        google_search_enabled=google_search_enabled,
    )
    app = create_app(config)
    app.testing = True
    Base.metadata.create_all(app.extensions["database"].engine)
    return app


def auth_headers():
    return {"Authorization": f"Bearer {API_TOKEN}"}


def provider_response(payload, status=200):
    response = MagicMock()
    response.status_code = status
    response.json.return_value = payload
    return response


def search_payload():
    return {
        "organic_results": [{
            "title": "Python",
            "link": "https://www.python.org/",
            "snippet": "The official home of the Python Programming Language.",
            "position": 1,
        }],
    }


class GoogleSearchTests(unittest.TestCase):
    def setUp(self):
        self.api_key_env = patch.dict(os.environ, {"SERPAPI_API_KEY": API_KEY})
        self.api_key_env.start()
        self.addCleanup(self.api_key_env.stop)
        self.app = make_app()
        self.addCleanup(self.app.extensions["database"].dispose)
        self.client = self.app.test_client()
        self.service = self.app.extensions["mirror"].services["google_search"]
        self.upstream_patch = patch.object(self.service.http, "request")
        self.upstream = self.upstream_patch.start()
        self.addCleanup(self.upstream_patch.stop)
        self.upstream.return_value = provider_response(search_payload())

    def get_usage(self):
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        with self.app.extensions["database"].session_scope() as db_session:
            return db_session.get(GoogleSearchUsage, month)

    def test_search_returns_structured_google_results(self):
        response = self.client.get(
            "/api/google-search/v1/search?q=python&limit=5&gl=us&hl=en",
            headers=auth_headers(),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {
            "query": "python",
            "country": "us",
            "language": "en",
            "source": "Google Search",
            "count": 1,
            "results": [{
                "title": "Python",
                "url": "https://www.python.org/",
                "snippet": "The official home of the Python Programming Language.",
                "position": 1,
                "source": "Google Search",
            }],
        })
        self.upstream.assert_called_once()
        args, kwargs = self.upstream.call_args
        self.assertEqual(args[:2], ("GET", "https://serpapi.com/search.json"))
        self.assertEqual(kwargs["params"], {
            "engine": "google",
            "q": "python",
            "num": 5,
            "gl": "us",
            "hl": "en",
            "api_key": API_KEY,
        })
        self.assertEqual(kwargs["timeout"], 8)

    def test_search_returns_empty_results_without_fallback(self):
        self.upstream.return_value = provider_response({"organic_results": []})

        response = self.client.get(
            "/api/google-search/v1/search?q=unlikely+query",
            headers=auth_headers(),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["results"], [])
        self.assertEqual(response.get_json()["count"], 0)
        self.assertEqual(self.upstream.call_count, 1)

    def test_results_are_limited_and_invalid_links_rejected(self):
        self.upstream.return_value = provider_response({
            "organic_results": [
                {"title": f"Page {index}", "link": f"https://example.com/{index}",
                 "snippet": "Example", "position": index}
                for index in range(1, 13)
            ],
        })

        response = self.client.get(
            "/api/google-search/v1/search?q=example&limit=10",
            headers=auth_headers(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["count"], 10)

        self.upstream.return_value = provider_response({
            "organic_results": [{
                "title": "Unsafe", "link": "javascript:alert(1)", "snippet": "unsafe",
            }],
        })
        bad_response = self.client.get(
            "/api/google-search/v1/search?q=unsafe",
            headers=auth_headers(),
        )
        self.assertEqual(bad_response.status_code, 502)
        self.assertEqual(
            bad_response.get_json()["error"],
            "Google Search provider returned an invalid result",
        )

    def test_invalid_parameters_do_not_call_provider(self):
        paths = (
            "/api/google-search/v1/search",
            "/api/google-search/v1/search?q=%20",
            "/api/google-search/v1/search?q=ok&limit=abc",
            "/api/google-search/v1/search?q=ok&limit=0",
            "/api/google-search/v1/search?q=ok&limit=11",
            "/api/google-search/v1/search?q=ok&gl=usa",
            "/api/google-search/v1/search?q=ok&hl=invalid-locale",
        )
        for path in paths:
            with self.subTest(path=path):
                response = self.client.get(path, headers=auth_headers())
                self.assertEqual(response.status_code, 400)
        self.upstream.assert_not_called()

    def test_provider_rate_limit_and_quota_errors_return_429(self):
        self.upstream.return_value = provider_response({}, status=429)
        response = self.client.get(
            "/api/google-search/v1/search?q=python",
            headers=auth_headers(),
        )
        self.assertEqual(response.status_code, 429)

        self.upstream.return_value = provider_response(
            {"error": "Your monthly searches are exhausted"},
        )
        response = self.client.get(
            "/api/google-search/v1/search?q=another",
            headers=auth_headers(),
        )
        self.assertEqual(response.status_code, 429)
        self.assertNotIn(API_KEY.encode(), response.data)

    def test_timeout_is_mapped_without_leaking_provider_url_or_key(self):
        self.upstream.side_effect = ServiceError(
            "Upstream timed out",
            504,
            detail=f"https://serpapi.com/?api_key={API_KEY}",
        )

        response = self.client.get(
            "/api/google-search/v1/search?q=python",
            headers=auth_headers(),
        )

        self.assertEqual(response.status_code, 504)
        self.assertEqual(response.get_json()["error"], "Google Search provider timed out")
        self.assertNotIn(API_KEY.encode(), response.data)

    def test_other_provider_errors_are_sanitized(self):
        self.upstream.return_value = provider_response(
            {"error": f"bad key: {API_KEY}"},
            status=403,
        )
        response = self.client.get(
            "/api/google-search/v1/search?q=python",
            headers=auth_headers(),
        )
        self.assertEqual(response.status_code, 502)
        self.assertNotIn(API_KEY.encode(), response.data)
        self.assertNotIn(b"bad key", response.data)

    def test_malformed_provider_responses_return_502(self):
        self.upstream.return_value = provider_response({"search_metadata": {}})
        response = self.client.get(
            "/api/google-search/v1/search?q=python",
            headers=auth_headers(),
        )
        self.assertEqual(response.status_code, 502)

        malformed_json = provider_response({})
        malformed_json.json.side_effect = ValueError("invalid JSON")
        self.upstream.return_value = malformed_json
        response = self.client.get(
            "/api/google-search/v1/search?q=malformed",
            headers=auth_headers(),
        )
        self.assertEqual(response.status_code, 502)

    def test_cache_avoids_extra_provider_calls_and_quota_usage(self):
        first = self.client.get(
            "/api/google-search/v1/search?q=python",
            headers=auth_headers(),
        )
        second = self.client.get(
            "/api/google-search/v1/search?q=python",
            headers=auth_headers(),
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(self.upstream.call_count, 1)
        self.assertEqual(self.get_usage().requests_used, 1)

    def test_local_monthly_limit_fails_closed_before_provider_call(self):
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        with self.app.extensions["database"].session_scope() as db_session:
            db_session.add(GoogleSearchUsage(month=month, requests_used=200))

        response = self.client.get(
            "/api/google-search/v1/search?q=limit",
            headers=auth_headers(),
        )

        self.assertEqual(response.status_code, 429)
        self.assertEqual(
            response.get_json()["error"],
            "Google Search monthly free-tier request limit reached",
        )
        self.upstream.assert_not_called()

    def test_failed_provider_attempt_still_consumes_local_reservation(self):
        self.upstream.return_value = provider_response({}, status=503)
        response = self.client.get(
            "/api/google-search/v1/search?q=failed",
            headers=auth_headers(),
        )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(self.get_usage().requests_used, 1)

    def test_missing_api_key_is_an_explicit_configuration_error(self):
        with patch.dict(os.environ, {"SERPAPI_API_KEY": ""}):
            response = self.client.get(
                "/api/google-search/v1/search?q=python",
                headers=auth_headers(),
            )

        self.assertEqual(response.status_code, 503)
        self.upstream.assert_not_called()

    def test_api_and_page_require_authentication(self):
        response = self.client.get("/api/google-search/v1/search?q=python")
        self.assertEqual(response.status_code, 401)
        self.upstream.assert_not_called()

        response = self.client.get("/google-search")
        self.assertEqual(response.status_code, 302)

    def test_dedicated_page_and_home_service_card(self):
        page = self.client.get("/google-search", headers=auth_headers())
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Google Web Search", page.data)
        self.assertIn(b"google_search.js", page.data)

        home = self.client.get("/", headers=auth_headers())
        self.assertIn(b"Google Web Search", home.data)
        self.assertIn(b"/google-search", home.data)

    def test_diagnostics_reports_provider_and_quota_without_exposing_key(self):
        response = self.client.get(
            "/api/google-search/v1/diagnostics",
            headers=auth_headers(),
        )

        self.assertEqual(response.status_code, 200)
        diagnostics = response.get_json()
        self.assertEqual(diagnostics["provider"], "SerpApi")
        self.assertEqual(diagnostics["engine"], "google")
        self.assertEqual(diagnostics["integration"], "third-party")
        self.assertTrue(diagnostics["api_key_configured"])
        self.assertEqual(diagnostics["monthly_request_limit"], 200)
        self.assertEqual(diagnostics["monthly_requests_used"], 0)
        self.assertNotIn(API_KEY, str(diagnostics))
        self.upstream.assert_not_called()

    def test_disabled_service_is_not_instantiated_or_registered(self):
        app = make_app(google_search_enabled=False)
        self.addCleanup(app.extensions["database"].dispose)

        self.assertNotIn("google_search", app.extensions["mirror"].services)
        response = app.test_client().get(
            "/api/google-search/v1/search?q=python",
            headers=auth_headers(),
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(app.test_client().get("/google-search").status_code, 404)

    def test_feature_flag_defaults_off_and_loads_from_environment(self):
        self.assertFalse(Config(env="testing").google_search_enabled)
        with patch.dict(os.environ, {"GOOGLE_SEARCH_ENABLED": "yes"}):
            self.assertTrue(Config.from_env().google_search_enabled)


if __name__ == "__main__":
    unittest.main()
