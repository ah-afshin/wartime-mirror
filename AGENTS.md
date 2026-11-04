# Project Guidelines

## Architecture
- `app/__init__.py` builds the Flask app and shared `AppContext`.
- `app/core/` contains shared HTTP, caching, security, error, and rate-limit utilities.
- Features live in `app/services/`; each service's logic should stay independent of Flask.
- A matching `app/routes/<name>.py` may expose a Flask blueprint named `bp`. Services and matching routes are discovered automatically by `app/registry.py`.
- Templates and static assets are in `app/templates/` and `app/static/`.

## Security
- Preserve the default-deny access model and CSRF protections; do not make routes public without a clear requirement.
- Read secrets from environment configuration. Never commit `.env` or credentials.
- Route outbound requests through the shared HTTP client and use `app/core/security.py` URL checks for user-supplied URLs.

## Tests
- Run the suite with `python -m unittest discover -v`.
- Tests use `unittest`; add or update focused tests for behavior changes.
- Follow `README.md` for local setup and app startup.
