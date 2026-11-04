"""Authenticated, provider-specific AI gateway adapters."""
from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from app.core.errors import ServiceError
from app.services.base import Service

OPENAI_PROVIDERS = {
    "groq": ("GROQ_API_KEY", "https://api.groq.com/openai/v1"),
    "openrouter": ("OPENROUTER_API_KEY", "https://openrouter.ai/api/v1"),
    "cerebras": ("CEREBRAS_API_KEY", "https://api.cerebras.ai/v1"),
    "mistral": ("MISTRAL_API_KEY", "https://api.mistral.ai/v1"),
}
GEMINI_PROVIDER = ("GEMINI_API_KEY", "https://generativelanguage.googleapis.com/v1beta")
MODEL_PATTERN = re.compile(r"^[A-Za-z0-9._:/-]{1,200}$")
MAX_MESSAGES = 100
MAX_MESSAGE_CHARS = 100_000


class AIError(Exception):
    def __init__(self, message: str, status: int, code: str, error_type: str = "gateway_error"):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code
        self.error_type = error_type


@dataclass(frozen=True)
class Provider:
    name: str
    key_env: str
    base_url: str


class ProviderAdapter:
    def __init__(self, service: "AIService", provider: Provider):
        self.service = service
        self.provider = provider

    def headers(self, key: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    def complete(self, _model: str, _payload: dict[str, Any], _key: str) -> dict[str, Any]:
        raise NotImplementedError

    def list_models(self, _key: str) -> list[str]:
        raise NotImplementedError

    def _request(self, method: str, url: str, *, key: str, payload: dict | None = None,
                 params: dict | None = None, timeout: float | None = None):
        try:
            return self.service.http.request(
                method, url, headers=self.headers(key), json=payload, params=params,
                timeout=timeout or self.service.config.ai_timeout,
                max_bytes=self.service.config.ai_max_response_bytes, retries=False,
                allow_redirects=False,
            )
        except ServiceError as exc:
            if exc.status == 504 or "Timeout" in (exc.detail or ""):
                raise AIError("The AI provider timed out", 504, "upstream_timeout", "timeout") from None
            raise AIError("The AI provider is unavailable", 503, "provider_unavailable", "server_error") from None

    def _check_status(self, response, *, discovery: bool = False) -> None:
        status = response.status_code
        if 200 <= status < 300:
            return
        if status in (401, 403):
            raise AIError("The AI provider rejected its configured credentials", 502,
                          "upstream_authentication_failed", "server_error")
        if status == 429:
            raise AIError("The AI provider rate limit was reached", 429, "upstream_rate_limit", "rate_limit_error")
        if status == 404 and not discovery:
            raise AIError("The requested model was not found", 404, "unknown_model", "invalid_request_error")
        if status in (408, 504):
            raise AIError("The AI provider timed out", 504, "upstream_timeout", "timeout")
        if status == 400:
            if not discovery:
                try:
                    body = response.json()
                    error = body.get("error", {}) if isinstance(body, dict) else {}
                    hints = " ".join(str(error.get(key, "")) for key in ("code", "type", "message")).lower()
                    if any(hint in hints for hint in (
                            "model_not_found", "model not found", "unknown model",
                            "model_decommissioned", "invalid_model", "does not exist")):
                        raise AIError("The requested model was not found", 404,
                                      "unknown_model", "invalid_request_error")
                except (ValueError, TypeError, AttributeError):
                    pass
            raise AIError("The AI provider rejected the request", 400, "invalid_request", "invalid_request_error")
        raise AIError("The AI provider is unavailable", 503, "provider_unavailable", "server_error")

    @staticmethod
    def _json(response) -> dict[str, Any]:
        try:
            data = response.json()
        except (ValueError, TypeError):
            raise AIError("The AI provider returned a malformed response", 502,
                          "malformed_upstream_response", "server_error") from None
        if not isinstance(data, dict):
            raise AIError("The AI provider returned a malformed response", 502,
                          "malformed_upstream_response", "server_error")
        return data


class OpenAICompatibleAdapter(ProviderAdapter):
    def complete(self, model: str, payload: dict[str, Any], key: str) -> dict[str, Any]:
        outgoing = {
            "model": model,
            "messages": payload["messages"],
        }
        for field in ("temperature", "top_p", "stop", "frequency_penalty", "presence_penalty", "seed"):
            if field in payload:
                outgoing[field] = payload[field]
        if "max_completion_tokens" in payload:
            outgoing["max_tokens"] = payload["max_completion_tokens"]
        elif "max_tokens" in payload:
            outgoing["max_tokens"] = payload["max_tokens"]
        response = self._request("POST", f"{self.provider.base_url}/chat/completions",
                                 key=key, payload=outgoing)
        self._check_status(response)
        data = self._json(response)
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise AIError("The AI provider returned a malformed response", 502,
                          "malformed_upstream_response", "server_error")
        normalized_choices = []
        for index, choice in enumerate(choices):
            if (not isinstance(choice, dict) or not isinstance(choice.get("message"), dict)
                    or choice["message"].get("role") != "assistant"
                    or choice["message"].get("content") is not None
                    and not isinstance(choice["message"].get("content"), str)):
                raise AIError("The AI provider returned a malformed response", 502,
                              "malformed_upstream_response", "server_error")
            choice_index = choice.get("index", index)
            finish_reason = choice.get("finish_reason")
            if (isinstance(choice_index, bool) or not isinstance(choice_index, int)
                    or finish_reason is not None and not isinstance(finish_reason, str)):
                raise AIError("The AI provider returned a malformed response", 502,
                              "malformed_upstream_response", "server_error")
            normalized_choices.append({
                "index": choice_index,
                "message": {"role": "assistant", "content": choice["message"].get("content")},
                "finish_reason": finish_reason,
            })
        data["choices"] = normalized_choices
        return self.service.openai_response(data, f"{self.provider.name}/{model}")

    def list_models(self, key: str) -> list[str]:
        response = self._request("GET", f"{self.provider.base_url}/models", key=key)
        self._check_status(response, discovery=True)
        data = self._json(response)
        models = data.get("data")
        if not isinstance(models, list):
            raise AIError("The AI provider returned a malformed model list", 502,
                          "malformed_upstream_response", "server_error")
        return sorted({item["id"] for item in models
                       if isinstance(item, dict) and isinstance(item.get("id"), str)
                       and MODEL_PATTERN.fullmatch(item["id"])})


class GeminiAdapter(ProviderAdapter):
    def headers(self, key: str) -> dict[str, str]:
        return {"x-goog-api-key": key, "Content-Type": "application/json"}

    def complete(self, model: str, payload: dict[str, Any], key: str) -> dict[str, Any]:
        contents = []
        system_parts = []
        for message in payload["messages"]:
            text = self.service.message_text(message["content"])
            if message["role"] == "system":
                system_parts.append({"text": text})
            else:
                role = "model" if message["role"] == "assistant" else "user"
                contents.append({"role": role, "parts": [{"text": text}]})
        if not any(item["role"] == "user" for item in contents):
            raise AIError("Gemini requests require at least one user message", 400,
                          "invalid_request", "invalid_request_error")
        outgoing: dict[str, Any] = {"contents": contents}
        if system_parts:
            outgoing["system_instruction"] = {"parts": system_parts}
        generation: dict[str, Any] = {}
        for source, target in (("temperature", "temperature"), ("top_p", "topP")):
            if source in payload:
                generation[target] = payload[source]
        if "max_completion_tokens" in payload:
            generation["maxOutputTokens"] = payload["max_completion_tokens"]
        elif "max_tokens" in payload:
            generation["maxOutputTokens"] = payload["max_tokens"]
        if generation:
            outgoing["generationConfig"] = generation
        endpoint = f"{self.provider.base_url}/models/{quote(model, safe='')}:generateContent"
        response = self._request("POST", endpoint, key=key, payload=outgoing)
        self._check_status(response)
        data = self._json(response)
        try:
            candidate = data["candidates"][0]
            parts = candidate["content"]["parts"]
            if not isinstance(parts, list) or not all(isinstance(part, dict) for part in parts):
                raise TypeError
            text = "".join(part["text"] for part in parts if isinstance(part.get("text"), str))
        except (KeyError, IndexError, TypeError):
            raise AIError("The AI provider returned a malformed response", 502,
                          "malformed_upstream_response", "server_error") from None
        usage = data.get("usageMetadata")
        usage = usage if isinstance(usage, dict) else {}
        finish_reason = candidate.get("finishReason", "stop")
        if not isinstance(finish_reason, str):
            raise AIError("The AI provider returned a malformed response", 502,
                          "malformed_upstream_response", "server_error")
        finish_map = {"MAX_TOKENS": "length", "STOP": "stop", "SAFETY": "content_filter"}
        usage_counts = []
        for field in ("promptTokenCount", "candidatesTokenCount", "totalTokenCount"):
            count = usage.get(field, 0)
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise AIError("The AI provider returned a malformed response", 502,
                              "malformed_upstream_response", "server_error")
            usage_counts.append(count)
        response_data = {
            "id": f"chatcmpl-{uuid.uuid4().hex}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": f"gemini/{model}",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": text},
                         "finish_reason": finish_map.get(finish_reason, "stop")}],
            "usage": dict(zip(("prompt_tokens", "completion_tokens", "total_tokens"), usage_counts)),
        }
        return response_data

    def list_models(self, key: str) -> list[str]:
        supported = []
        page_token = None
        for _ in range(20):
            params = {"pageSize": 1000}
            if page_token:
                params["pageToken"] = page_token
            response = self._request("GET", f"{self.provider.base_url}/models",
                                     key=key, params=params,
                                     timeout=min(self.service.config.ai_timeout, 10.0))
            self._check_status(response, discovery=True)
            data = self._json(response)
            models = data.get("models")
            if not isinstance(models, list):
                raise AIError("The AI provider returned a malformed model list", 502,
                              "malformed_upstream_response", "server_error")
            for item in models:
                if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                    continue
                methods = item.get("supportedGenerationMethods", [])
                name = item["name"].removeprefix("models/")
                if (isinstance(methods, list) and "generateContent" in methods
                        and MODEL_PATTERN.fullmatch(name) and "/" not in name):
                    supported.append(name)
            page_token = data.get("nextPageToken")
            if not isinstance(page_token, str) or not page_token:
                break
        return sorted(set(supported))


class AIService(Service):
    name = "ai"
    title = "AI API Gateway"
    description = "Authenticated OpenAI-compatible gateway for configured providers."

    def __init__(self, ctx):
        super().__init__(ctx)
        self.providers = {
            name: Provider(name, key_env, base_url)
            for name, (key_env, base_url) in OPENAI_PROVIDERS.items()
        }
        key_env, base_url = GEMINI_PROVIDER
        self.providers["gemini"] = Provider("gemini", key_env, base_url)
        self.adapters = {
            name: (GeminiAdapter(self, provider) if name == "gemini"
                   else OpenAICompatibleAdapter(self, provider))
            for name, provider in self.providers.items()
        }

    def get_key(self, provider: Provider) -> str:
        key = self.config.secret(provider.key_env)
        if not key:
            raise AIError(f"No API key is configured for {provider.name}", 503,
                          "missing_api_key", "server_error")
        return key

    @staticmethod
    def message_text(content: Any) -> str:
        if isinstance(content, str):
            return content
        return "".join(part["text"] for part in content)

    def validate_request(self, payload: Any) -> tuple[str, str]:
        if not isinstance(payload, dict):
            raise AIError("Request body must be a JSON object", 400, "invalid_request", "invalid_request_error")
        model = payload.get("model")
        if not isinstance(model, str) or "/" not in model:
            raise AIError("Model must use the provider/model naming convention", 400,
                          "unknown_provider", "invalid_request_error")
        provider_name, model_id = model.split("/", 1)
        if provider_name not in self.providers:
            raise AIError("The requested provider is not supported", 400,
                          "unknown_provider", "invalid_request_error")
        if (not MODEL_PATTERN.fullmatch(model_id) or
                provider_name == "gemini" and "/" in model_id):
            raise AIError("The model identifier is invalid", 400, "invalid_request", "invalid_request_error")
        if payload.get("stream", False):
            raise AIError("Streaming is not supported yet", 400, "unsupported_streaming", "invalid_request_error")
        messages = payload.get("messages")
        if not isinstance(messages, list) or not messages or len(messages) > MAX_MESSAGES:
            raise AIError("Messages must be a non-empty list of at most 100 items", 400,
                          "invalid_request", "invalid_request_error")
        normalized = []
        total_chars = 0
        for message in messages:
            if (not isinstance(message, dict) or not isinstance(message.get("role"), str)
                    or message["role"] not in {"system", "user", "assistant"}):
                raise AIError("Each message must have a supported role and content", 400,
                              "invalid_request", "invalid_request_error")
            content = message.get("content")
            if isinstance(content, str):
                text = content
            elif (isinstance(content, list) and content and all(
                    isinstance(part, dict) and part.get("type") == "text"
                    and isinstance(part.get("text"), str) for part in content)):
                text = self.message_text(content)
            else:
                raise AIError("Message content must be text or text parts", 400,
                              "invalid_request", "invalid_request_error")
            total_chars += len(text)
            normalized.append({"role": message["role"], "content": content})
        if total_chars > MAX_MESSAGE_CHARS:
            raise AIError("Message content is too large", 413, "request_too_large", "invalid_request_error")
        for field in ("temperature", "top_p", "frequency_penalty", "presence_penalty"):
            if field in payload:
                value = payload[field]
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise AIError(f"{field} must be a number", 400, "invalid_request", "invalid_request_error")
                if field == "temperature" and not 0 <= value <= 2:
                    raise AIError("temperature must be between 0 and 2", 400, "invalid_request", "invalid_request_error")
                if field == "top_p" and not 0 <= value <= 1:
                    raise AIError("top_p must be between 0 and 1", 400, "invalid_request", "invalid_request_error")
                if field in {"frequency_penalty", "presence_penalty"} and not -2 <= value <= 2:
                    raise AIError(f"{field} must be between -2 and 2", 400, "invalid_request", "invalid_request_error")
        for field in ("max_tokens", "max_completion_tokens"):
            if field in payload and (isinstance(payload[field], bool) or
                                     not isinstance(payload[field], int) or payload[field] < 1):
                raise AIError(f"{field} must be a positive integer", 400, "invalid_request", "invalid_request_error")
        if "stop" in payload and not (isinstance(payload["stop"], str) or
                                      isinstance(payload["stop"], list) and
                                      all(isinstance(item, str) for item in payload["stop"])):
            raise AIError("stop must be a string or list of strings", 400,
                          "invalid_request", "invalid_request_error")
        if "seed" in payload and (isinstance(payload["seed"], bool) or not isinstance(payload["seed"], int)):
            raise AIError("seed must be an integer", 400, "invalid_request", "invalid_request_error")
        supported = {"model", "messages", "stream", "temperature", "top_p", "max_tokens",
                     "max_completion_tokens", "stop", "frequency_penalty", "presence_penalty", "seed"}
        unsupported = set(payload) - supported
        if unsupported:
            field = sorted(unsupported)[0]
            raise AIError(f"Request field '{field}' is not supported", 400,
                          "unsupported_parameter", "invalid_request_error")
        payload["messages"] = normalized
        return provider_name, model_id

    @staticmethod
    def openai_response(data: dict[str, Any], model: str) -> dict[str, Any]:
        usage = data.get("usage")
        usage = usage if isinstance(usage, dict) else {}
        token_counts = []
        for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
            count = usage.get(field, 0)
            token_counts.append(count if isinstance(count, int) and not isinstance(count, bool)
                                and count >= 0 else 0)
        return {
            "id": data.get("id") if isinstance(data.get("id"), str) else f"chatcmpl-{uuid.uuid4().hex}",
            "object": "chat.completion",
            "created": data.get("created") if isinstance(data.get("created"), int)
            and not isinstance(data.get("created"), bool) else int(time.time()),
            "model": model,
            "choices": data["choices"],
            "usage": dict(zip(("prompt_tokens", "completion_tokens", "total_tokens"), token_counts)),
        }

    def complete(self, payload: Any) -> dict[str, Any]:
        provider_name, model_id = self.validate_request(payload)
        provider = self.providers[provider_name]
        adapter = self.adapters[provider_name]
        return adapter.complete(model_id, payload, self.get_key(provider))

    def models(self) -> dict[str, Any]:
        return self.cache.get_or_set("ai:models", self._discover_models, ttl=120)

    def _discover_models(self) -> dict[str, Any]:
        configured = []
        data = []
        for name, provider in self.providers.items():
            key = self.config.secret(provider.key_env)
            row: dict[str, Any] = {"id": name, "configured": bool(key), "available": False, "models": []}
            if key:
                try:
                    identifiers = self.adapters[name].list_models(key)
                    row.update(available=True, models=[f"{name}/{item}" for item in identifiers])
                    data.extend({"id": model, "object": "model", "owned_by": name}
                                for model in row["models"])
                except AIError:
                    row["error"] = "provider_unavailable"
            configured.append(row)
        return {"object": "list", "data": data, "providers": configured}
