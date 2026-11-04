import unittest
from unittest.mock import patch

from werkzeug.security import generate_password_hash

from app import create_app
from app.config import Config
from app.core.cache import MemoryCache
from app.core.errors import ServiceError
from app.core.security import assert_public_url, is_public_ip
from app.models import Base, User
from app.services.diagnostics import DiagnosticsService

PASSWORD = "correct-horse-battery"
TOKEN = "tok-1234567890"


def make_app(**kw):
    cfg = Config(env="testing", secret_key="k" * 40, api_token=TOKEN,
                 database_url="sqlite:///:memory:", **kw)
    app = create_app(cfg)
    app.testing = True
    database = app.extensions["database"]
    Base.metadata.create_all(database.engine)
    with database.session_scope() as db_session:
        db_session.add(User(username="owner", password_hash=generate_password_hash(PASSWORD)))
    return app


def login(client, password=PASSWORD, nxt=""):
    client.get("/login")
    with client.session_transaction() as s:
        csrf = s["csrf"]
    return client.post("/login", data={
        "username": "owner", "password": password, "csrf_token": csrf, "next": nxt,
    })


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.app = make_app()
        self.c = self.app.test_client()

    def tearDown(self):
        self.app.extensions["database"].dispose()

    def test_healthz_is_public(self):
        self.assertEqual(self.c.get("/healthz").status_code, 200)

    def test_default_deny(self):
        r = self.c.get("/")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login", r.headers["Location"])
        chat = self.c.get("/chat")
        self.assertEqual(chat.status_code, 302)
        self.assertIn("/login", chat.headers["Location"])
        r = self.c.get("/api/services")
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.get_json()["status"], 401)

    def test_chat_page_uses_authenticated_template_and_csrf_token(self):
        login(self.c)
        response = self.c.get("/chat")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'name="csrf-token"', response.data)
        self.assertIn(b"/api/ai/v1/models", response.data)
        self.assertIn(b"Open Mirror Chat", self.c.get("/").data)

    def test_bearer_token(self):
        ok = self.c.get("/api/services", headers={"Authorization": f"Bearer {TOKEN}"})
        self.assertEqual(ok.status_code, 200)
        bad = self.c.get("/api/services", headers={"Authorization": "Bearer nope"})
        self.assertEqual(bad.status_code, 401)

    def test_login_requires_csrf(self):
        r = self.c.post("/login", data={"password": PASSWORD})
        self.assertEqual(r.status_code, 400)

    def test_wrong_then_right_password(self):
        self.assertEqual(login(self.c, "wrong").status_code, 401)
        r = login(self.c)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.c.get("/").status_code, 200)

    def test_lockout(self):
        for _ in range(5):
            login(self.c, "wrong")
        self.assertEqual(login(self.c, PASSWORD).status_code, 429)

    def test_no_open_redirect(self):
        r = login(self.c, nxt="//evil.example")
        self.assertEqual(r.headers["Location"], "/")
        self.c.post("/logout", data={"csrf_token": "x"})  # rejected (bad csrf) - still logged in
        other_app = make_app()
        self.addCleanup(other_app.extensions["database"].dispose)
        r = login(other_app.test_client(), nxt="/diagnostics")
        self.assertEqual(r.headers["Location"], "/diagnostics")


class ServiceTests(unittest.TestCase):
    def test_services_discovered(self):
        app = make_app()
        self.addCleanup(app.extensions["database"].dispose)
        self.assertIn("diagnostics", app.extensions["mirror"].services)

    def test_enabled_services_filter(self):
        app = make_app(enabled_services=("nothing",))
        self.addCleanup(app.extensions["database"].dispose)
        self.assertEqual(app.extensions["mirror"].services, {})

    def test_diagnostics_route(self):
        app = make_app()
        self.addCleanup(app.extensions["database"].dispose)
        fake = [{"label": "X", "host": "x.test", "reachable": True, "status": 200, "error": None, "ms": 5}]
        with patch.object(DiagnosticsService, "check", return_value=fake):
            c = app.test_client()
            r = c.get("/api/diagnostics/outbound", headers={"Authorization": f"Bearer {TOKEN}"})
            self.assertEqual(r.get_json()["results"], fake)
            login(c)
            self.assertIn(b"x.test", c.get("/diagnostics").data)

    def test_config_without_api_token_remains_default_deny(self):
        config = Config(
            env="testing",
            secret_key="k" * 40,
            database_url="sqlite:///:memory:",
        )
        config.validate()
        app = create_app(config)
        self.addCleanup(app.extensions["database"].dispose)
        self.assertEqual(app.test_client().get("/").status_code, 302)


class CoreTests(unittest.TestCase):
    def test_public_ip(self):
        for bad in ("127.0.0.1", "10.0.0.5", "192.168.1.1", "169.254.169.254", "::1", "::ffff:127.0.0.1", "224.0.0.1"):
            self.assertFalse(is_public_ip(bad), bad)
        self.assertTrue(is_public_ip("8.8.8.8"))

    def test_assert_public_url(self):
        for bad in ("ftp://example.com", "http://user:pw@example.com", "http://127.0.0.1/", "http://[::1]/", "file:///etc/passwd"):
            with self.assertRaises(ServiceError, msg=bad):
                assert_public_url(bad)
        with self.assertRaises(ServiceError):
            assert_public_url("https://evil.example.net/", allowed_hosts=["wikipedia.org"])

    def test_cache_ttl_and_eviction(self):
        c = MemoryCache(max_items=2, default_ttl=100)
        c.set("a", 1); c.set("b", 2); c.set("c", 3)
        self.assertIsNone(c.get("a"))
        self.assertEqual(c.get("c"), 3)
        c.set("z", 9, ttl=-1)
        self.assertIsNone(c.get("z"))


if __name__ == "__main__":
    unittest.main()
