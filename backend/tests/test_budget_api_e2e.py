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


# ---------------------------------------------------------------------------
# NSF Form 1030 template endpoints
# ---------------------------------------------------------------------------

def test_nsf_template_endpoint_returns_a_blank_document(ctx):
    c, _ = ctx
    r = c.get("/api/budget/nsf/template?years=3")
    assert r.status_code == 200
    doc = r.json()["document"]
    assert doc["schema"] == "nsf_1030"
    assert len(doc["years"]) == 3


def test_nsf_compute_endpoint_returns_years_cumulative_and_flags(ctx):
    c, _ = ctx
    doc = c.get("/api/budget/nsf/template").json()["document"]
    doc["years"][0]["other_direct"]["materials_supplies"] = [
        {"description": "Reagents", "amount": 10_000}]
    r = c.post("/api/budget/nsf/compute", json=doc)
    assert r.status_code == 200
    body = r.json()
    assert body["years"][0]["lines"]["I"] == 5_400.0
    assert "cumulative" in body and "flags" in body


def test_nsf_add_year_endpoint_escalates_salaries(ctx):
    c, _ = ctx
    doc = c.get("/api/budget/nsf/template").json()["document"]
    doc["years"][0]["senior"][0]["base_salary"] = 100_000
    r = c.post("/api/budget/nsf/add-year", json={"inputs": doc})
    assert r.status_code == 200
    out = r.json()["document"]
    assert len(out["years"]) == 2
    assert out["years"][1]["senior"][0]["base_salary"] == 103_000.0


def test_nsf_justification_endpoint_returns_text(ctx):
    c, _ = ctx
    doc = c.get("/api/budget/nsf/template").json()["document"]
    doc["years"][0]["senior"][0].update(name="Dr. Oladunni", base_salary=90_000,
                                        appointment_basis="academic_9", acad=2)
    r = c.post("/api/budget/nsf/justification", json={"inputs": doc, "use_ai": False})
    assert r.status_code == 200
    assert "Dr. Oladunni" in r.json()["justification"]


# --- persistence ----------------------------------------------------------

def test_saving_and_loading_an_nsf_budget_round_trips(ctx):
    c, sub_id = ctx
    doc = c.get("/api/budget/nsf/template").json()["document"]
    doc["years"][0]["equipment"] = [{"description": "Confocal", "amount": 40_000}]
    assert c.put(f"/api/me/submissions/{sub_id}/budget",
                 json={"inputs": doc}).status_code == 200
    body = c.get(f"/api/me/submissions/{sub_id}/budget").json()
    assert body["schema"] == "nsf_1030"
    assert body["inputs"]["years"][0]["equipment"][0]["amount"] == 40_000
    assert body["computed"]["years"][0]["lines"]["D"]["total"] == 40_000.0


def test_a_generic_budget_with_no_schema_key_still_loads(ctx):
    """THE regression guard. Existing saved budgets must not change behaviour."""
    c, sub_id = ctx
    assert c.put(f"/api/me/submissions/{sub_id}/budget",
                 json={"inputs": WORKED}).status_code == 200
    body = c.get(f"/api/me/submissions/{sub_id}/budget").json()
    assert body.get("schema") in (None, "generic")
    assert body["computed"]["total"] > 0          # the generic response shape
    assert "personnel" in body["computed"]


# --- export ---------------------------------------------------------------

def test_xlsx_download_returns_a_workbook(ctx):
    c, sub_id = ctx
    doc = c.get("/api/budget/nsf/template").json()["document"]
    c.put(f"/api/me/submissions/{sub_id}/budget", json={"inputs": doc})
    r = c.get(f"/api/me/submissions/{sub_id}/budget.xlsx")
    assert r.status_code == 200
    assert r.content[:2] == b"PK"                 # a zip container, i.e. xlsx
    assert "spreadsheetml" in r.headers["content-type"]
    assert "attachment" in r.headers["content-disposition"]


def test_xlsx_download_refuses_a_generic_budget(ctx):
    """There is no Form 1030 to export from a generic budget -- say so, don't 500."""
    c, sub_id = ctx
    c.put(f"/api/me/submissions/{sub_id}/budget", json={"inputs": WORKED})
    r = c.get(f"/api/me/submissions/{sub_id}/budget.xlsx")
    assert r.status_code == 400
