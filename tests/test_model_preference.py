"""Model preference system tests (2026-09-22).

doc/model-preference-design.md (ratified), pinned here:

Resolution (two-layer, the 13d-lite/limits-registry pattern):
  1. user override (users.llm_model_pref) — honored ONLY if in the
     catalog (LLM_MODEL_CATALOG)
  2. tier default — LLM_MODEL_ADMIN_DEFAULT (glm-5.2) /
     LLM_MODEL_PAID_DEFAULT (minimax-m3)
  3. legacy fallback — llm_model_ollama (for unknown roles; fail-safe)

Also pinned:
  - The catalog parsing (comma-separated, whitespace-forgiving)
  - The settings page: admin-gated, catalog rendering, round-trip
    save, unknown-model 400
  - FREE never touches the ollama branch (groq-only chain)
"""

import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

FAKE_ADMIN = {"uid": "admin-uid", "email": "admin@example.com", "role": 0}
FAKE_PAID = {"uid": "paid-uid", "email": "paid@example.com", "role": 1}


def _admin_auth():
    return patch("app.auth.dependencies.verify_token", return_value=FAKE_ADMIN)


def _paid_auth():
    return patch("app.auth.dependencies.verify_token", return_value=FAKE_PAID)


def _seed_roles(db: Session) -> None:
    from app.auth.admin import clear_role_cache

    db.execute(text(
        "INSERT OR REPLACE INTO users (user_id, email, role) "
        "VALUES ('admin-uid', 'admin@example.com', 0)"))
    db.execute(text(
        "INSERT OR REPLACE INTO users (user_id, email, role) "
        "VALUES ('paid-uid', 'paid@example.com', 1)"))
    db.commit()
    clear_role_cache()


def _set_pref(db: Session, uid: str, model: str | None) -> None:
    """Directly set a user's llm_model_pref (the resolver's input)."""
    db.execute(text(
        "UPDATE users SET llm_model_pref = :m WHERE user_id = :u"),
        {"m": model, "u": uid},
    )
    db.commit()


def _mock_llm_ok(result_box):
    """Mock litellm.completion that records the model it was called with."""
    def fake_completion(*, model, **kwargs):
        result_box.append(model)
        class R:
            class choices:
                class message:
                    content = "ok"
        r = R()
        r.choices = [type("C", (), {"message": type("M", (), {"content": "ok"})})()]
        return r
    return fake_completion


# ─────────────────────────────────────────────────────────────────────────────
# 1. Config helpers
# ─────────────────────────────────────────────────────────────────────────────

def test_catalog_parsing():
    from app.config import settings

    catalog = settings.get_model_catalog()
    assert catalog == ["glm-5.2:cloud", "minimax-m3:cloud", "glm-5.3:cloud"]
    # Ratified defaults
    assert settings.llm_model_paid_default == "minimax-m3:cloud"
    assert settings.llm_model_admin_default == "glm-5.2:cloud"


def test_catalog_whitespace_forgiving(monkeypatch):
    """Hand-edited .env values with stray spaces still parse."""
    from app.config import settings

    monkeypatch.setattr(
        settings, "llm_model_catalog",
        "glm-5.2:cloud, minimax-m3:cloud , ,glm-5.3:cloud",
    )
    assert settings.get_model_catalog() == [
        "glm-5.2:cloud", "minimax-m3:cloud", "glm-5.3:cloud",
    ]


def test_tier_default_resolver():
    from app.config import settings

    assert settings.get_tier_default_model(0) == "glm-5.2:cloud"    # ADMIN
    assert settings.get_tier_default_model(1) == "minimax-m3:cloud"  # PAID
    # Unknown role → the LEGACY model, fail-safe (never the paid default)
    assert settings.get_tier_default_model(99) == settings.llm_model_ollama


# ─────────────────────────────────────────────────────────────────────────────
# 2. The resolver (call_llm_with_fallback's ollama branch)
# ─────────────────────────────────────────────────────────────────────────────

def _call_paid(db: Session) -> list[str]:
    """Make a PAID-tier call and return the models litellm was invoked with."""
    from app.services.llm_providers import call_llm_with_fallback

    os.environ["OLLAMA_API_KEY"] = "test-ollama"
    os.environ["OPENAI_API_KEY"] = "test-openai"
    models: list[str] = []

    class FakeMsg:
        content = "ok"

    class FakeChoice:
        message = FakeMsg()

    class FakeResp:
        choices = [FakeChoice()]

    with patch("app.services.llm_providers.litellm.completion",
               side_effect=lambda **kw: (models.append(kw["model"]), FakeResp())[1]):
        call_llm_with_fallback(
            messages=[{"role": "user", "content": "hi"}],
            user_role=1,
            user_id="paid-uid",
        )
    return models


def test_paid_default_is_minimax(client: TestClient, db_session):
    _seed_roles(db_session)
    # No override → tier default
    _set_pref(db_session, "paid-uid", None)
    models = _call_paid(db_session)
    assert models[0] == "ollama/minimax-m3:cloud"


def test_paid_override_in_catalog_wins(client: TestClient, db_session):
    _seed_roles(db_session)
    _set_pref(db_session, "paid-uid", "glm-5.3:cloud")
    models = _call_paid(db_session)
    assert models[0] == "ollama/glm-5.3:cloud"


def test_override_not_in_catalog_falls_back(client: TestClient, db_session):
    """A pref removed from the catalog (retired model) silently falls
    back to the tier default — graceful retirement, no 500."""
    _seed_roles(db_session)
    _set_pref(db_session, "paid-uid", "glm-4.0:retired")
    models = _call_paid(db_session)
    assert models[0] == "ollama/minimax-m3:cloud"


def test_admin_default_is_glm52(client: TestClient, db_session):
    from app.services.llm_providers import call_llm_with_fallback

    _seed_roles(db_session)
    os.environ["OLLAMA_API_KEY"] = "test-ollama"
    os.environ["OPENAI_API_KEY"] = "test-openai"
    models: list[str] = []

    class FakeMsg:
        content = "ok"

    class FakeChoice:
        message = FakeMsg()

    class FakeResp:
        choices = [FakeChoice()]

    with patch("app.services.llm_providers.litellm.completion",
               side_effect=lambda **kw: (models.append(kw["model"]), FakeResp())[1]):
        call_llm_with_fallback(
            messages=[{"role": "user", "content": "hi"}],
            user_role=0,
            user_id="admin-uid",
        )
    assert models[0] == "ollama/glm-5.2:cloud"


def test_free_never_uses_ollama(client: TestClient, db_session):
    """FREE chain is groq-only — the ollama branch (and any pref
    lookup) must never fire for FREE."""
    from app.services.llm_providers import call_llm_with_fallback

    os.environ["GROQ_API_KEY"] = "test-groq"
    models: list[str] = []

    class FakeMsg:
        content = "ok"

    class FakeChoice:
        message = FakeMsg()

    class FakeResp:
        choices = [FakeChoice()]

    with patch("app.services.llm_providers.litellm.completion",
               side_effect=lambda **kw: (models.append(kw["model"]), FakeResp())[1]):
        result = call_llm_with_fallback(
            messages=[{"role": "user", "content": "hi"}],
            user_role=2,
            user_id="free-uid",
        )
    assert result["status"] == "ok"
    assert models[0].startswith("groq/")
    assert not any(m.startswith("ollama/") for m in models)


# ─────────────────────────────────────────────────────────────────────────────
# 3. The settings page
# ─────────────────────────────────────────────────────────────────────────────

def test_settings_page_admin_gate(paid_and_admin_clients, db_session):
    """PAID → 403 (the gate is the admin capability dep); anonymous
    would 401/redirect — not tested here."""
    paid_client, admin_client = paid_and_admin_clients
    _seed_roles(db_session)

    with _paid_auth():
        r = paid_client.get("/admin/settings")
    assert r.status_code == 403


def test_settings_page_renders_catalog(paid_and_admin_clients, db_session):
    paid_client, admin_client = paid_and_admin_clients
    _seed_roles(db_session)

    with _admin_auth():
        r = admin_client.get("/admin/settings")
    assert r.status_code == 200
    for m in ("glm-5.2:cloud", "minimax-m3:cloud", "glm-5.3:cloud"):
        assert m in r.text
    # The paid default is surfaced so the admin always knows what
    # non-admin users are getting
    assert "minimax-m3:cloud" in r.text


def test_settings_save_round_trip(paid_and_admin_clients, db_session):
    """Save → redirect → the row holds the choice → GET shows it checked."""
    paid_client, admin_client = paid_and_admin_clients
    _seed_roles(db_session)

    with _admin_auth():
        r = admin_client.post(
            "/admin/settings",
            data={"llm_model_pref": "minimax-m3:cloud"},
            follow_redirects=False,
        )
    assert r.status_code == 303
    assert r.headers["location"].endswith("?saved=1")

    row = db_session.execute(text(
        "SELECT llm_model_pref FROM users WHERE user_id = 'admin-uid'"
    )).fetchone()
    assert row[0] == "minimax-m3:cloud"

    with _admin_auth():
        r2 = admin_client.get("/admin/settings?saved=1")
    assert r2.status_code == 200
    assert "Saved" in r2.text
    # The checked radio is the saved one (whitespace-robust: the
    # input tag spans lines, value="..." then the checked attr)
    import re as _re
    assert _re.search(
        r'value="minimax-m3:cloud"[^>]*checked', r2.text
    ), "the saved model must be the checked radio"


def test_settings_rejects_unknown_model(paid_and_admin_clients, db_session):
    """A hand-crafted POST with a model not in the catalog → 400 with
    the valid options echoed (typo guard)."""
    paid_client, admin_client = paid_and_admin_clients
    _seed_roles(db_session)

    with _admin_auth():
        r = admin_client.post(
            "/admin/settings",
            data={"llm_model_pref": "gpt-not-real"},
        )
    assert r.status_code == 400
    assert "Unknown model" in r.text
    assert "glm-5.2:cloud" in r.text  # catalog echoed
    # And the row was NOT written
    row = db_session.execute(text(
        "SELECT llm_model_pref FROM users WHERE user_id = 'admin-uid'"
    )).fetchone()
    assert row[0] is None


def test_settings_save_empty_resets_to_default(paid_and_admin_clients, db_session):
    """Saving an empty choice clears the override (tier default again)."""
    paid_client, admin_client = paid_and_admin_clients
    _seed_roles(db_session)
    _set_pref(db_session, "admin-uid", "glm-5.3:cloud")

    with _admin_auth():
        r = admin_client.post(
            "/admin/settings",
            data={"llm_model_pref": ""},
            follow_redirects=False,
        )
    assert r.status_code == 303
    row = db_session.execute(text(
        "SELECT llm_model_pref FROM users WHERE user_id = 'admin-uid'"
    )).fetchone()
    assert row[0] is None