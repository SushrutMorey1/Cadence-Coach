"""
test_config.py — `.env` hygiene
=================================
A template line like `AZURE_SPEECH_KEY=      # your key` loads the comment as the value,
which makes a feature look configured while it sends garbage to the provider.
"""

import os
import subprocess
import sys
import textwrap
from pathlib import Path

from dotenv import dotenv_values

from app.config import drop_comment_values

ROOT = Path(__file__).resolve().parent.parent


def test_dotenv_really_does_read_the_comment_as_the_value(tmp_path):
    """Documents the trap: if this stops being true, the guard below is merely redundant."""
    env_file = tmp_path / ".env"
    env_file.write_text("KEY=      # a comment\nREAL=abc   # trailing note\n")
    values = dotenv_values(env_file)
    assert (values["KEY"] or "").startswith("#")
    assert values["REAL"] == "abc"


def test_drop_comment_values_unsets_only_comment_entries():
    environ = {"KEY": "# a comment", "OK": "sk-123", "EMPTY": "", "SAME": "# x"}
    values = {"KEY": "# a comment", "OK": "sk-123", "EMPTY": "", "OTHER": "# y"}
    dropped = drop_comment_values(values, environ)
    assert dropped == ["KEY"]
    assert "KEY" not in environ and environ["OK"] == "sk-123"
    assert environ["SAME"] == "# x"      # not part of .env → untouched


def test_real_environment_value_wins_over_file():
    """If the process env was set explicitly to something else, don't delete it."""
    environ = {"KEY": "real-value"}
    assert drop_comment_values({"KEY": "# comment"}, environ) == []
    assert environ["KEY"] == "real-value"


def test_config_treats_comment_value_as_unset(tmp_path):
    """End to end in a fresh interpreter: the feature must not report as configured."""
    project = tmp_path / "proj"
    (project / "app").mkdir(parents=True)
    # Reuse the real config module against a throw-away project root with a poisoned .env.
    (project / "app" / "config.py").write_text((ROOT / "app" / "config.py").read_text(encoding="utf-8"), encoding="utf-8")
    (project / "app" / "__init__.py").write_text("")
    (project / ".env").write_text(textwrap.dedent("""\
        GROQ_API_KEY=real-groq
        AZURE_SPEECH_KEY=                        # your Azure key
        GOOGLE_CLIENT_ID=client-id
        GOOGLE_CLIENT_SECRET=   # the secret
        AUTH_REQUIRED=false
    """), encoding="utf-8")
    code = ("from app import config; s = config.integration_status(); "
            "print(s['groq'], s['azure_tts'], s['google_oauth'], repr(config.AZURE_SPEECH_KEY))")
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("AZURE_", "GOOGLE_", "GROQ_", "AUTH_")) and k != "CADENCE_SKIP_DOTENV"}
    out = subprocess.run([sys.executable, "-c", code], cwd=project, env={**env, "PYTHONPATH": str(project)},
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    # groq, azure_tts, google_oauth, AZURE_SPEECH_KEY — only the real value counts
    assert out.stdout.split() == ["True", "False", "False", "None"], out.stdout
    assert "Ignoring .env entries" in out.stderr
    assert "AZURE_SPEECH_KEY" in out.stderr and "GOOGLE_CLIENT_SECRET" in out.stderr
    assert "real-groq" not in out.stderr                      # never log values


def _fresh_config(tmp_path, dotenv: str, extra_env: dict, code: str):
    """Import the real config module in a fresh interpreter against a throw-away .env."""
    project = tmp_path / "proj"
    (project / "app").mkdir(parents=True)
    (project / "app" / "config.py").write_text((ROOT / "app" / "config.py").read_text(encoding="utf-8"), encoding="utf-8")
    (project / "app" / "__init__.py").write_text("")
    (project / ".env").write_text(textwrap.dedent(dotenv), encoding="utf-8")
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("AZURE_", "GOOGLE_", "GROQ_", "AUTH_", "DB_")) and k != "CADENCE_SKIP_DOTENV"}
    return subprocess.run([sys.executable, "-c", code], cwd=project,
                          env={**env, **extra_env, "PYTHONPATH": str(project)},
                          capture_output=True, text=True, timeout=60)


def test_boot_refuses_to_run_without_google_sign_in(tmp_path):
    """No silent guest mode: missing OAuth keys stop the boot unless AUTH_REQUIRED=false."""
    out = _fresh_config(tmp_path, "GROQ_API_KEY=k\n", {}, "from app import config")
    assert out.returncode != 0 and "GOOGLE_CLIENT_ID" in out.stderr


def test_real_environment_beats_dotenv_and_mysql_is_the_default(tmp_path):
    out = _fresh_config(tmp_path, """\
        GOOGLE_CLIENT_ID=from-file
        GOOGLE_CLIENT_SECRET=secret
    """, {"GOOGLE_CLIENT_ID": "from-env"},
        "from app import config; print(config.GOOGLE_CLIENT_ID, config.AUTH_REQUIRED, config.DB_BACKEND)")
    assert out.returncode == 0, out.stderr
    assert out.stdout.split() == ["from-env", "True", "mysql"]
