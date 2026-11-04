import unittest

from flask import session
from sqlalchemy import select
from werkzeug.security import check_password_hash, generate_password_hash

from app import create_app
from app.auth import get_current_user
from app.config import Config
from app.models import Base, User

PASSWORD_A = "correct-horse-battery-a"
PASSWORD_B = "correct-horse-battery-b"
API_TOKEN = "test-api-token"


class PerUserAuthTests(unittest.TestCase):
    def setUp(self):
        config = Config(
            env="testing",
            secret_key="s" * 40,
            api_token=API_TOKEN,
            database_url="sqlite:///:memory:",
        )
        self.app = create_app(config)
        self.app.testing = True
        self.database = self.app.extensions["database"]
        Base.metadata.create_all(self.database.engine)
        with self.database.session_scope() as db_session:
            db_session.add_all(
                [
                    User(username="alice", password_hash=generate_password_hash(PASSWORD_A)),
                    User(username="bob", password_hash=generate_password_hash(PASSWORD_B)),
                    User(
                        username="inactive",
                        password_hash=generate_password_hash(PASSWORD_A),
                        is_active=False,
                    ),
                ]
            )
        with self.database.session_scope() as db_session:
            self.user_ids = {
                user.username: user.id for user in db_session.scalars(select(User))
            }

    def tearDown(self):
        self.database.dispose()

    def login(self, client, username, password):
        client.get("/login")
        with client.session_transaction() as browser_session:
            csrf = browser_session["csrf"]
        return client.post(
            "/login",
            data={"username": username, "password": password, "csrf_token": csrf},
        )

    def test_valid_login_normalizes_username_and_stores_database_user_id(self):
        client = self.app.test_client()

        response = self.login(client, "  ALICE ", PASSWORD_A)

        self.assertEqual(response.status_code, 302)
        with client.session_transaction() as browser_session:
            self.assertEqual(browser_session["user_id"], self.user_ids["alice"])
            self.assertNotIn("auth", browser_session)

    def test_incorrect_and_unknown_user_have_same_generic_failure(self):
        client = self.app.test_client()

        incorrect = self.login(client, "alice", "wrong-password")
        unknown = self.login(client, "not-registered", "wrong-password")

        self.assertEqual(incorrect.status_code, 401)
        self.assertEqual(unknown.status_code, 401)
        self.assertEqual(incorrect.data, unknown.data)
        self.assertIn(b"Invalid username or password.", incorrect.data)
        self.assertNotIn(b"alice", incorrect.data)

    def test_inactive_user_cannot_log_in(self):
        client = self.app.test_client()

        response = self.login(client, "inactive", PASSWORD_A)

        self.assertEqual(response.status_code, 401)
        with client.session_transaction() as browser_session:
            self.assertNotIn("user_id", browser_session)
        self.assertIn(b"Invalid username or password.", response.data)

    def test_password_hash_verifies_without_storing_plaintext(self):
        with self.database.session_scope() as db_session:
            user = db_session.get(User, self.user_ids["alice"])
            self.assertNotEqual(user.password_hash, PASSWORD_A)
            self.assertTrue(check_password_hash(user.password_hash, PASSWORD_A))
            self.assertFalse(check_password_hash(user.password_hash, PASSWORD_B))

    def test_logout_clears_session(self):
        client = self.app.test_client()
        self.login(client, "alice", PASSWORD_A)
        client.get("/")
        with client.session_transaction() as browser_session:
            csrf = browser_session["csrf"]

        response = client.post("/logout", data={"csrf_token": csrf})

        self.assertEqual(response.status_code, 302)
        with client.session_transaction() as browser_session:
            self.assertNotIn("user_id", browser_session)
            self.assertNotIn("auth", browser_session)

    def test_two_browser_sessions_remain_distinct(self):
        alice_client = self.app.test_client()
        bob_client = self.app.test_client()

        self.login(alice_client, "alice", PASSWORD_A)
        self.login(bob_client, "bob", PASSWORD_B)

        with alice_client.session_transaction() as alice_session:
            alice_id = alice_session["user_id"]
        with bob_client.session_transaction() as bob_session:
            bob_id = bob_session["user_id"]
        self.assertEqual(alice_id, self.user_ids["alice"])
        self.assertEqual(bob_id, self.user_ids["bob"])
        self.assertNotEqual(alice_id, bob_id)
        with self.app.test_request_context("/"):
            session["user_id"] = alice_id
            self.assertEqual(get_current_user().username, "alice")
        with self.app.test_request_context("/"):
            session["user_id"] = bob_id
            self.assertEqual(get_current_user().username, "bob")

    def test_cli_creates_normalized_user_with_hashed_password(self):
        password = "new-secure-user-password"
        result = self.app.test_cli_runner().invoke(
            args=["create-user"],
            input=f"  ChArLiE  \n{password}\n{password}\n",
        )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("Created user 'charlie'.", result.output)
        self.assertNotIn(password, result.output)
        with self.database.session_scope() as db_session:
            created = db_session.scalar(select(User).where(User.username == "charlie"))
            self.assertIsNotNone(created)
            self.assertNotEqual(created.password_hash, password)
            self.assertTrue(check_password_hash(created.password_hash, password))

    def test_cli_rejects_duplicate_username(self):
        result = self.app.test_cli_runner().invoke(
            args=["create-user"],
            input="ALICE\n",
        )

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("already registered", result.output)

    def test_cli_rejects_password_confirmation_mismatch(self):
        result = self.app.test_cli_runner().invoke(
            args=["create-user"],
            input="dana\nfirst-password\nsecond-password\n",
        )

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("Passwords do not match.", result.output)
        with self.database.session_scope() as db_session:
            self.assertIsNone(
                db_session.scalar(select(User.id).where(User.username == "dana"))
            )

    def test_legacy_auth_only_session_must_log_in_again(self):
        client = self.app.test_client()
        with client.session_transaction() as browser_session:
            browser_session["auth"] = True

        response = client.get("/")

        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])
        with client.session_transaction() as browser_session:
            self.assertNotIn("auth", browser_session)
            self.assertNotIn("user_id", browser_session)

    def test_bearer_authentication_remains_independent(self):
        response = self.app.test_client().get(
            "/api/services",
            headers={"Authorization": f"Bearer {API_TOKEN}"},
        )

        self.assertEqual(response.status_code, 200)

    def test_login_csrf_and_failure_rate_limit_remain_enforced(self):
        client = self.app.test_client()
        self.assertEqual(
            client.post("/login", data={"username": "alice", "password": PASSWORD_A}).status_code,
            400,
        )
        for _ in range(5):
            self.assertEqual(self.login(client, "alice", "wrong-password").status_code, 401)

        self.assertEqual(self.login(client, "alice", PASSWORD_A).status_code, 429)


if __name__ == "__main__":
    unittest.main()
