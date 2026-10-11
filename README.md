# Mirror

Small Flask app that relays requests to useful services (Wikipedia, search, AI, ...) from a
server outside Iran. Default-deny: nothing is reachable without login or a Bearer token.

## Layout
- `app/core/`      shared plumbing: http client, cache, SSRF guard, errors, rate limit
- `app/services/`  one file per feature, pure logic (subclass `Service`)
- `app/routes/`    one blueprint per service (`bp`), same file name as the service
- `app/auth.py`    login, CSRF, default-deny hook
- `app/registry.py` auto-discovers services + routes

## Add a service
1. `app/services/foo.py` with `class FooService(Service): name = "foo"; ...`
2. `app/routes/foo.py` with `bp = Blueprint("foo", __name__)`
That's all. Use `/api/foo/...` for JSON and `/foo/...` for HTML pages.

## Local run
    python -m venv .venv && source .venv/bin/activate
    pip install -r requirements.txt
    python tools/make_hash.py            # copy output to ACCESS_PASSWORD_HASH
    cp .env.example .env                 # fill it; set APP_ENV=development locally
    flask --app wsgi run --debug
    python -m unittest discover -v       # tests

## PythonAnywhere
1. Upload/clone the project, create a virtualenv, `pip install -r requirements.txt`.
2. Web tab -> add a Flask app (manual config), set the virtualenv path.
3. In the WSGI file: add the project folder to `sys.path`, then `from wsgi import application`.
4. Create `.env` in the project folder (APP_ENV=production, SECRET_KEY, ACCESS_PASSWORD_HASH, API_TOKEN).
5. Reload, open `/login`, then `/diagnostics` to see which outside hosts the free plan allows.

Keep `.env` out of version control and out of screenshots.
