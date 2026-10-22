import sqlite3

import pytest

import database as db
from models import AuditLog, EvidenceSnapshot, CompletionProposal


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "test.db"
    db.init_db(path)
    return path


def test_rules_seeded_per_product(db_path):
    rules = db.get_rules_for_product("TermLife_10yr", db_path=db_path)
    assert [r.id for r in rules] == ["required_fields", "signature_present", "date_consistency",
                                     "medical_recency", "beneficiary_designated", "id_valid"]
    assert "UniversalLife" in db.known_products(db_path)
    db.init_db(db_path)  # idempotent
    assert len(db.get_rules_for_product("TermLife_10yr", db_path=db_path)) == 6


def _proposal(app_id="APP_1"):
    return CompletionProposal(
        application_id=app_id, proposed_status="NIBO", confidence_score=0.9, issues_summary="x",
        issues_detail=[], reasoning="r", next_steps=[], recommended_letter_text="",
        evidence_snapshot=EvidenceSnapshot(extracted_documents={}, rules_checked=[], rules_version="1.0.0"),
        agent_mode="mock",
    )


def test_save_proposal_writes_audit_and_marks_proposed(db_path):
    db.save_proposal(_proposal(), "TermLife_10yr", db_path)
    app = db.get_application("APP_1", db_path)
    assert app["status"] == "PROPOSED" and app["proposed_status"] == "NIBO"
    log = db.get_audit_log("APP_1", db_path)
    assert len(log) == 1 and log[0].event_type == "agent_proposal"
    assert log[0].agent_proposal.confidence_score == 0.9


def test_audit_log_is_append_only(db_path):
    entry = db.save_proposal(_proposal(), "TermLife_10yr", db_path)
    with db.connect(db_path) as conn, pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("DELETE FROM audit_logs WHERE id = ?", (entry.id,))
    with db.connect(db_path) as conn, pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("UPDATE audit_logs SET event_type = 'x' WHERE id = ?", (entry.id,))
