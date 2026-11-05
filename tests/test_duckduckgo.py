import os
import unittest
from unittest.mock import MagicMock, patch

from ddgs.exceptions import DDGSException, RatelimitException, TimeoutException

from app import create_app
from app.config import Config

API_TOKEN = "duckduckgo-test-token"


def make_app(*, duckduckgo_enabled=True):
    config = Config(
        env="testing",
        secret_key="s" * 40,
        api_token=API_TOKEN,
        database_url="sqlite:///:memory:",
        duckduckgo_enabled=duckduckgo_enabled,
    )
    app = create_app(config)
    app.testing = True
    return app


def auth_headers():
    return {"Authorization": f"Bearer {API_TOKEN}"}


class DuckDuckGoApiTests(unittest.TestCase):
    def setUp(self):
        self.app = make_app()
        self.addCleanup(self.app.extensions["database"].dispose)
        self.client = self.app.test_client()
        self.service = self.app.extensions["mirror"].services["duckduckgo"]
        self.provider = MagicMock()
        self.ddgs_patch = patch("app.services.duckduckgo.DDGS")
        self.ddgs = self.ddgs_patch.start()
        self.addCleanup(self.ddgs_patch.stop)
        self.ddgs.return_value.__enter__.return_value = self.provider
        self.provider.text.return_value = [{
            "title": "Python",
            "href": "https://www.python.org/",
            "body": "The official home of the Python Programming Language.",
        }]

    def test_search_returns_structured_results_and_selects_direct_backend(self):
        response = self.client.get(
            "/api/duckduckgo/v1/search?q=python&limit=5",
            headers=auth_headers(),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {
            "query": "python",
            "region": "us-en",
            "safesearch": "moderate",
            "source": "DuckDuckGo",
            "count": 1,
            "results": [{
                "title": "Python",
                "url": "https://www.python.org/",
                "snippet": "The official home of the Python Programming Language.",
                "source": "DuckDuckGo",
            }],
        })
        self.ddgs.assert_called_once_with(timeout=6)
        self.provider.text.assert_called_once_with(
            "python",
            region="us-en",
            safesearch="moderate",
            max_results=5,
            backend="duckduckgo",
        )

    def test_search_supports_region_language_and_safe_search(self):
        response = self.client.get(
            "/api/duckduckgo/v1/search?q=python&region=uk-en&lang=fr&safesearch=off",
            headers=auth_headers(),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["region"], "uk-fr")
        self.assertEqual(response.get_json()["safesearch"], "off")
        self.assertEqual(
            self.provider.text.call_args.kwargs,
            {
                "region": "uk-fr",
                "safesearch": "off",
                "max_results": 5,
                "backend": "duckduckgo",
            },
        )

    def test_search_with_no_results_returns_empty_result_list(self):
        self.provider.text.return_value = []

        response = self.client.get(
            "/api/duckduckgo/v1/search?q=no+results",
            headers=auth_headers(),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["count"], 0)
        self.assertEqual(response.get_json()["results"], [])

    def test_package_no_results_exception_is_not_reported_as_provider_failure(self):
        self.provider.text.side_effect = DDGSException("No results found.")

        response = self.client.get(
            "/api/duckduckgo/v1/search?q=no+results",
            headers=auth_headers(),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["results"], [])

    def test_provider_rate_limit_returns_429(self):
        self.provider.text.side_effect = RatelimitException("rate limited")

        response = self.client.get(
            "/api/duckduckgo/v1/search?q=python",
            headers=auth_headers(),
        )

        self.assertEqual(response.status_code, 429)
        self.assertEqual(
            response.get_json()["error"],
            "DuckDuckGo temporarily rate-limited the search",
        )

    def test_wrapped_provider_rate_limit_returns_429(self):
        self.provider.text.side_effect = DDGSException("RatelimitException('blocked')")

        response = self.client.get(
            "/api/duckduckgo/v1/search?q=python",
            headers=auth_headers(),
        )

        self.assertEqual(response.status_code, 429)

    def test_timeout_returns_504(self):
        self.provider.text.side_effect = TimeoutException("timed out")

        response = self.client.get(
            "/api/duckduckgo/v1/search?q=python",
            headers=auth_headers(),
        )

        self.assertEqual(response.status_code, 504)
        self.assertEqual(response.get_json()["error"], "DuckDuckGo search timed out")

    def test_wrapped_timeout_returns_504(self):
        self.provider.text.side_effect = DDGSException("request timed out")

        response = self.client.get(
            "/api/duckduckgo/v1/search?q=python",
            headers=auth_headers(),
        )

        self.assertEqual(response.status_code, 504)

    def test_provider_failure_does_not_expose_internal_exception(self):
        self.provider.text.side_effect = RuntimeError("sensitive provider internals")

        response = self.client.get(
            "/api/duckduckgo/v1/search?q=python",
            headers=auth_headers(),
        )

        self.assertEqual(response.status_code, 502)
        self.assertNotIn(b"sensitive provider internals", response.data)

    def test_malformed_provider_results_return_502(self):
        self.provider.text.return_value = [{
            "title": "Python",
            "href": "javascript:alert(1)",
            "body": "Unsafe link",
        }]

        response = self.client.get(
            "/api/duckduckgo/v1/search?q=python",
            headers=auth_headers(),
        )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(
            response.get_json()["error"],
            "DuckDuckGo returned an invalid result URL",
        )

    def test_invalid_parameters_return_400_without_provider_call(self):
        paths = (
            "/api/duckduckgo/v1/search",
            "/api/duckduckgo/v1/search?q=%20",
            "/api/duckduckgo/v1/search?q=valid&limit=abc",
            "/api/duckduckgo/v1/search?q=valid&limit=21",
            "/api/duckduckgo/v1/search?q=valid&region=invalid",
            "/api/duckduckgo/v1/search?q=valid&lang=invalid",
            "/api/duckduckgo/v1/search?q=valid&safesearch=unsafe",
        )
        for path in paths:
            with self.subTest(path=path):
                response = self.client.get(path, headers=auth_headers())
                self.assertEqual(response.status_code, 400)
        self.ddgs.assert_not_called()

    def test_endpoint_rate_limit_returns_429(self):
        with patch("app.routes.duckduckgo._limiter.allow", return_value=False):
            response = self.client.get(
                "/api/duckduckgo/v1/search?q=python",
                headers=auth_headers(),
            )

        self.assertEqual(response.status_code, 429)
        self.ddgs.assert_not_called()

    def test_successful_search_is_cached(self):
        first = self.client.get(
            "/api/duckduckgo/v1/search?q=python",
            headers=auth_headers(),
        )
        second = self.client.get(
            "/api/duckduckgo/v1/search?q=python",
            headers=auth_headers(),
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.ddgs.assert_called_once()

    def test_unauthenticated_requests_are_denied(self):
        response = self.client.get("/api/duckduckgo/v1/search?q=python")

        self.assertEqual(response.status_code, 401)
        self.ddgs.assert_not_called()

    def test_dedicated_ui_page_is_authenticated_and_linked_from_home(self):
        response = self.client.get("/duckduckgo")
        self.assertEqual(response.status_code, 302)

        response = self.client.get("/duckduckgo", headers=auth_headers())
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Safe search", response.data)
        self.assertIn(b"duckduckgo.js", response.data)
        self.assertIn(b"/api/duckduckgo/v1/search", response.data)

        home = self.client.get("/", headers=auth_headers())
        self.assertIn(b"DuckDuckGo Web Search", home.data)
        self.assertIn(b"/duckduckgo", home.data)

    def test_existing_api_urls_remain_registered(self):
        response = self.client.get(
            "/api/duckduckgo/v1/search?q=python",
            headers=auth_headers(),
        )
        self.assertEqual(response.status_code, 200)

    def test_disabled_service_is_not_instantiated_or_registered(self):
        app = make_app(duckduckgo_enabled=False)
        self.addCleanup(app.extensions["database"].dispose)
        client = app.test_client()

        self.assertNotIn("duckduckgo", app.extensions["mirror"].services)
        response = client.get(
            "/api/duckduckgo/v1/search?q=python",
            headers=auth_headers(),
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(client.get("/duckduckgo").status_code, 404)

    def test_feature_flag_loads_from_environment_and_defaults_off(self):
        self.assertFalse(Config(env="testing").duckduckgo_enabled)
        with patch.dict(os.environ, {"DUCKDUCKGO_ENABLED": "yes"}):
            self.assertTrue(Config.from_env().duckduckgo_enabled)

    def test_diagnostics_identifies_unofficial_direct_integration(self):
        response = self.client.get(
            "/api/duckduckgo/v1/diagnostics",
            headers=auth_headers(),
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["backend"], "duckduckgo")
        self.assertEqual(payload["integration"], "unofficial")
        self.assertTrue(payload["enabled"])


if __name__ == "__main__":
    unittest.main()
