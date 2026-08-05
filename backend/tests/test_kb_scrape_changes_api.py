"""Full-app TestClient tests for the scrape change queue.

The bug these pin: the queue was scoped to the latest run, so a scrape that
found nothing became "latest" and emptied the panel -- while approvable drafts
from earlier runs sat unreviewed in the database and the admin tree's badges
still counted them. The panel and the tree read the same rows and disagreed,
and the tree was the one telling the truth.
"""
import os

os.environ["TRUSTED_HOSTS"] = "testserver,localhost,127.0.0.1"

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import deps
import main
from db import Base
from models import ScrapeChange, ScrapeRun


@pytest.fixture
def ctx():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    seed = TestingSession()
    now = datetime.now(timezone.utc)

    # Run 1: found real work, left unreviewed.
    old = ScrapeRun(status="succeeded", created_at=now - timedelta(hours=2),
                    started_at=now - timedelta(hours=2),
                    finished_at=now - timedelta(hours=2))
    seed.add(old)
    seed.commit()
    seed.add(ScrapeChange(
        run_id=old.id, url="https://www.morgan.edu/ora/a", page_title="Page A",
        change_type="modified", status="pending", doc_id="doc_a",
        previous_content="old", new_content="new",
    ))
    seed.add(ScrapeChange(
        run_id=old.id, url="https://www.morgan.edu/ora/b", page_title="Page B",
        change_type="unreadable", status="skipped",
    ))

    # Run 2: the quiet run that must not bury run 1.
    new = ScrapeRun(status="succeeded", created_at=now, started_at=now, finished_at=now)
    seed.add(new)
    seed.commit()
    seed.add(ScrapeChange(
        run_id=new.id, url="https://www.morgan.edu/ora/c", page_title="Page C",
        change_type="modified", status="cosmetic",
    ))
    seed.commit()
    old_id, new_id = old.id, new.id
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
        "user_id": 1, "email": "admin@morgan.edu", "role": "admin",
    }
    c = TestClient(main.app)
    yield c, old_id, new_id
    main.app.dependency_overrides.clear()


def test_a_later_quiet_run_does_not_bury_an_unreviewed_proposal(ctx):
    c, old_id, new_id = ctx
    r = c.get("/api/admin/kb-scrape/changes")
    assert r.status_code == 200
    body = r.json()

    titles = [ch["page_title"] for ch in body["changes"]]
    assert "Page A" in titles, "the unreviewed proposal from the earlier run vanished"
    assert body["counts"].get("pending") == 1


def test_the_latest_runs_own_rows_are_still_included(ctx):
    c, old_id, new_id = ctx
    body = c.get("/api/admin/kb-scrape/changes").json()
    assert body["run_id"] == new_id
    assert "Page C" in [ch["page_title"] for ch in body["changes"]]


def test_settled_rows_from_older_runs_are_not_dragged_in(ctx):
    """Only outstanding work carries over — a handled row stays with its run."""
    c, old_id, new_id = ctx
    titles = [ch["page_title"] for ch in c.get("/api/admin/kb-scrape/changes").json()["changes"]]
    assert "Page B" not in titles


def test_asking_for_one_run_still_returns_exactly_that_run(ctx):
    c, old_id, new_id = ctx
    body = c.get(f"/api/admin/kb-scrape/changes?run_id={old_id}").json()
    titles = [ch["page_title"] for ch in body["changes"]]
    assert titles and "Page C" not in titles
    assert body["run_id"] == old_id


def test_pending_sorts_ahead_of_everything_else(ctx):
    c, old_id, new_id = ctx
    statuses = [ch["status"] for ch in c.get("/api/admin/kb-scrape/changes").json()["changes"]]
    assert statuses[0] == "pending"


def test_a_non_admin_is_refused(ctx):
    c, old_id, new_id = ctx
    main.app.dependency_overrides[main.get_current_user] = lambda: {
        "user_id": 2, "email": "pi@morgan.edu", "role": "user",
    }
    assert c.get("/api/admin/kb-scrape/changes").status_code == 403
