"""WSGI entry point (PythonAnywhere: point the WSGI file at this module's `application`)."""
import os

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

from app import create_app  # noqa: E402

application = create_app()
app = application  # so `flask --app wsgi run` works too
