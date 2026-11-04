import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from sqlalchemy import func, select
from werkzeug.security import generate_password_hash

from app import create_app
from app.config import Config
from app.models import Base, Conversation, Message, User

PASSWORD = "conversation-test-password"
API_TOKEN = "conversation-api-token"
MODEL_LIST = {
    "object": "list",
    "data": [
        {"id": "groq/model-a", "object": "model", "owned_by": "groq"},
        {"id": "mistral/model-b", "object": "model", "owned_by": "mistral"},
    ],
    "providers": [
        {"id": "groq", "configured": True, "available": True, "models": ["groq/model-a"]},
        {"id": "mistral", "configured": True, "available": True, "models": ["mistral/model-b"]},
    ],
}


class ConversationAPITests(unittest.TestCase):
    def setUp(self):
        config = Config(
            env="testing",
            secret_key="c" * 40,
            api_token=API_TOKEN,
            database_url="sqlite:///:memory:",
        )
        self.app = create_app(config)
        self.app.testing = True
        self.database = self.app.extensions["database"]
        Base.metadata.create_all(self.database.engine)
        with self.database.session_scope() as db_session:
            alice = User(username="alice", password_hash=generate_password_hash(PASSWORD))
            bob = User(username="bob", password_hash=generate_password_hash(PASSWORD))
            db_session.add_all([alice, bob])
            db_session.flush()
            alice_id, bob_id = alice.id, bob.id
            alice_conversation = Conversation(
                user_id=alice_id, title="Alice chat", model="groq/model-a",
            )
            alice_conversation.messages.extend(
                [
                    Message(role="user", content="Alice question"),
                    Message(role="assistant", content="Alice answer", model="groq/model-a"),
                ]
            )
            second_alice_conversation = Conversation(
                user_id=alice_id, title="Older Alice chat", model="groq/model-a",
                updated_at=datetime.now(timezone.utc) - timedelta(days=1),
            )
            bob_conversation = Conversation(
                user_id=bob_id, title="Bob chat", model="mistral/model-b",
            )
            db_session.add_all(
                [alice_conversation, second_alice_conversation, bob_conversation]
            )
            db_session.flush()
            self.alice_conversation_id = alice_conversation.id
            self.second_alice_conversation_id = second_alice_conversation.id
            self.bob_conversation_id = bob_conversation.id
            self.alice_id = alice_id
            self.bob_id = bob_id

        self.ai_service = self.app.extensions["mirror"].services["ai"]
        self.models_patch = patch.object(self.ai_service, "models", return_value=MODEL_LIST)
        self.models_patch.start()
        self.addCleanup(self.models_patch.stop)

    def tearDown(self):
        self.database.dispose()

    def login(self, username):
        client = self.app.test_client()
        client.get("/login")
        with client.session_transaction() as browser_session:
            csrf = browser_session["csrf"]
        result = client.post(
            "/login",
            data={"username": username, "password": PASSWORD, "csrf_token": csrf},
        )
        self.assertEqual(result.status_code, 302)
        self.assertEqual(client.get("/").status_code, 200)
        return client

    def csrf(self, client):
        with client.session_transaction() as browser_session:
            return browser_session["csrf"]

    def test_listing_is_user_scoped_and_sorted_by_recent_update(self):
        alice_client = self.login("alice")
        bob_client = self.login("bob")
        alice_response = alice_client.get("/api/ai/v1/conversations")
        bob_response = bob_client.get("/api/ai/v1/conversations")

        self.assertEqual(alice_response.status_code, 200)
        alice_rows = alice_response.get_json()["conversations"]
        self.assertEqual(
            [row["id"] for row in alice_rows],
            [self.alice_conversation_id, self.second_alice_conversation_id],
        )
        self.assertTrue({"id", "title", "model", "created_at", "updated_at"} <= alice_rows[0].keys())
        self.assertEqual(
            [row["id"] for row in bob_response.get_json()["conversations"]],
            [self.bob_conversation_id],
        )
        bob_detail = bob_client.get(
            f"/api/ai/v1/conversations/{self.bob_conversation_id}"
        )
        self.assertEqual(bob_detail.status_code, 200)
        self.assertEqual(bob_detail.get_json()["conversation"]["title"], "Bob chat")

    def test_retrieve_includes_ordered_messages_for_owner(self):
        response = self.login("alice").get(
            f"/api/ai/v1/conversations/{self.alice_conversation_id}"
        )

        self.assertEqual(response.status_code, 200)
        conversation = response.get_json()["conversation"]
        self.assertEqual(conversation["title"], "Alice chat")
        self.assertEqual(
            [message["content"] for message in conversation["messages"]],
            ["Alice question", "Alice answer"],
        )

    def test_create_defaults_title_and_uses_authenticated_user(self):
        client = self.login("alice")

        response = client.post(
            "/api/ai/v1/conversations",
            json={"model": "groq/model-a"},
            headers={"X-CSRF-Token": self.csrf(client)},
        )

        self.assertEqual(response.status_code, 201)
        created = response.get_json()["conversation"]
        self.assertEqual(created["title"], "New chat")
        with self.database.session_scope() as db_session:
            saved = db_session.get(Conversation, created["id"])
            self.assertEqual(saved.user_id, self.alice_id)
            self.assertEqual(saved.model, "groq/model-a")

    def test_create_validates_model_and_rejects_client_owner(self):
        client = self.login("alice")
        csrf = self.csrf(client)

        unavailable = client.post(
            "/api/ai/v1/conversations",
            json={"model": "groq/not-listed"},
            headers={"X-CSRF-Token": csrf},
        )
        spoofed = client.post(
            "/api/ai/v1/conversations",
            json={"model": "groq/model-a", "user_id": self.bob_id},
            headers={"X-CSRF-Token": csrf},
        )

        self.assertEqual(unavailable.status_code, 400)
        self.assertEqual(spoofed.status_code, 400)
        with self.database.session_scope() as db_session:
            count = db_session.scalar(select(func.count()).select_from(Conversation))
        self.assertEqual(count, 3)

    def test_model_discovery_failure_is_reported(self):
        self.ai_service.models.return_value = {
            "object": "list",
            "data": [],
            "providers": [
                {"id": "groq", "configured": True, "available": False,
                 "models": [], "error": "provider_unavailable"},
            ],
        }
        client = self.login("alice")

        response = client.post(
            "/api/ai/v1/conversations",
            json={"model": "groq/model-a"},
            headers={"X-CSRF-Token": self.csrf(client)},
        )

        self.assertEqual(response.status_code, 503)
        self.assertIn("discovery failed", response.get_json()["error"])

    def test_patch_renames_and_changes_model_without_changing_messages(self):
        client = self.login("alice")
        url = f"/api/ai/v1/conversations/{self.alice_conversation_id}"
        before = client.get(url).get_json()["conversation"]["messages"]

        response = client.patch(
            url,
            json={"title": "Renamed", "model": "mistral/model-b"},
            headers={"X-CSRF-Token": self.csrf(client)},
        )

        self.assertEqual(response.status_code, 200)
        updated = response.get_json()["conversation"]
        self.assertEqual(updated["title"], "Renamed")
        self.assertEqual(updated["model"], "mistral/model-b")
        after = client.get(url).get_json()["conversation"]["messages"]
        self.assertEqual(after, before)

    def test_patch_rejects_invalid_values_and_owner_id(self):
        client = self.login("alice")
        url = f"/api/ai/v1/conversations/{self.alice_conversation_id}"
        csrf = self.csrf(client)

        empty = client.patch(url, json={}, headers={"X-CSRF-Token": csrf})
        invalid_title = client.patch(
            url, json={"title": "   "}, headers={"X-CSRF-Token": csrf},
        )
        spoofed = client.patch(
            url, json={"user_id": self.bob_id}, headers={"X-CSRF-Token": csrf},
        )

        self.assertEqual(empty.status_code, 400)
        self.assertEqual(invalid_title.status_code, 400)
        self.assertEqual(spoofed.status_code, 400)
        with self.database.session_scope() as db_session:
            saved = db_session.get(Conversation, self.alice_conversation_id)
            self.assertEqual(saved.user_id, self.alice_id)
            self.assertEqual(saved.title, "Alice chat")

    def test_other_user_cannot_retrieve_read_messages_rename_or_delete(self):
        bob = self.login("bob")
        url = f"/api/ai/v1/conversations/{self.alice_conversation_id}"
        csrf = self.csrf(bob)

        get_response = bob.get(url)
        patch_response = bob.patch(
            url, json={"title": "Stolen"}, headers={"X-CSRF-Token": csrf},
        )
        delete_response = bob.delete(url, headers={"X-CSRF-Token": csrf})

        self.assertEqual(get_response.status_code, 404)
        self.assertEqual(patch_response.status_code, 404)
        self.assertEqual(delete_response.status_code, 404)
        with self.database.session_scope() as db_session:
            saved = db_session.get(Conversation, self.alice_conversation_id)
            self.assertEqual(saved.title, "Alice chat")
            self.assertEqual(
                db_session.scalar(
                    select(func.count()).select_from(Message).where(
                        Message.conversation_id == self.alice_conversation_id
                    )
                ),
                2,
            )

    def test_delete_removes_related_messages(self):
        client = self.login("alice")

        response = client.delete(
            f"/api/ai/v1/conversations/{self.alice_conversation_id}",
            headers={"X-CSRF-Token": self.csrf(client)},
        )

        self.assertEqual(response.status_code, 204)
        with self.database.session_scope() as db_session:
            self.assertIsNone(db_session.get(Conversation, self.alice_conversation_id))
            self.assertEqual(
                db_session.scalar(
                    select(func.count()).select_from(Message).where(
                        Message.conversation_id == self.alice_conversation_id
                    )
                ),
                0,
            )

    def test_bearer_only_cannot_access_conversation_history(self):
        response = self.app.test_client().get(
            "/api/ai/v1/conversations",
            headers={"Authorization": f"Bearer {API_TOKEN}"},
        )

        self.assertEqual(response.status_code, 401)

    def test_mutations_require_csrf_even_with_bearer_and_browser_session(self):
        client = self.login("alice")

        response = client.post(
            "/api/ai/v1/conversations",
            json={"model": "groq/model-a"},
            headers={"Authorization": f"Bearer {API_TOKEN}"},
        )

        self.assertEqual(response.status_code, 400)

    def test_mutations_without_csrf_are_rejected(self):
        client = self.login("alice")

        response = client.post(
            "/api/ai/v1/conversations",
            json={"model": "groq/model-a"},
        )

        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
