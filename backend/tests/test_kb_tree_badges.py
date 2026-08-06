"""The tree's amber ⚠ badge must agree with the review panel.

The bug this pins: the badge counted every pending proposal with a doc_id,
while the panel lists only those carrying a draft (`pending && has_diff`).
After the "Review by hand" group was removed from the UI, a draftless proposal
became invisible in the panel but still badged the document — so an admin who
approved everything on offer was left with an amber warning and no way to clear
it. A badge you cannot act on is a dead end, not information.
"""
import os

os.environ["TRUSTED_HOSTS"] = "testserver,localhost,127.0.0.1"

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import kb_scrape_service as scrape
from db import Base
from models import ScrapeChange, ScrapeRun


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    now = datetime.now(timezone.utc)
    run = ScrapeRun(status="succeeded", created_at=now, started_at=now, finished_at=now)
    s.add(run)
    s.commit()

    # Approvable: has a drafted replacement.
    s.add(ScrapeChange(run_id=run.id, url="https://x/a", doc_id="doc_with_draft",
                       change_type="modified", status="pending",
                       previous_content="old", new_content="new"))
    # Not approvable: the model could not ground a replacement, so no draft.
    s.add(ScrapeChange(run_id=run.id, url="https://x/b", doc_id="doc_no_draft",
                       change_type="modified", status="pending",
                       what_changed="This page changed. No draft was produced."))
    # Empty-string draft is the same thing as none.
    s.add(ScrapeChange(run_id=run.id, url="https://x/c", doc_id="doc_empty_draft",
                       change_type="modified", status="pending",
                       previous_content="old", new_content=""))
    s.commit()
    yield s
    s.close()


def test_badge_counts_only_proposals_an_admin_can_act_on(db):
    badged = scrape.pending_by_doc(db)
    assert "doc_with_draft" in badged
    assert "doc_no_draft" not in badged, "badged a document with no draft to approve"
    assert "doc_empty_draft" not in badged, "an empty draft is not a draft"


def test_the_badge_carries_what_the_panel_needs(db):
    row = scrape.pending_by_doc(db)["doc_with_draft"]
    assert row["has_draft"] is True
    assert row["url"] == "https://x/a"


def test_an_approved_proposal_clears_the_badge(db):
    change = db.query(ScrapeChange).filter(ScrapeChange.doc_id == "doc_with_draft").first()
    change.status = "approved"
    db.commit()
    assert scrape.pending_by_doc(db) == {}


# ---------------------------------------------------------------------------
# Draftless proposals accumulate every run — 90 after three — and there was no
# way to clear them once "Review by hand" left the UI.
# ---------------------------------------------------------------------------

def test_dismiss_reported_clears_only_the_draftless(db):
    result = scrape.dismiss_reported(db, user_id=1)
    assert result["success"] and result["dismissed"] == 2

    left = {c.doc_id: c.status for c in db.query(ScrapeChange).all()}
    assert left["doc_with_draft"] == "pending", "a real decision was dismissed"
    assert left["doc_no_draft"] == "rejected"
    assert left["doc_empty_draft"] == "rejected"


def test_dismiss_reported_is_safe_to_run_twice(db):
    scrape.dismiss_reported(db, user_id=1)
    assert scrape.dismiss_reported(db, user_id=1)["dismissed"] == 0
