"""
Finding the OpenRouter key wherever it was actually put.

"No API key found" while the key is sitting in Streamlit secrets is a dead
end for the user: nothing on screen distinguishes a missing key from one the
app looked for under a different name, in a different place, or with quotes
carried in from a paste. These tests pin the shapes that must resolve.
"""
import importlib
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

KEY = "sk-or-v1-" + "a" * 40


def load_app(monkeypatch, secrets: dict | None, env: dict | None = None):
    """Import app.py with st.secrets and the environment under our control."""
    for name in _KEY_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    for name, value in (env or {}).items():
        monkeypatch.setenv(name, value)

    import streamlit as st

    if secrets is None:
        class Missing(dict):
            def __iter__(self):
                raise FileNotFoundError("no secrets.toml")
            def keys(self):
                raise FileNotFoundError("no secrets.toml")
        monkeypatch.setattr(st, "secrets", Missing(), raising=False)
    else:
        monkeypatch.setattr(st, "secrets", secrets, raising=False)

    sys.modules.pop("app", None)
    app = importlib.import_module("app")
    # load_dotenv() would read a developer's real .env and mask the test.
    monkeypatch.setattr(app, "load_dotenv", lambda *a, **k: None)
    return app


_KEY_ENV_NAMES = ("OPENROUTER_API_KEY", "OPENROUTER_KEY", "OPENROUTER_APIKEY")


@pytest.fixture(autouse=True)
def _quiet_streamlit(monkeypatch):
    """Streamlit warns loudly when run outside a script context."""
    monkeypatch.setenv("STREAMLIT_GLOBAL_SUPPRESS_DEPRECATION_WARNINGS", "1")


def test_reads_a_plain_environment_variable(monkeypatch):
    app = load_app(monkeypatch, secrets={}, env={"OPENROUTER_API_KEY": KEY})
    key, source = app._resolve_api_key()

    assert key == KEY
    assert source == "environment"


def test_reads_a_top_level_secret(monkeypatch):
    app = load_app(monkeypatch, secrets={"OPENROUTER_API_KEY": KEY})
    key, source = app._resolve_api_key()

    assert key == KEY
    assert source == "Streamlit secrets"


def test_reads_a_secret_filed_under_a_section(monkeypatch):
    """A key under [general] is still the key.

    TOML encourages sections, and a secret one level down used to be
    invisible to the app.
    """
    app = load_app(monkeypatch, secrets={"general": {"OPENROUTER_API_KEY": KEY}})
    assert app._resolve_api_key()[0] == KEY


def test_is_case_insensitive_about_the_secret_name(monkeypatch):
    app = load_app(monkeypatch, secrets={"openrouter_api_key": KEY})
    assert app._resolve_api_key()[0] == KEY


def test_accepts_alternative_spellings(monkeypatch):
    for name in ("OPENROUTER_KEY", "OPENROUTER_APIKEY"):
        app = load_app(monkeypatch, secrets={name: KEY})
        assert app._resolve_api_key()[0] == KEY, name


def test_strips_quotes_carried_in_from_a_paste(monkeypatch):
    """`"sk-or-v1-..."` with the quotes inside the value must still work."""
    for wrapped in (f'"{KEY}"', f"'{KEY}'", f"  {KEY}  ", f'  "{KEY}"  '):
        app = load_app(monkeypatch, secrets={"OPENROUTER_API_KEY": wrapped})
        assert app._resolve_api_key()[0] == KEY, repr(wrapped)


def test_environment_wins_over_secrets(monkeypatch):
    other = "sk-or-v1-" + "b" * 40
    app = load_app(monkeypatch, secrets={"OPENROUTER_API_KEY": other},
                   env={"OPENROUTER_API_KEY": KEY})
    assert app._resolve_api_key()[0] == KEY


def test_a_secret_is_published_to_the_environment(monkeypatch):
    """Config and the summariser read os.environ, so it must land there too."""
    app = load_app(monkeypatch, secrets={"OPENROUTER_API_KEY": KEY})
    app._resolve_api_key()

    import os
    assert os.environ["OPENROUTER_API_KEY"] == KEY
    assert app.Config.OPENROUTER_API_KEY == KEY


def test_no_secrets_file_is_not_an_error(monkeypatch):
    """Locally there is usually no secrets.toml at all."""
    app = load_app(monkeypatch, secrets=None)
    assert app._resolve_api_key() == ("", "")


def test_empty_secret_counts_as_missing(monkeypatch):
    app = load_app(monkeypatch, secrets={"OPENROUTER_API_KEY": "   "})
    assert app._resolve_api_key()[0] == ""


def test_badge_reports_a_key_that_is_not_openrouter(monkeypatch):
    app = load_app(monkeypatch, secrets={"OPENROUTER_API_KEY": "abcd" * 12})
    ok, kind, message = app.api_key_ready()

    assert ok is False
    assert kind == "warn"
    assert "does not look like" in message


def test_badge_never_prints_the_whole_key(monkeypatch):
    app = load_app(monkeypatch, secrets={"OPENROUTER_API_KEY": KEY})
    ok, _, message = app.api_key_ready()

    assert ok is True
    assert KEY not in message
    assert message.endswith("(Streamlit secrets)")


def test_snapshot_flattens_sections_without_exposing_values(monkeypatch):
    app = load_app(monkeypatch, secrets={
        "TOP": "top-value",
        "general": {"NESTED": "nested-value"},
    })
    flat, sections = app._secrets_snapshot()

    assert set(flat) == {"TOP", "NESTED"}
    assert sections == ["general"]
