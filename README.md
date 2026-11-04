# Wartime Mirror

Small Flask app that relays requests to useful services from a server outside Iran.
It uses default-deny authentication: per-user browser sessions or the configured `API_TOKEN`
are required for private pages and APIs.

## Endpoints

- `GET /healthz` — public health check.
- `GET /api/services` — authenticated list of enabled services.
- `GET /api/diagnostics/outbound` — authenticated outbound connectivity diagnostics.
- `GET /diagnostics` — diagnostics page for logged-in browser sessions.
- `POST /api/ai/v1/chat/completions` — authenticated OpenAI-compatible chat completion, streamed or non-streamed.
- `GET /api/ai/v1/models` — authenticated model discovery for configured AI providers.

The AI API is server-side only. It never accepts a client-provided upstream URL.
Non-streaming responses use an OpenAI-style completion body. Streaming requests return
Server-Sent Events (`text/event-stream`) with OpenAI-compatible completion chunks and
terminate with `data: [DONE]`. Tool/function calls and image/audio inputs are not
supported.

## Authentication

Browser users sign in with a username and password stored in the `users` table.
Passwords are stored as Werkzeug password hashes. Public registration is disabled;
create accounts from the authorized server CLI:

```bash
flask --app wsgi create-user
```

The command prompts for a username and hidden password confirmation. Usernames are
trimmed, Unicode-normalized, and case-insensitive. Run `alembic upgrade head` before
creating the first account. Additional accounts are provisioned with the same command.

`API_TOKEN` remains the independent shared credential for external API clients, sent as
`Authorization: Bearer <token>`. It does not create or identify browser users.

The AI routes require authentication. Do not send provider keys from clients; keys are
configured on the server and are never included in gateway responses.

## Layout

- `app/core/` — shared HTTP client, cache, security, errors, and rate limiting.
- `app/services/` — feature logic, including the AI provider adapters.
- `app/routes/` — matching Flask blueprints for each service.
- `app/auth.py` — per-user browser login, CSRF, API bearer authentication, and default-deny access control.
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
Streaming additionally supports `stream` and `stream_options.include_usage`. Requests
are limited to 100 messages and 100,000 text characters; HTTP request size is capped at
1 MB. The gateway limits AI API usage to 60 requests per minute per client IP in each
application process.

All four OpenAI-compatible providers (Groq, OpenRouter, Cerebras, and Mistral) receive
`stream=true`; their SSE chunks are normalized without collecting the completion.
Usage is forwarded when returned by the provider. Request it with
`stream_options: {"include_usage": true}` when supported by the selected provider/model.
Gemini uses its native `streamGenerateContent` API with `alt=sse`; candidate text,
finish reasons, and usage metadata are translated into completion chunks. Gemini does
not send OpenAI's `[DONE]` marker, so the gateway emits it when the native stream ends.
Provider errors or disconnects after SSE headers are sent are delivered as an SSE error
event followed by `[DONE]`; failures before streaming starts use the normal JSON error.

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

with client.chat.completions.create(
    model="groq/PROVIDER_MODEL_ID",
    messages=[{"role": "user", "content": "Hello"}],
    stream=True,
) as stream:
    for chunk in stream:
        text = chunk.choices[0].delta.content
        if text:
            print(text, end="", flush=True)
```

The OpenAI Python package is a client-side example; it is not an application
dependency. Use a model identifier returned by the authenticated models endpoint.

## Local setup

The application uses SQLAlchemy with Alembic migrations. The default SQLite
database is stored in the repository's persistent `instance/` directory; set
`DATABASE_URL` to use another database location. Apply schema changes before
starting the application with `alembic upgrade head`.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
alembic upgrade head
flask --app wsgi create-user
flask --app wsgi run --debug
python -m unittest discover -v
```

On macOS/Linux, activate with `source .venv/bin/activate` and copy the sample
environment file with `cp .env.example .env`.

Set `APP_ENV=development`, `SECRET_KEY`, optional `API_TOKEN`, and provider keys in
`.env`. Never commit `.env` or real credentials. `AI_TIMEOUT`
defaults to 60 seconds and `AI_MAX_RESPONSE_BYTES` to 5,000,000. Streaming defaults
to a 240-second generation limit and a 30-second provider idle timeout
(`AI_STREAM_TIMEOUT` and `AI_STREAM_IDLE_TIMEOUT`).
The generation deadline is checked between upstream reads; the idle timeout bounds a
stalled read.

## PythonAnywhere deployment

1. Upload or clone the repository into your PythonAnywhere account.
2. Create and activate a virtualenv using the Python version selected for the web app;
   install dependencies with `pip install -r requirements.txt`.
3. In the **Web** tab, create or configure the Flask WSGI application and set its
   virtualenv path.
4. In the WSGI configuration file, set the project path and required environment
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
   os.environ["API_TOKEN"] = "replace-with-a-long-random-api-token"
   os.environ["DATABASE_URL"] = "sqlite:////home/yourusername/.local/share/wartime-mirror.sqlite3"
   os.environ["GROQ_API_KEY"] = "optional-provider-key"

   from wsgi import application
   ```

   Store those values only in PythonAnywhere's private WSGI/environment settings, not
   in this repository. Leave unused provider key variables unset. Set `AI_TIMEOUT` or
   `AI_MAX_RESPONSE_BYTES` there as well if the defaults are unsuitable.
   Database migrations: run `alembic upgrade head` from the project directory with the same virtualenv
   and `DATABASE_URL` configured. Keep the SQLite database on persistent storage
   outside temporary or deployment-replaced directories; the parent directory is
   created automatically.
5. In a PythonAnywhere **Bash console**, activate the same virtualenv and make the
   production `SECRET_KEY` and exact `DATABASE_URL` from the WSGI configuration
   available to the command (for example, source a private shell-only environment
   file kept outside the repository). Then run this exact command from the project
   directory:

   ```bash
   flask --app wsgi create-user
   ```

   Enter the owner's existing browser password if it is known and desired, or choose
   a new one. The password is prompted interactively and is not a command-line
   argument. Repeat the command to provision additional users. Old shared-password
   sessions are not migrated and must sign in again.
6. After provisioning the first account, remove the obsolete
   `ACCESS_PASSWORD_HASH` setting from the WSGI configuration or `.env`. The application no
   longer reads it. Keep `API_TOKEN` if external API clients use it; it remains
   independent of browser accounts.
7. For streaming, optionally set `AI_STREAM_TIMEOUT` (default 240 seconds) and
   `AI_STREAM_IDLE_TIMEOUT` (default 30 seconds) in the WSGI environment. Keep the
   stream lifetime below PythonAnywhere's documented web-worker timeout.
8. Save and reload the web app, then verify the new username/password login. Log in
   with the new account and request `/api/ai/v1/models` to verify provider discovery.
   PythonAnywhere plan/network
   restrictions may prevent outbound connections to one or more official provider
   endpoints.

## PythonAnywhere streaming verification

Streaming is a synchronous WSGI response and occupies a web worker until it completes,
times out, or disconnects. PythonAnywhere [documents a five-minute web-worker
timeout](https://help.pythonanywhere.com/pages/AsyncInWebApps/), but this project has
not been deployed and tested there; SSE delivery, buffering, and client-disconnect
behavior on PythonAnywhere remain unverified. Configure `AI_STREAM_TIMEOUT` below the
platform worker timeout and `AI_STREAM_IDLE_TIMEOUT` for the longest acceptable wait
between provider bytes. After deployment, test with an authenticated client:

```bash
curl -N https://YOUR-DOMAIN/api/ai/v1/chat/completions \
  -H "Authorization: Bearer YOUR_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"model":"groq/PROVIDER_MODEL_ID","messages":[{"role":"user","content":"Hello"}],"stream":true}'
```

Confirm chunks arrive incrementally and test client disconnect behavior on the actual
deployment. Local tests do not establish PythonAnywhere streaming support.

## Known limitations

- Streaming is implemented for the four OpenAI-compatible providers and Gemini,
  subject to provider/model support and deployment-server behavior.
- Text-only chat messages and the `system`, `user`, and `assistant` roles are supported;
  tool/function calls and multimodal content are not.
- Model availability depends on each provider's model-list endpoint and account access.
- The rate limiter and model-discovery cache are in-memory and per process; rate limits
  are not shared across multiple workers or instances.
- Long-lived streams occupy a synchronous web worker until completion, timeout, or
  disconnect. Reverse-proxy buffering and PythonAnywhere SSE compatibility are not
  verified here.
- No live provider calls are performed by the test suite. Real API credentials, model
  access, billing, and deployment-network access must be verified by the operator.
