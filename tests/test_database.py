import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from app import create_app
from app.config import Config
from app.models import Base, Conversation, Message, User


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        database_path = Path(self.temporary_directory.name) / "nested" / "isolated.sqlite3"
        self.config = Config(
            env="testing",
            secret_key="k" * 40,
            api_token="test-token",
            database_url=f"sqlite:///{database_path.as_posix()}",
        )
        self.app = create_app(self.config)
        self.database = self.app.extensions["database"]
        self.assertTrue(database_path.parent.is_dir())
        Base.metadata.create_all(self.database.engine)

    def tearDown(self):
        self.database.dispose()
        self.temporary_directory.cleanup()

    def test_create_user(self):
        with self.database.session_scope() as session:
            session.add(User(username="amira", password_hash="hashed-password"))

        with self.database.session_scope() as session:
            saved_user = session.scalar(select(User).where(User.username == "amira"))

        self.assertIsNotNone(saved_user)
        self.assertTrue(saved_user.is_active)
        self.assertEqual(saved_user.password_hash, "hashed-password")
        self.assertIsNotNone(saved_user.created_at)

    def test_password_hash_is_required_by_database_schema(self):
        password_hash_column = next(
            column
            for column in inspect(self.database.engine).get_columns("users")
            if column["name"] == "password_hash"
        )
        self.assertFalse(password_hash_column["nullable"])

        with self.assertRaises(IntegrityError):
            with self.database.session_scope() as session:
                session.add(User(username="missing-hash", password_hash=None))
                session.flush()

    def test_create_conversation_belonging_to_user(self):
        with self.database.session_scope() as session:
            user = User(username="amira", password_hash="hashed-password")
            conversation = Conversation(model="groq/model", user=user)
            session.add(conversation)
            session.flush()
            conversation_id = conversation.id
            user_id = user.id

        with self.database.session_scope() as session:
            saved_conversation = session.get(Conversation, conversation_id)
            self.assertEqual(saved_conversation.user_id, user_id)
            self.assertEqual(saved_conversation.title, "New chat")
            self.assertEqual(saved_conversation.user.username, "amira")

    def test_create_messages_and_relationships(self):
        with self.database.session_scope() as session:
            user = User(username="amira", password_hash="hashed-password")
            conversation = Conversation(model="groq/model", user=user)
            user_message = Message(role="user", content="Hello", conversation=conversation)
            assistant_message = Message(
                role="assistant", content="Hi", model="groq/model", conversation=conversation
            )
            session.add(user)
            session.flush()

        with self.database.session_scope() as session:
            saved_conversation = session.get(Conversation, conversation.id)
            self.assertIs(saved_conversation.user.conversations[0], saved_conversation)
            self.assertEqual(
                [message.content for message in saved_conversation.messages],
                [user_message.content, assistant_message.content],
            )
            self.assertEqual(user_message.conversation_id, saved_conversation.id)
        self.assertEqual(user_message.status, "complete")
        self.assertEqual(assistant_message.status, "complete")

    def test_messages_are_ordered_by_primary_key(self):
        with self.database.session_scope() as session:
            user = User(username="amira", password_hash="hashed-password")
            conversation = Conversation(model="groq/model", user=user)
            conversation.messages.extend(
                [
                    Message(id=20, role="assistant", content="second"),
                    Message(id=10, role="user", content="first"),
                ]
            )
            session.add(conversation)
            session.flush()
            conversation_id = conversation.id

        with self.database.session_scope() as session:
            saved_conversation = session.get(Conversation, conversation_id)
            self.assertEqual([message.id for message in saved_conversation.messages], [10, 20])

    def test_deleting_conversation_cascades_to_messages(self):
        with self.database.session_scope() as session:
            user = User(username="amira", password_hash="hashed-password")
            conversation = Conversation(model="groq/model", user=user)
            conversation.messages.append(Message(role="user", content="Hello"))
            session.add(conversation)
            session.flush()
            conversation_id = conversation.id

        with self.database.session_scope() as session:
            session.delete(session.get(Conversation, conversation_id))

        with self.database.session_scope() as session:
            self.assertIsNone(session.scalar(select(Message.id)))

    def test_deleting_user_cascades_to_conversations(self):
        with self.database.session_scope() as session:
            user = User(username="amira", password_hash="hashed-password")
            user.conversations.append(Conversation(model="groq/model"))
            session.add(user)
            session.flush()
            user_id = user.id

        with self.database.session_scope() as session:
            session.delete(session.get(User, user_id))

        with self.database.session_scope() as session:
            self.assertIsNone(session.scalar(select(Conversation.id)))

    def test_data_persists_between_sessions(self):
        with self.database.session_scope() as session:
            session.add(User(username="persistent", password_hash="hashed-password"))

        with self.database.session_scope() as session:
            persisted_user = session.scalar(select(User).where(User.username == "persistent"))

        self.assertIsNotNone(persisted_user)

    def test_sqlite_foreign_keys_are_enabled(self):
        with self.database.session_scope() as session:
            self.assertEqual(session.scalar(text("PRAGMA foreign_keys")), 1)

        with self.assertRaises(IntegrityError):
            with self.database.session_scope() as session:
                session.add(Conversation(user_id=999, model="groq/model"))
                session.flush()

        with self.assertRaises(IntegrityError):
            with self.database.session_scope() as session:
                session.add(Message(conversation_id="missing", role="user", content="Hello"))
                session.flush()

    def test_message_role_and_status_constraints(self):
        with self.assertRaises(IntegrityError):
            with self.database.session_scope() as session:
                user = User(username="amira", password_hash="hashed-password")
                conversation = Conversation(model="groq/model", user=user)
                conversation.messages.append(Message(role="system", content="not allowed"))
                session.add(conversation)
                session.flush()

        with self.assertRaises(IntegrityError):
            with self.database.session_scope() as session:
                user = User(username="amira", password_hash="hashed-password")
                conversation = Conversation(model="groq/model", user=user)
                conversation.messages.append(
                    Message(role="user", content="Hello", status="unknown")
                )
                session.add(conversation)
                session.flush()


class DatabaseConfigTests(unittest.TestCase):
    def test_database_url_can_be_overridden_by_environment(self):
        with patch.dict(os.environ, {"DATABASE_URL": "sqlite:///custom.sqlite3"}):
            self.assertEqual(Config.from_env().database_url, "sqlite:///custom.sqlite3")

    def test_empty_database_url_uses_default(self):
        with patch.dict(os.environ, {"DATABASE_URL": ""}):
            self.assertEqual(Config.from_env().database_url, Config().database_url)

    def test_default_database_url_uses_absolute_instance_path(self):
        database_path = Path(make_url(Config().database_url).database)
        self.assertTrue(database_path.is_absolute())
        self.assertEqual(database_path.parent.name, "instance")


if __name__ == "__main__":
    unittest.main()
