import json
import os
import unittest
from unittest.mock import patch

import requests
from werkzeug.security import generate_password_hash

from app import create_app
from app.config import Config
from app.core.ratelimit import RequestLimiter

TOKEN = "ai-test-token"
KEYS = {
    "groq": ("GROQ_API_KEY", "groq-secret"),
    "openrouter": ("OPENROUTER_API_KEY", "openrouter-secret"),
    "cerebras": ("CEREBRAS_API_KEY", "cerebras-secret"),
    "mistral": ("MISTRAL_API_KEY", "mistral-secret"),
    "gemini": ("GEMINI_API_KEY", "gemini-secret"),
}
BASE_URLS = {
    "groq": "https://api.groq.com/openai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "cerebras": "https://api.cerebras.ai/v1",
    "mistral": "https://api.mistral.ai/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta",
}


def make_app():
    config = Config(env="testing", secret_key="s" * 40,
                    password_hash=generate_password_hash("password"), api_token=TOKEN)
    app = create_app(config)
    app.testing = True
    return app


def response(status, payload):
    result = requests.Response()
    result.status_code = status
    result._content = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    result._content_consumed = True
    result.headers["Content-Type"] = "application/json"
    return result


def completion_response():
    return {
        "id": "chatcmpl-upstream",
        "object": "chat.completion",
        "created": 123,
        "model": "provider-model",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "Hello"},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
    }


def auth_headers():
    return {"Authorization": f"Bearer {TOKEN}"}


def chat_payload(model):
    return {
        "model": model,
        "messages": [{"role": "user", "content": "Hi"}],
        "temperature": 0.4,
        "top_p": 0.8,
        "max_tokens": 32,
    }


class AIAuthTests(unittest.TestCase):
    def test_api_routes_return_json_401_without_authentication(self):
        client = make_app().test_client()
        post = client.post("/api/ai/v1/chat/completions", json=chat_payload("groq/model"))
        get = client.get("/api/ai/v1/models")
        self.assertEqual(post.status_code, 401)
        self.assertEqual(get.status_code, 401)
        self.assertEqual(post.get_json()["status"], 401)
        self.assertEqual(get.get_json()["status"], 401)

    def test_ai_timeout_configuration_must_be_finite_and_positive(self):
        for timeout in (0, -1, float("inf"), float("nan")):
            with self.subTest(timeout=timeout):
                config = Config(env="testing", secret_key="s" * 40, api_token=TOKEN,
                                ai_timeout=timeout)
                with self.assertRaises(RuntimeError):
                    config.validate()


class AICompletionTests(unittest.TestCase):
    def test_openai_compatible_adapters_use_fixed_urls_and_map_requests(self):
        for provider in ("groq", "openrouter", "cerebras", "mistral"):
            with self.subTest(provider=provider):
                env_name, key = KEYS[provider]
                app = make_app()
                calls = []

                def fake_request(method, url, **kwargs):
                    calls.append((method, url, kwargs))
                    return response(200, completion_response())

                with patch.dict(os.environ, {env_name: key}), \
                        patch.object(requests.Session, "request", side_effect=fake_request):
                    result = app.test_client().post(
                        "/api/ai/v1/chat/completions",
                        headers=auth_headers(), json=chat_payload(f"{provider}/vendor/model-x"),
                    )

                self.assertEqual(result.status_code, 200)
                self.assertEqual(result.get_json()["object"], "chat.completion")
                self.assertEqual(result.get_json()["model"], f"{provider}/vendor/model-x")
                self.assertEqual(result.get_json()["choices"][0]["message"]["content"], "Hello")
                self.assertEqual(len(calls), 1)
                method, url, kwargs = calls[0]
                self.assertEqual(method, "POST")
                self.assertEqual(url, f"{BASE_URLS[provider]}/chat/completions")
                self.assertEqual(kwargs["headers"]["Authorization"], f"Bearer {key}")
                self.assertEqual(kwargs["json"], {
                    "model": "vendor/model-x",
                    "messages": [{"role": "user", "content": "Hi"}],
                    "temperature": 0.4, "top_p": 0.8, "max_tokens": 32,
                })
                self.assertNotIn(key, result.get_data(as_text=True))

    def test_gemini_native_request_and_response_mapping(self):
        app = make_app()
        key = KEYS["gemini"][1]
        payload = {
            "model": "gemini/gemini-2.5-flash",
            "messages": [{"role": "system", "content": "Be concise"},
                         {"role": "user", "content": [{"type": "text", "text": "Hi"}]}],
            "temperature": 0.2, "top_p": 0.9, "max_completion_tokens": 24,
        }
        calls = []
        upstream = {
            "candidates": [{"content": {"parts": [{"text": "Hello"}]}, "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 2, "candidatesTokenCount": 1, "totalTokenCount": 3},
        }

        def fake_request(method, url, **kwargs):
            calls.append((method, url, kwargs))
            return response(200, upstream)

        with patch.dict(os.environ, {"GEMINI_API_KEY": key}), \
                patch.object(requests.Session, "request", side_effect=fake_request):
            result = app.test_client().post(
                "/api/ai/v1/chat/completions", headers=auth_headers(), json=payload,
            )
        self.assertEqual(result.status_code, 200)
        output = result.get_json()
        self.assertEqual(output["choices"][0]["message"]["content"], "Hello")
        self.assertEqual(output["usage"]["total_tokens"], 3)
        method, url, kwargs = calls[0]
        self.assertEqual(method, "POST")
        self.assertEqual(url, f"{BASE_URLS['gemini']}/models/gemini-2.5-flash:generateContent")
        self.assertEqual(kwargs["headers"]["x-goog-api-key"], key)
        self.assertNotIn("Authorization", kwargs["headers"])
        self.assertEqual(kwargs["json"], {
            "contents": [{"role": "user", "parts": [{"text": "Hi"}]}],
            "system_instruction": {"parts": [{"text": "Be concise"}]},
            "generationConfig": {"temperature": 0.2, "topP": 0.9, "maxOutputTokens": 24},
        })
        self.assertNotIn(key, result.get_data(as_text=True))

    def test_missing_key_unknown_provider_and_invalid_model(self):
        app = make_app()
        client = app.test_client()
        with patch.dict(os.environ, {"GROQ_API_KEY": ""}):
            missing = client.post("/api/ai/v1/chat/completions", headers=auth_headers(),
                                  json=chat_payload("groq/model"))
        self.assertEqual(missing.status_code, 503)
        self.assertEqual(missing.get_json()["error"]["code"], "missing_api_key")
        unknown = client.post("/api/ai/v1/chat/completions", headers=auth_headers(),
                              json=chat_payload("unlisted/model"))
        self.assertEqual(unknown.status_code, 400)
        self.assertEqual(unknown.get_json()["error"]["code"], "unknown_provider")
        invalid = client.post("/api/ai/v1/chat/completions", headers=auth_headers(),
                              json=chat_payload("groq/not a model"))
        self.assertEqual(invalid.status_code, 400)

    def test_invalid_request_and_streaming_rejected_without_upstream_call(self):
        app = make_app()
        client = app.test_client()
        invalid = chat_payload("groq/model")
        invalid["messages"] = [{"role": [], "content": "Hi"}]
        with patch.object(requests.Session, "request") as mocked:
            result = client.post("/api/ai/v1/chat/completions", headers=auth_headers(), json=invalid)
            self.assertEqual(result.status_code, 400)
            self.assertEqual(client.post(
                "/api/ai/v1/chat/completions", headers=auth_headers(),
                json={**chat_payload("groq/model"), "stream": True},
            ).status_code, 400)
            mocked.assert_not_called()

    def test_unknown_model_and_upstream_status_errors_are_sanitized(self):
        app = make_app()
        key = KEYS["groq"][1]
        cases = ((404, 404, "unknown_model"), (400, 404, "unknown_model"),
                 (401, 502, "upstream_authentication_failed"),
                 (429, 429, "upstream_rate_limit"), (504, 504, "upstream_timeout"))
        for upstream_status, expected_status, expected_code in cases:
            with self.subTest(status=upstream_status):
                upstream_body = {"error": {"message": "model not found", "code": "model_not_found"}}
                with patch.dict(os.environ, {"GROQ_API_KEY": key}), \
                        patch.object(requests.Session, "request",
                                     return_value=response(
                                         upstream_status,
                                         upstream_body if upstream_status == 400 else {"error": key},
                                     )):
                    result = app.test_client().post(
                        "/api/ai/v1/chat/completions", headers=auth_headers(),
                        json=chat_payload("groq/model"),
                    )
                self.assertEqual(result.status_code, expected_status)
                self.assertEqual(result.get_json()["error"]["code"], expected_code)
                self.assertNotIn(key, result.get_data(as_text=True))

    def test_malformed_provider_response_is_sanitized(self):
        app = make_app()
        with patch.dict(os.environ, {"MISTRAL_API_KEY": KEYS["mistral"][1]}), \
                patch.object(requests.Session, "request", return_value=response(200, b"not-json")):
            result = app.test_client().post(
                "/api/ai/v1/chat/completions", headers=auth_headers(),
                json=chat_payload("mistral/model"),
            )
        self.assertEqual(result.status_code, 502)
        self.assertEqual(result.get_json()["error"]["code"], "malformed_upstream_response")

    def test_network_timeout_is_sanitized(self):
        app = make_app()
        with patch.dict(os.environ, {"GROQ_API_KEY": KEYS["groq"][1]}), \
                patch.object(requests.Session, "request", side_effect=requests.Timeout):
            result = app.test_client().post(
                "/api/ai/v1/chat/completions", headers=auth_headers(),
                json=chat_payload("groq/model"),
            )
        self.assertEqual(result.status_code, 504)
        self.assertEqual(result.get_json()["error"]["code"], "upstream_timeout")

    def test_upstream_redirects_are_not_followed(self):
        app = make_app()
        calls = []

        def fake_request(_method, url, **kwargs):
            calls.append((url, kwargs))
            result = response(302, {"location": "https://attacker.example/"})
            result.headers["Location"] = "https://attacker.example/"
            return result

        with patch.dict(os.environ, {"GROQ_API_KEY": KEYS["groq"][1]}), \
                patch.object(requests.Session, "request", side_effect=fake_request):
            result = app.test_client().post(
                "/api/ai/v1/chat/completions", headers=auth_headers(),
                json=chat_payload("groq/model"),
            )
        self.assertEqual(result.status_code, 503)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], f"{BASE_URLS['groq']}/chat/completions")
        self.assertFalse(calls[0][1]["allow_redirects"])


class AIModelDiscoveryTests(unittest.TestCase):
    def test_models_use_provider_model_list_endpoints_and_hide_keys(self):
        app = make_app()
        env = {env_name: key for env_name, key in KEYS.values()}
        calls = []

        def fake_request(method, url, **kwargs):
            calls.append((method, url, kwargs))
            if url == f"{BASE_URLS['gemini']}/models":
                body = {"models": [
                    {"name": "models/gemini-verified", "supportedGenerationMethods": ["generateContent"]},
                    {"name": "models/gemini-embedding", "supportedGenerationMethods": ["embedContent"]},
                ]}
            else:
                body = {"data": [{"id": f"{url.split('/')[2]}-verified"}]}
            return response(200, body)

        with patch.dict(os.environ, env), \
                patch.object(requests.Session, "request", side_effect=fake_request):
            result = app.test_client().get("/api/ai/v1/models", headers=auth_headers())
        self.assertEqual(result.status_code, 200)
        data = result.get_json()
        self.assertCountEqual(
            [item["id"] for item in data["data"]],
            ["groq/api.groq.com-verified", "openrouter/openrouter.ai-verified",
             "cerebras/api.cerebras.ai-verified", "mistral/api.mistral.ai-verified",
             "gemini/gemini-verified"],
        )
        self.assertEqual(len(calls), 5)
        for method, url, kwargs in calls:
            self.assertEqual(method, "GET")
            provider = next(name for name, base in BASE_URLS.items() if url.startswith(base))
            key = KEYS[provider][1]
            expected_header = "x-goog-api-key" if provider == "gemini" else "Authorization"
            expected_value = key if provider == "gemini" else f"Bearer {key}"
            self.assertEqual(kwargs["headers"][expected_header], expected_value)
        body = result.get_data(as_text=True)
        for _, key in KEYS.values():
            self.assertNotIn(key, body)
        self.assertTrue(all(item["configured"] for item in data["providers"]))

    def test_model_discovery_reports_unavailable_provider_without_body(self):
        app = make_app()
        key = KEYS["groq"][1]
        with patch.dict(os.environ, {"GROQ_API_KEY": key}), \
                patch.object(requests.Session, "request",
                             return_value=response(503, {"error": key})):
            result = app.test_client().get("/api/ai/v1/models", headers=auth_headers())
        provider = next(item for item in result.get_json()["providers"] if item["id"] == "groq")
        self.assertFalse(provider["available"])
        self.assertEqual(provider["error"], "provider_unavailable")
        self.assertNotIn(key, result.get_data(as_text=True))


class RequestLimiterTests(unittest.TestCase):
    def test_sliding_window_limits_requests_per_key(self):
        limiter = RequestLimiter(max_requests=2, window=60)
        self.assertTrue(limiter.allow("client"))
        self.assertTrue(limiter.allow("client"))
        self.assertFalse(limiter.allow("client"))
        self.assertTrue(limiter.allow("another-client"))

    def test_gateway_returns_json_rate_limit_error(self):
        from app.routes import ai

        with patch.object(ai, "_limiter", RequestLimiter(max_requests=0, window=60)):
            result = make_app().test_client().post(
                "/api/ai/v1/chat/completions", headers=auth_headers(),
                json=chat_payload("groq/model"),
            )
        self.assertEqual(result.status_code, 429)
        self.assertEqual(result.get_json()["error"]["code"], "rate_limit_exceeded")


if __name__ == "__main__":
    unittest.main()
