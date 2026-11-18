"""The safety judge is ON unless SAFETY_JUDGE says otherwise (switched on 18 Nov 2026)."""

import importlib
import os
from unittest.mock import patch

from app import config


def test_default_is_on_and_env_turns_it_off():
    try:
        with patch.dict(os.environ):
            os.environ.pop("SAFETY_JUDGE", None)
            assert importlib.reload(config).SAFETY_JUDGE is True
        with patch.dict(os.environ, {"SAFETY_JUDGE": "off"}):
            assert importlib.reload(config).SAFETY_JUDGE is False
    finally:
        importlib.reload(config)
