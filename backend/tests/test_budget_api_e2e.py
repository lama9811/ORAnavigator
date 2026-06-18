"""Full-app TestClient tests for the Budget Helper endpoints — the route ->
dependency -> response cycle (compute, rates, justification, save/load).

Self-contained env so it runs standalone (importing `main` connects to the DB
on startup; SQLite keeps that fast and offline)."""
import os
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("TRUSTED_HOSTS", "testserver,localhost,127.0.0.1")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

import main
import deps
from db import Base
from models import User, Submission
from security import hash_password


# The hand-verified worked example (matches test_budget_helper.py).
WORKED = {
    "people": [{"name": "Dr. Smith", "base_salary": 80_000, "effort_pct": 25, "fringe": "faculty_ay"}],
    "equipment": 40_000, "travel": 3_000, "supplies": 5_000,
    "participant_support": 2_000, "subawards": [50_000],
    "fa_rate_key": "organized_research_on_campus", "fa_year": "fy_2025_2026",
}


@pytest.fixture
def ctx():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    seed = TestingSession()
    u = User(email="pi@morgan.edu", password_hash=hash_password("password123"),
             role="user", name="Pat Investigator")
    seed.add(u)
    seed.commit()
    uid = u.id
    sub = Submission(user_id=uid, title="NSF CAREER", sponsor="NSF", status="active")
    seed.add(sub)
    seed.commit()
    sub_id = sub.id
    seed.close()

    def _override_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    main.app.dependency_overrides[main.get_db] = _override_db
    main.app.dependency_overrides[deps.get_db] = _override_db
    main.app.dependency_overrides[main.get_current_user] = lambda: {
        "user_id": uid, "email": "pi@morgan.edu", "role": "user",
    }
    c = TestClient(main.app)
    yield c, sub_id
    main.app.dependency_overrides.clear()


def test_rates_endpoint_exposes_fa_and_fringe(ctx):
    c, _ = ctx
    r = c.get("/api/budget/rates")
    assert r.status_code == 200
    body = r.json()
    assert "fa_rates" in body and "fringe_rates" in body
    assert body["defaults"]["fa_rate_key"] == "organized_research_on_campus"
    # Organized Research on-campus FY25-26 is 54%
    fy = {opt["key"]: opt["rate"] for opt in body["fa_rates"]["fy_2025_2026"]}
    assert fy["organized_research_on_campus"] == 0.54


def test_compute_endpoint_returns_deterministic_total(ctx):
    c, _ = ctx
    r = c.post("/api/budget/compute", json=WORKED)
    assert r.status_code == 200
    b = r.json()
    assert b["direct_costs"] == 128_400.0
    assert b["mtdc_base"] == 61_400.0
    assert b["fa_amount"] == 33_156.0
    assert b["total"] == 161_556.0


def test_justification_template_contains_the_figures(ctx):
    c, _ = ctx
    r = c.post("/api/budget/justification", json={**WORKED, "use_ai": False})
    assert r.status_code == 200
    body = r.json()
    assert body["ai"] is False
    text = body["justification"]
    assert "$161,556" in text          # total
    assert "54%" in text               # F&A rate
    assert "Dr. Smith" in text


def test_truncated_ai_justification_falls_back_to_template(ctx, monkeypatch):
    """Gemini under load can return a non-empty but TRUNCATED fragment. The
    endpoint must detect it (missing total figure) and serve the complete
    deterministic template instead of the half-sentence."""
    from services import gemini_client
    monkeypatch.setattr(
        gemini_client, "generate_text",
        lambda *a, **k: "Personnel: Dr. Smith will commit 25% effort. The requested salary is",
    )
    c, _ = ctx
    r = c.post("/api/budget/justification", json={**WORKED, "use_ai": True})
    assert r.status_code == 200
    body = r.json()
    # Truncated fragment rejected -> deterministic template served.
    assert body["ai"] is False
    assert "$161,556" in body["justification"]   # complete: states the total


def test_complete_ai_justification_is_kept(ctx, monkeypatch):
    """A complete AI rewrite (contains the total) is served as-is."""
    from services import gemini_client
    good = ("Dr. Smith commits 25% effort... F&A at 54%... "
            "The total project cost is $161,556.")
    monkeypatch.setattr(gemini_client, "generate_text", lambda *a, **k: good)
    c, _ = ctx
    r = c.post("/api/budget/justification", json={**WORKED, "use_ai": True})
    assert r.status_code == 200
    body = r.json()
    assert body["ai"] is True
    assert body["justification"] == good


def test_save_then_load_budget_roundtrip(ctx):
    c, sub_id = ctx
    # Save
    r = c.put(f"/api/me/submissions/{sub_id}/budget", json={"inputs": WORKED})
    assert r.status_code == 200
    assert r.json()["computed"]["total"] == 161_556.0
    # Load — inputs persisted, recomputed fresh
    r2 = c.get(f"/api/me/submissions/{sub_id}/budget")
    assert r2.status_code == 200
    assert r2.json()["inputs"]["equipment"] == 40_000
    assert r2.json()["computed"]["total"] == 161_556.0
    # Detail view now flags the budget
    r3 = c.get(f"/api/me/submissions/{sub_id}")
    assert r3.json()["has_budget"] is True
    assert r3.json()["budget"]["supplies"] == 5_000


def test_budget_for_other_users_submission_is_404(ctx):
    c, _ = ctx
    r = c.get("/api/me/submissions/99999/budget")
    assert r.status_code == 404
