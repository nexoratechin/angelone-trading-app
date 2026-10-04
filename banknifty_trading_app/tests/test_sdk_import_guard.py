"""The Angel One SDK import guard must report the *real* reason it is unavailable.

``smartapi-python`` imports ``logzero`` and ``six`` but does not declare them as
dependencies. When one of those was missing, the old guard swallowed the
ImportError and reported "smartapi-python is not installed" - sending the user
to install a package that was already installed. These tests pin the useful
behaviour.
"""

from __future__ import annotations

import banknifty_trading_app.angelone.auth as auth


def _hint(exc: Exception, monkeypatch) -> str:
    monkeypatch.setattr(auth, "SDK_IMPORT_ERROR", exc)
    return auth.sdk_import_hint()


def test_missing_transitive_dep_names_the_package(monkeypatch):
    hint = _hint(ModuleNotFoundError("No module named 'logzero'", name="logzero"), monkeypatch)
    assert "logzero" in hint
    assert "pip install logzero" in hint
    # must NOT tell the user to install the SDK it already has
    assert "smartapi-python is not installed" not in hint


def test_missing_sdk_recommends_the_sdk_package(monkeypatch):
    hint = _hint(ModuleNotFoundError("No module named 'SmartApi'", name="SmartApi"), monkeypatch)
    assert "pip install smartapi-python" in hint
    # the top-level module is not a pip package name
    assert "pip install SmartApi" not in hint


def test_other_import_errors_are_surfaced(monkeypatch):
    hint = _hint(ImportError("undefined symbol: ffi_type_pointer"), monkeypatch)
    assert "ImportError" in hint
    assert "requirements.txt" in hint


def test_no_recorded_error_is_still_actionable():
    hint = auth.sdk_import_hint()
    assert hint.strip()
    assert "requirements.txt" in hint


def test_sdk_is_importable_in_this_environment():
    """If the SDK is installed, the guard must not claim otherwise."""
    if auth.SmartConnect is None:  # pragma: no cover - depends on environment
        import pytest

        pytest.skip(f"SDK unavailable here: {auth.sdk_import_hint()}")
    assert auth.SDK_IMPORT_ERROR is None
