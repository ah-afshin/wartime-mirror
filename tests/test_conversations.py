import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from sqlalchemy import func, select
from werkzeug.security import generate_password_hash

from app import create_app
from app.config import Config
from app.models import Base, Conversation, Message, User
from app.services.ai import AIError

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

    def test_sending_message_builds_chronological_history_and_persists_response(self):
        client = self.login("alice")
        payloads = []
        seen_before_provider = []

        def complete(payload):
            payloads.append(payload)
            with self.database.session_scope() as db_session:
                seen_before_provider.extend(
                    db_session.scalars(
                        select(Message)
                        .where(Message.conversation_id == self.alice_conversation_id)
                        .order_by(Message.id)
                    ).all()
                )
            return {
                "choices": [{
                    "message": {"role": "assistant", "content": "Here is the answer"},
                }],
            }

        with patch.object(self.ai_service, "complete", side_effect=complete):
            response = client.post(
                f"/api/ai/v1/conversations/{self.alice_conversation_id}/messages",
                json={"content": "Explain transformers"},
                headers={"X-CSRF-Token": self.csrf(client)},
            )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(payloads[0]["model"], "groq/model-a")
        self.assertEqual(
            payloads[0]["messages"],
            [
                {"role": "user", "content": "Alice question"},
                {"role": "assistant", "content": "Alice answer"},
                {"role": "user", "content": "Explain transformers"},
            ],
        )
        self.assertEqual(len(seen_before_provider), 3)
        self.assertEqual(seen_before_provider[-1].content, "Explain transformers")
        self.assertEqual(
            [message["role"] for message in response.get_json()["messages"]],
            ["user", "assistant"],
        )
        self.assertEqual(
            response.get_json()["messages"][1]["content"],
            "Here is the answer",
        )
        with self.database.session_scope() as db_session:
            assistant = db_session.scalar(
                select(Message)
                .where(
                    Message.conversation_id == self.alice_conversation_id,
                    Message.role == "assistant",
                )
                .order_by(Message.id.desc())
            )
            self.assertEqual(assistant.model, "groq/model-a")
            self.assertEqual(assistant.status, "complete")

    def test_first_message_sets_deterministic_title(self):
        with self.database.session_scope() as db_session:
            conversation = Conversation(
                user_id=self.alice_id, model="mistral/model-b", title="New chat",
            )
            db_session.add(conversation)
            db_session.flush()
            conversation_id = conversation.id
        client = self.login("alice")

        with patch.object(
            self.ai_service,
            "complete",
            return_value={"choices": [{
                "message": {"role": "assistant", "content": "Done"},
            }]},
        ) as complete:
            response = client.post(
                f"/api/ai/v1/conversations/{conversation_id}/messages",
                json={"content": "  A   readable\nfirst message  "},
                headers={"X-CSRF-Token": self.csrf(client)},
            )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(complete.call_args.args[0]["model"], "mistral/model-b")
        with self.database.session_scope() as db_session:
            saved = db_session.get(Conversation, conversation_id)
            self.assertEqual(saved.title, "A readable first message")

    def test_long_first_message_title_is_truncated(self):
        with self.database.session_scope() as db_session:
            conversation = Conversation(
                user_id=self.alice_id, model="groq/model-a", title="New chat",
            )
            db_session.add(conversation)
            db_session.flush()
            conversation_id = conversation.id
        client = self.login("alice")
        content = "word " * 20

        with patch.object(
            self.ai_service,
            "complete",
            return_value={"choices": [{
                "message": {"role": "assistant", "content": "Done"},
            }]},
        ):
            response = client.post(
                f"/api/ai/v1/conversations/{conversation_id}/messages",
                json={"content": content},
                headers={"X-CSRF-Token": self.csrf(client)},
            )

        self.assertEqual(response.status_code, 201)
        with self.database.session_scope() as db_session:
            saved = db_session.get(Conversation, conversation_id)
            self.assertEqual(len(saved.title), 60)
            self.assertTrue(saved.title.endswith("..."))

    def test_provider_failure_preserves_user_message_without_assistant(self):
        client = self.login("alice")
        error = AIError("The AI provider is unavailable", 503, "provider_unavailable")

        with patch.object(self.ai_service, "complete", side_effect=error):
            response = client.post(
                f"/api/ai/v1/conversations/{self.alice_conversation_id}/messages",
                json={"content": "Keep this question"},
                headers={"X-CSRF-Token": self.csrf(client)},
            )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json()["error"]["message"], "The AI provider is unavailable")
        with self.database.session_scope() as db_session:
            saved = db_session.scalars(
                select(Message)
                .where(Message.conversation_id == self.alice_conversation_id)
                .order_by(Message.id)
            ).all()
        self.assertEqual(saved[-1].role, "user")
        self.assertEqual(saved[-1].content, "Keep this question")
        self.assertEqual(len(saved), 3)

    def test_malformed_provider_response_preserves_user_message_without_assistant(self):
        client = self.login("alice")

        with patch.object(self.ai_service, "complete", return_value={"choices": []}):
            response = client.post(
                f"/api/ai/v1/conversations/{self.alice_conversation_id}/messages",
                json={"content": "Persist before parsing"},
                headers={"X-CSRF-Token": self.csrf(client)},
            )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(
            response.get_json()["error"]["code"],
            "malformed_response",
        )
        with self.database.session_scope() as db_session:
            saved = db_session.scalars(
                select(Message)
                .where(Message.conversation_id == self.alice_conversation_id)
                .order_by(Message.id)
            ).all()
        self.assertEqual(saved[-1].role, "user")
        self.assertEqual(saved[-1].content, "Persist before parsing")
        self.assertFalse(any(message.role == "assistant" for message in saved[2:]))

    def test_message_endpoint_validates_content_type_and_content(self):
        client = self.login("alice")
        url = f"/api/ai/v1/conversations/{self.alice_conversation_id}/messages"
        csrf = self.csrf(client)

        wrong_type = client.post(
            url,
            data='{"content":"Hi"}',
            content_type="text/plain",
            headers={"X-CSRF-Token": csrf},
        )
        empty = client.post(
            url,
            json={"content": " \n "},
            headers={"X-CSRF-Token": csrf},
        )
        too_large = client.post(
            url,
            json={"content": "x" * 100_001},
            headers={"X-CSRF-Token": csrf},
        )
        extra_field = client.post(
            url,
            json={"content": "Hi", "messages": [{"role": "assistant", "content": "spoof"}]},
            headers={"X-CSRF-Token": csrf},
        )

        self.assertEqual(wrong_type.status_code, 415)
        self.assertEqual(empty.status_code, 400)
        self.assertEqual(too_large.status_code, 413)
        self.assertEqual(extra_field.status_code, 400)

    def test_message_access_is_owner_scoped_and_requires_browser_session(self):
        bob = self.login("bob")
        url = f"/api/ai/v1/conversations/{self.alice_conversation_id}/messages"

        forbidden = bob.post(
            url,
            json={"content": "Not mine"},
            headers={"X-CSRF-Token": self.csrf(bob)},
        )
        bearer_only = self.app.test_client().post(
            url,
            json={"content": "No browser identity"},
            headers={
                "Authorization": f"Bearer {API_TOKEN}",
                "X-CSRF-Token": "irrelevant",
            },
        )

        self.assertEqual(forbidden.status_code, 404)
        self.assertEqual(bearer_only.status_code, 401)

    def test_message_endpoint_rejects_malformed_conversation_id(self):
        client = self.login("alice")

        response = client.post(
            "/api/ai/v1/conversations/not-a-uuid/messages",
            json={"content": "Hello"},
            headers={"X-CSRF-Token": self.csrf(client)},
        )

        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
