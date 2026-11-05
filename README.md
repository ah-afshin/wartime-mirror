# Wartime Mirror

Small Flask app that relays requests to useful services from a server outside Iran.
It uses default-deny authentication: per-user browser sessions or the configured `API_TOKEN`
are required for private pages and APIs.

## Endpoints

- `GET /healthz` — public health check.
- `GET /api/services` — authenticated list of enabled services.
- `GET /api/diagnostics/outbound` — authenticated outbound connectivity diagnostics.
- `GET /api/duckduckgo/v1/search?q=python&limit=5` — authenticated DuckDuckGo web search.
- `GET /api/duckduckgo/v1/diagnostics` — authenticated DuckDuckGo integration diagnostics.
- `GET /duckduckgo` — authenticated DuckDuckGo search page.
- `GET /api/google-search/v1/search?q=python&limit=5` — authenticated Google web search.
- `GET /api/google-search/v1/diagnostics` — authenticated Google Search configuration and quota diagnostics.
- `GET /google-search` — authenticated Google Search browser page.
- `GET /api/wikipedia/v1/search?q=neural+networks&lang=en&limit=5` — authenticated Wikipedia article search.
- `GET /api/wikipedia/v1/page?title=Neural+network&lang=en` — authenticated article metadata and plain-text extract.
- `GET /api/wikipedia/v1/diagnostics` — authenticated Wikipedia API connectivity check.
- `GET /wikipedia` — authenticated Wikipedia search and article page.
- `GET /diagnostics` — diagnostics page for logged-in browser sessions.
- `GET /chat` — authenticated browser chat interface.
- `GET /api/ai/v1/conversations` — list the logged-in browser user's conversations.
- `POST /api/ai/v1/conversations` — create a conversation.
- `GET`, `PATCH`, `DELETE /api/ai/v1/conversations/<id>` — retrieve, update, or delete an owned conversation.
- `POST /api/ai/v1/conversations/<id>/messages` — save a user message and generate a non-streaming assistant response.
- `POST /api/ai/v1/conversations/<id>/messages/stream` — save a user message and stream a persistent assistant response over SSE.
- `POST /api/ai/v1/chat/completions` — authenticated OpenAI-compatible chat completion, streamed or non-streamed.
- `GET /api/ai/v1/models` — authenticated model discovery for configured AI providers.

The AI API is server-side only. It never accepts a client-provided upstream URL.
Non-streaming responses use an OpenAI-style completion body. Streaming requests return
Server-Sent Events (`text/event-stream`) with OpenAI-compatible completion chunks and
terminate with `data: [DONE]`. Tool/function calls and image/audio inputs are not
supported.

## Wikipedia API

The independent Wikipedia service uses the official MediaWiki API and requires no API
key. It is disabled by default; set `WIKIPEDIA_ENABLED=1` to enable it. If
`ENABLED_SERVICES` is non-empty, it must also include `wikipedia`.

Search accepts `q` (required, up to 300 characters), `lang` (defaults to `en`), and
`limit` (defaults to 5; allowed range 1–20). Page retrieval accepts `title` (required,
up to 300 characters) and `lang`. Language codes are lowercase MediaWiki project
codes, including `en` and `fa` for English and Persian. Both endpoints return
deterministic JSON; search snippets are plain text, and page results include a plain
text article extract, canonical URL, page ID, and available page metadata. Search
responses have the shape `{"query":"...","language":"en","count":1,"results":[...]}`;
page responses have the shape
`{"page":{"pageid":1,"title":"...","language":"en","url":"...","extract":"..."}}`.
No search results produce an empty `results` list. Missing pages return HTTP 404;
invalid parameters return HTTP 400; upstream and malformed-response errors return
HTTP 502, upstream timeouts return HTTP 504, and more than 60 requests per minute
per client IP return HTTP 429. The service caches successful
responses using the shared in-memory TTL cache. Requests require the same browser
session or Bearer API token as the rest of the private API.

For PythonAnywhere deployments, ensure outbound access is available to the selected
Wikipedia language subdomain (for example, `en.wikipedia.org` or `fa.wikipedia.org`).
The service uses the shared HTTP client and does not require an additional package.
The authenticated `/wikipedia` page provides an English/Persian search form, lets you
select an article from the results, and displays its plain-text extract, metadata, and
canonical Wikipedia link. It calls only the independent Wikipedia API described above.

## DuckDuckGo Search API

The independent DuckDuckGo service uses the unofficial `ddgs` Python package, not an
official DuckDuckGo search API, and requires no API key. It is disabled by default;
set `DUCKDUCKGO_ENABLED=1` to enable it. If `ENABLED_SERVICES` is non-empty, it must
also include `duckduckgo`. The service always explicitly selects the `duckduckgo`
text backend; it does not use `ddgs` automatic multi-engine mode or fall back to
other search providers.

`GET /api/duckduckgo/v1/search` accepts `q` (required, up to 500 characters), `limit`
(defaults to 5; allowed range 1–20), `region` (defaults to `us-en`), optional `lang`
(two- or three-letter language code, replacing the region's language suffix), and
`safesearch` (`on`, `moderate`, or `off`; defaults to `moderate`). Region values use
the `country-language` format supported by `ddgs`, for example `us-en` or `uk-en`.
The response contains the original query, selected region and safe-search setting,
provider source, result count, and results with `title`, `url`, `snippet`, and
`source`. An empty result set returns HTTP 200 with `"count": 0` and `"results": []`.
Invalid parameters return HTTP 400; provider rate limits return HTTP 429; timeouts
return HTTP 504; other provider failures return HTTP 502. The endpoint also limits
each client IP to 30 requests per minute and caches successful responses. Diagnostics
identify the package version, selected backend, and unofficial integration status;
they do not guarantee current upstream availability.

`ddgs` 9.16.1 supports Python 3.10–3.14. It is an unofficial integration that may be
rate-limited or disrupted by changes to DuckDuckGo; search availability is not
guaranteed. Ensure deployment hosts can reach the provider.
The authenticated `/duckduckgo` page provides query, result-count, country, language,
and safe-search controls, then displays results from only the DuckDuckGo service API.
It does not submit searches to another provider.

## Google Search API

The independent Google Search service uses SerpApi's third-party Google results
engine, not an official Google API. It does not aggregate providers or fall back to
another search engine. It is disabled by default; set `GOOGLE_SEARCH_ENABLED=1` and
provide `SERPAPI_API_KEY` from a server-side **free-tier-only** SerpApi account. If
`ENABLED_SERVICES` is non-empty, include `google_search` too. Keep the key secret;
it is never returned by the API or diagnostics. Do not activate a paid plan,
overages, or automatic renewal.

The authenticated `/google-search` page is a dedicated UI for this service and is
linked from the home-page Services list. The independent
`GET /api/google-search/v1/search` endpoint accepts `q` (required, up to 500
characters), `limit` (defaults to 5; allowed range 1–10), `gl` (two-letter country
code, defaults to `us`), and `hl` (language code, defaults to `en`). Responses
contain `query`, `country`, `language`, `source`, `count`, and results with `title`,
`url`, `snippet`, `position`, and `source`. A successful search with no organic
results returns HTTP 200 and an empty `results` list. Invalid parameters return
HTTP 400; provider rate limiting or exhausted provider quota returns HTTP 429;
upstream timeouts return HTTP 504; malformed or other upstream failures return
HTTP 502. The endpoint has a 30-requests-per-minute per-client-IP limit and caches
successful responses.

To keep usage below the currently published free allowance, the service atomically
reserves a maximum of 200 provider requests per UTC calendar month in the persistent
application database. Cache hits do not consume the local allowance; provider
attempts reserve usage before sending a request, including attempts that fail. Once
the local limit is reached, the service fails closed without contacting the
provider. Run `alembic upgrade head` to create the quota ledger table before enabling
the service. This local ceiling does not guarantee future provider quotas or pricing;
confirm the key remains on a no-charge free plan. Diagnostics show the provider
engine, whether a key is configured, and remaining local monthly allowance; they
do not make a billable provider request.

The service uses the shared HTTP client and adds no Python dependency. Ensure the
deployment can reach `serpapi.com`. That host is not on PythonAnywhere's free-account
outbound allowlist; use an eligible unrestricted-egress account or request an
allowlist change before enabling the feature. Egress availability is separate from
provider terms and pricing.

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

## Conversation API

Conversation endpoints require a valid browser session with a database-backed user.
Ownership always comes from that session; request-supplied user IDs are not accepted.
The shared `API_TOKEN` continues to authenticate existing AI Gateway clients, but
cannot access conversation history. Mutating browser requests require the existing
CSRF token (`csrf_token` form field or `X-CSRF-Token` header).

Conversation creation and model updates validate identifiers against the AI service's
currently discovered model list. A discovery failure returns an error rather than
silently switching models. Conversation details include messages ordered by their
database IDs. Message generation accepts JSON `{"content": "..."}`; it persists the
user message before calling the existing AI service with the conversation's selected
model and stored history. For the existing non-streaming `/messages` endpoint, a
generation failure retains the user message and does not create an assistant message.
The first message also sets a short title from its text. The authenticated `/chat`
browser interface discovers models from the backend and uses the conversation endpoints
for all persistent history and model changes. User and assistant text is stored raw; the
browser renders Markdown by creating
DOM text and formatting nodes rather than interpreting message content as HTML.

The browser chat uses the `/messages/stream` endpoint. It saves the user message as
`complete` and creates an empty assistant message as `streaming` in a short database
transaction before calling `AIService.stream()` with the conversation model and stored
message history. The endpoint forwards OpenAI-compatible SSE content chunks and
`[DONE]`; it does not call the public AI HTTP endpoint. Assistant text is persisted as
chunks arrive. A valid `[DONE]` without an error changes the message to `complete`.
Provider errors, malformed or interrupted streams, and browser disconnects preserve
any received text as `incomplete` (or use `failed` if no text arrived). A provider
rejection before the stream opens returns a normal JSON error and marks the assistant
message `failed`; the saved user message is retained in all generation-failure cases.
The existing `/messages` endpoint remains non-streaming.

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
stalled read. Start the app with `flask --app wsgi run --debug`, sign in, and open
`http://127.0.0.1:5000/chat`.

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
   with the new account, request `/api/ai/v1/models` to verify provider discovery, and
   open `https://YOUR-DOMAIN/chat`.
   PythonAnywhere plan/network
   restrictions may prevent outbound connections to one or more official provider
   endpoints.

## PythonAnywhere streaming verification

Streaming is a synchronous WSGI response and occupies a web worker until it completes,
times out, or disconnects. PythonAnywhere [documents a five-minute web-worker
timeout](https://help.pythonanywhere.com/pages/AsyncInWebApps/), but this project has
not been deployed and tested there; SSE delivery, buffering, and client-disconnect
behavior on PythonAnywhere remain unverified. Configure `AI_STREAM_TIMEOUT` below the
platform worker timeout (`240` seconds by default) and `AI_STREAM_IDLE_TIMEOUT` for the
longest acceptable wait between provider bytes. After deployment, test with an
authenticated client:

```bash
curl -N https://YOUR-DOMAIN/api/ai/v1/chat/completions \
  -H "Authorization: Bearer YOUR_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"model":"groq/PROVIDER_MODEL_ID","messages":[{"role":"user","content":"Hello"}],"stream":true}'
```

Confirm chunks arrive incrementally and test client disconnect behavior on the actual
deployment. Also sign in to the deployed `/chat`, start a persistent conversation, and
confirm assistant text appears before generation finishes. Open that conversation's
`GET /api/ai/v1/conversations/<id>` response to verify the final status and text. Start
another response and close or navigate away from the tab; then retrieve the conversation
again and confirm any saved partial assistant text has status `incomplete` (or `failed`
if there was no output). Check that buffering is disabled and keep the configured stream
timeout safely below the worker timeout. Local tests do not establish PythonAnywhere
streaming support; this manual verification must be performed on the deployed
application.

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
