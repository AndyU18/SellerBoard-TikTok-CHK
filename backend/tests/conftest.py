import pytest

from app.config import settings


@pytest.fixture(autouse=True)
def _auth_off_by_default(monkeypatch):
    """Los tests de datos no inician sesión; los de test_auth.py la activan explícitamente."""
    monkeypatch.setattr(settings, "auth_required", False)
