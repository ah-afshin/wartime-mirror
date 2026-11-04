# Wartime Mirror

Small Flask app that relays requests to useful services from a server outside Iran.
It uses default-deny authentication: browser sessions or the configured Bearer token
are required for private pages and APIs.

## Endpoints

- `GET /healthz` — public health check.
- `GET /api/services` — authenticated list of enabled services.
- `GET /api/diagnostics/outbound` — authenticated outbound connectivity diagnostics.
- `GET /diagnostics` — diagnostics page for logged-in browser sessions.
- `POST /api/ai/v1/chat/completions` — authenticated, non-streaming OpenAI-compatible chat completion.
- `GET /api/ai/v1/models` — authenticated model discovery for configured AI providers.

The AI API is server-side only. It never accepts a client-provided upstream URL.
Responses use an OpenAI-style completion body and sanitized JSON errors. Streaming,
tool/function calls, and image/audio inputs are not supported in this first version.

## Authentication

Configure at least one of:

- `ACCESS_PASSWORD_HASH` for the browser login, generated with `python tools/make_hash.py`.
- `API_TOKEN` for API clients, sent as `Authorization: Bearer <API_TOKEN>`.

The AI routes require authentication. Do not send provider keys from clients; keys are
configured on the server and are never included in gateway responses.

## Layout

- `app/core/` — shared HTTP client, cache, security, errors, and rate limiting.
- `app/services/` — feature logic, including the AI provider adapters.
- `app/routes/` — matching Flask blueprints for each service.
- `app/auth.py` — browser login, CSRF, Bearer token, and default-deny access control.
- `app/registry.py` — service and route auto-discovery.

## Adding a service

1. Add `app/services/<name>.py` with a `Service` subclass.
2. Optionally add `app/routes/<name>.py` with a Flask blueprint named `bp`.
3. Use `/api/<name>/...` for JSON endpoints and register pages as needed.

## AI provider configuration and models

Set one or more server environment variables:

- `GROQ_API_KEY`
- `OPENROUTER_API_KEY`
- `CEREBRAS_API_KEY`
- `MISTRAL_API_KEY`
- `GEMINI_API_KEY`

The gateway routes only to a provider with a non-empty key. Use explicit model names:
`groq/<provider-model-id>`, `openrouter/<provider-model-id>`,
`cerebras/<provider-model-id>`, `mistral/<provider-model-id>`, or
`gemini/<provider-model-id>`. Model identifiers belong to the named provider and are
not interchangeable. `GET /api/ai/v1/models` queries each configured provider's
official model-list API; it lists only models returned by those APIs. Discovery errors
are reported per provider without exposing upstream error bodies.

Supported chat fields include `model`, text `messages` with `system`, `user`, or
`assistant` roles, `temperature`, `top_p`, `max_tokens` (or
`max_completion_tokens`), `stop`, `frequency_penalty`, `presence_penalty`, and `seed`.
Requests are limited to 100 messages and 100,000 text characters; HTTP request size is
capped at 1 MB. The gateway limits AI API usage to 60 requests per minute per client IP
in each application process.

## Python client

```python
from openai import OpenAI

client = OpenAI(
    base_url="https://YOUR-DOMAIN/api/ai/v1",
    api_key="YOUR_API_TOKEN",
)

response = client.chat.completions.create(
    model="groq/PROVIDER_MODEL_ID",
    messages=[{"role": "user", "content": "Hello"}],
    max_tokens=128,
)
print(response.choices[0].message.content)
```

The OpenAI Python package is a client-side example; it is not an application
dependency. Use a model identifier returned by the authenticated models endpoint.

## Local setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python tools/make_hash.py
Copy-Item .env.example .env
flask --app wsgi run --debug
python -m unittest discover -v
```

On macOS/Linux, activate with `source .venv/bin/activate` and copy the sample
environment file with `cp .env.example .env`.

Set `APP_ENV=development`, `SECRET_KEY`, `ACCESS_PASSWORD_HASH` and/or `API_TOKEN`,
and provider keys in `.env`. Never commit `.env` or real credentials. `AI_TIMEOUT`
defaults to 60 seconds and `AI_MAX_RESPONSE_BYTES` to 5,000,000.

## PythonAnywhere deployment

1. Upload or clone the repository into your PythonAnywhere account.
2. Create and activate a virtualenv using the Python version selected for the web app;
   install dependencies with `pip install -r requirements.txt`.
3. Generate an access password hash with `python tools/make_hash.py`.
4. In the **Web** tab, create or configure the Flask WSGI application and set its
   virtualenv path.
5. In the WSGI configuration file, set the project path and required environment
   variables before importing the app. For example, replace the placeholders and add
   the provider keys you actually use:

   ```python
   import os
   import sys

   project_home = "/home/yourusername/wartime-mirror"
   if project_home not in sys.path:
       sys.path.insert(0, project_home)

   os.environ["APP_ENV"] = "production"
   os.environ["SECRET_KEY"] = "replace-with-a-random-value-of-at-least-32-characters"
   os.environ["ACCESS_PASSWORD_HASH"] = "replace-with-generated-password-hash"
   os.environ["API_TOKEN"] = "replace-with-a-long-random-api-token"
   os.environ["GROQ_API_KEY"] = "optional-provider-key"

   from wsgi import application
   ```

   Store those values only in PythonAnywhere's private WSGI/environment settings, not
   in this repository. Leave unused provider key variables unset. Set `AI_TIMEOUT` or
   `AI_MAX_RESPONSE_BYTES` there as well if the defaults are unsuitable.
7. Save, reload the web app, log in or authenticate with the Bearer token, then request
   `/api/ai/v1/models` to verify provider discovery. PythonAnywhere plan/network
   restrictions may prevent outbound connections to one or more official provider
   endpoints.

## Known limitations

- Streaming is explicitly unsupported.
- Text-only chat messages and the `system`, `user`, and `assistant` roles are supported;
  tool/function calls and multimodal content are not.
- Model availability depends on each provider's model-list endpoint and account access.
- The rate limiter and model-discovery cache are in-memory and per process; rate limits
  are not shared across multiple workers or instances.
- No live provider calls are performed by the test suite. Real API credentials, model
  access, billing, and deployment-network access must be verified by the operator.
