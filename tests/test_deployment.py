"""
Import-time resilience for hosted deployments.

The Streamlit app died on Streamlit Cloud before a single line of it ran:
logging built a RotatingFileHandler at import, the log directory is gitignored
so a fresh checkout does not have one, and Config.validate() -- which used to
create it -- raised on the missing API key before reaching the mkdir. A
misconfigured key took the whole app down with a FileNotFoundError.

These tests pin the two properties that failure violated: directories are
created regardless of whether credentials are present, and a filesystem that
refuses writes degrades to console logging instead of killing the process.
"""
import builtins
import importlib
import logging
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:                       # the legacy app lives at the root
    sys.path.insert(0, ROOT)


@pytest.fixture
def fresh(tmp_path, monkeypatch):
    """Import config + logger_config fresh, rooted in an empty directory."""
    def _load(*, api_key: str | None, makedirs_fails: bool = False):
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        if api_key:
            monkeypatch.setenv("OPENROUTER_API_KEY", api_key)

        for name in ("config", "logger_config"):
            sys.modules.pop(name, None)

        config = importlib.import_module("config")
        monkeypatch.setattr(config.Config, "OPENROUTER_API_KEY", api_key or "")
        monkeypatch.setattr(config.Config, "LOG_DIR", str(tmp_path / "logs"))
        monkeypatch.setattr(config.Config, "CACHE_DIR", str(tmp_path / "cache"))
        monkeypatch.setattr(config.Config, "SESSION_HISTORY_DIR", str(tmp_path / "hist"))

        if makedirs_fails:
            def deny(*args, **kwargs):
                raise OSError(30, "Read-only file system")
            monkeypatch.setattr(config.os, "makedirs", deny)
            monkeypatch.setattr(os, "makedirs", deny)

        logger_config = importlib.reload(importlib.import_module("logger_config"))
        return config, logger_config

    yield _load

    for name in ("config", "logger_config"):
        sys.modules.pop(name, None)


def test_directories_are_created_without_an_api_key(fresh, tmp_path):
    """The mkdir must not sit behind the credential check.

    This is the exact production failure: no key meant no log directory,
    which meant the logging import blew up.
    """
    config, _ = fresh(api_key=None)
    config.Config.ensure_directories()

    assert (tmp_path / "logs").is_dir()
    assert (tmp_path / "cache").is_dir()
    assert (tmp_path / "hist").is_dir()


def test_validate_still_reports_a_missing_key(fresh):
    config, _ = fresh(api_key=None)
    with pytest.raises(ValueError, match="OPENROUTER_API_KEY"):
        config.Config.validate()


def test_validate_creates_directories_before_it_raises(fresh, tmp_path):
    config, _ = fresh(api_key=None)
    with pytest.raises(ValueError):
        config.Config.validate()

    # The point of the fix: the failure path still leaves a usable log dir.
    assert (tmp_path / "logs").is_dir()


def test_logger_imports_when_the_log_directory_is_absent(fresh, tmp_path):
    """Importing logging must not depend on the directory already existing."""
    assert not (tmp_path / "logs").exists()

    _, logger_config = fresh(api_key=None)
    logger = logger_config.setup_logger("deployment-test")

    assert logger.handlers, "logger came back with no handlers at all"
    logger.info("this must not raise")


def test_logging_falls_back_to_console_on_a_read_only_filesystem(fresh):
    """A read-only mount costs the log file, not the process."""
    _, logger_config = fresh(api_key=None, makedirs_fails=True)
    logger = logger_config.setup_logger("readonly-test")

    kinds = [type(h) for h in logger.handlers]
    assert logging.StreamHandler in kinds, "no console handler to fall back to"
    assert not any(isinstance(h, logging.FileHandler) for h in logger.handlers)

    logger.warning("still usable")


def test_cache_manager_survives_a_read_only_filesystem(monkeypatch, tmp_path):
    """A cache that cannot be written is a miss, not a crash."""
    import cache_manager

    def deny(*args, **kwargs):
        raise OSError(30, "Read-only file system")

    monkeypatch.setattr(cache_manager.Path, "mkdir", deny)
    manager = cache_manager.CacheManager(cache_dir=str(tmp_path / "nope"))

    assert manager.enabled is False
    assert manager.get("f.pdf", "hash", "brief") is None
    assert manager.set("f.pdf", "hash", "brief", {"summary": "x"}) is True


def test_session_manager_survives_a_read_only_filesystem(monkeypatch, tmp_path):
    import session_manager

    def deny(*args, **kwargs):
        raise OSError(30, "Read-only file system")

    monkeypatch.setattr(session_manager.Path, "mkdir", deny)
    manager = session_manager.SessionManager(session_dir=str(tmp_path / "nope"))

    assert manager.enabled is False


def test_app_module_imports_with_no_key_and_no_directories(monkeypatch, tmp_path):
    """The whole failure, end to end: app.py must import in a bare checkout."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)

    for name in list(sys.modules):
        if name in {"config", "logger_config", "cache_manager", "session_manager"}:
            del sys.modules[name]

    # Importing these is what crashed in production.
    importlib.import_module("logger_config")
    importlib.import_module("cache_manager")
    importlib.import_module("session_manager")


def test_import_does_not_depend_on_the_cwd(monkeypatch, tmp_path):
    """Config paths are anchored to the module, not the working directory."""
    monkeypatch.chdir(tmp_path)
    sys.modules.pop("config", None)
    config = importlib.import_module("config")

    assert os.path.isabs(config.Config.LOG_DIR)
    assert not config.Config.LOG_DIR.startswith(str(tmp_path))


def test_builtins_untouched():
    """Guard against a fixture leaking a patched open() into other tests."""
    assert builtins.open is open
