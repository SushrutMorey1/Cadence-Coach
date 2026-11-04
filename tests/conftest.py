"""
conftest.py — Test environment
=================================
Runs the whole suite against an isolated SQLite database with fake API
keys, so `pytest` needs no MySQL, no network and no secrets. The env
vars are set BEFORE `app.config` is imported anywhere.
"""

import os
import pathlib
import tempfile

_TMP = pathlib.Path(tempfile.mkdtemp(prefix="cadence-tests-"))

os.environ["CADENCE_SKIP_DOTENV"] = "1"
os.environ["DB_BACKEND"] = "sqlite"
os.environ["SQLITE_PATH"] = str(_TMP / "test.db")
os.environ["AUTH_REQUIRED"] = "false"
os.environ["GROQ_API_KEY"] = "test-groq-key"
os.environ["AZURE_SPEECH_KEY"] = "test-azure-key"
os.environ["SESSION_SECRET_KEY"] = "test-session-secret"
os.environ["STARTUP_CHECKS"] = "0"        # no network at boot
for _k in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"):
    os.environ.pop(_k, None)

from app.services.database import init_db  # noqa: E402

init_db()
