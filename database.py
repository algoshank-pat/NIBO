"""SQLite persistence: applications, audit_logs (append-only) and versioned rules.

Run `python database.py` to create the schema and seed the rules table.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from config import settings
from models import (AuditLog, CompletionProposal, DecisionRequest, RuleDefinition,
                    SystemExecution, UnderwriterDecision, utcnow)

SCHEMA = """
CREATE TABLE IF NOT EXISTS applications (
    application_id   TEXT PRIMARY KEY,
    policy_product   TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'PENDING',   -- PENDING | PROPOSED | NIBO | READY
    proposed_status  TEXT,
    confidence_score REAL,
    latest_proposal  TEXT,                              -- CompletionProposal JSON
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id                   TEXT PRIMARY KEY,
    application_id       TEXT NOT NULL REFERENCES applications(application_id),
    timestamp            TEXT NOT NULL,
    event_type           TEXT NOT NULL,                 -- agent_proposal | underwriter_decision | system_execution
    agent_proposal       TEXT,
    underwriter_decision TEXT,
    system_execution     TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_app ON audit_logs(application_id, timestamp);

-- Audit rows are immutable.
CREATE TRIGGER IF NOT EXISTS audit_logs_no_update BEFORE UPDATE ON audit_logs
BEGIN SELECT RAISE(ABORT, 'audit_logs is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_logs_no_delete BEFORE DELETE ON audit_logs
BEGIN SELECT RAISE(ABORT, 'audit_logs is append-only'); END;

CREATE TABLE IF NOT EXISTS rules (
    rule_id            TEXT NOT NULL,
    version            TEXT NOT NULL,
    policy_product     TEXT NOT NULL,
    description        TEXT NOT NULL,
    severity           TEXT NOT NULL,
    error_message      TEXT NOT NULL,
    required_documents TEXT NOT NULL,                   -- JSON list
    enabled            INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (rule_id, version, policy_product)
);
"""


@contextmanager
def connect(db_path: Path | None = None):
    conn = sqlite3.connect(db_path or settings.database_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(db_path: Path | None = None) -> None:
    """Create tables and (re)seed the rules table from rules.py for the current version."""
    from rules import PRODUCT_RULES, RULES

    with connect(db_path) as conn:
        conn.executescript(SCHEMA)
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(applications)")}
        for name in ("final_issues", "stored_letter_text"):
            if name not in columns:
                conn.execute(f"ALTER TABLE applications ADD COLUMN {name} TEXT")
        for product, rule_ids in PRODUCT_RULES.items():
            for rid in rule_ids:
                d = RULES[rid].definition
                conn.execute(
                    """INSERT INTO rules (rule_id, version, policy_product, description, severity,
                                          error_message, required_documents)
                       VALUES (?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(rule_id, version, policy_product) DO UPDATE SET
                         description = excluded.description, severity = excluded.severity,
                         error_message = excluded.error_message,
                         required_documents = excluded.required_documents""",
                    (d.id, d.version, product, d.description, d.severity, d.error_message,
                     json.dumps([t.value for t in d.required_documents])),
                )


def get_rules_for_product(product: str, version: str | None = None, db_path: Path | None = None) -> list[RuleDefinition]:
    version = version or settings.rules_version
    with connect(db_path) as conn:
        rows = conn.execute(
            """SELECT * FROM rules WHERE policy_product = ? AND version = ? AND enabled = 1
               ORDER BY rowid""",
            (product, version),
        ).fetchall()
    return [
        RuleDefinition(
            id=r["rule_id"], description=r["description"], severity=r["severity"],
            error_message=r["error_message"], version=r["version"],
            required_documents=json.loads(r["required_documents"]),
        )
        for r in rows
    ]


def known_products(db_path: Path | None = None) -> list[str]:
    with connect(db_path) as conn:
        return [r[0] for r in conn.execute("SELECT DISTINCT policy_product FROM rules ORDER BY 1")]


def upsert_application(application_id: str, policy_product: str, db_path: Path | None = None) -> None:
    now = utcnow().isoformat()
    with connect(db_path) as conn:
        conn.execute(
            """INSERT INTO applications (application_id, policy_product, created_at, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(application_id) DO UPDATE SET
                 policy_product = excluded.policy_product, updated_at = excluded.updated_at""",
            (application_id, policy_product, now, now),
        )


def save_proposal(proposal: CompletionProposal, policy_product: str, db_path: Path | None = None) -> AuditLog:
    """Store the agent's proposal on the application and append an `agent_proposal` audit entry.

    The application status becomes PROPOSED - only an underwriter decision can set NIBO/READY.
    """
    upsert_application(proposal.application_id, policy_product, db_path)
    entry = AuditLog(application_id=proposal.application_id, event_type="agent_proposal",
                     agent_proposal=proposal)
    with connect(db_path) as conn:
        conn.execute(
            """UPDATE applications SET status = 'PROPOSED', proposed_status = ?, confidence_score = ?,
                   latest_proposal = ?, updated_at = ?, final_issues = NULL, stored_letter_text = NULL WHERE application_id = ?""",
            (proposal.proposed_status, proposal.confidence_score, proposal.model_dump_json(),
             utcnow().isoformat(), proposal.application_id),
        )
        _insert_audit(conn, entry)
    return entry


class DecisionConflict(ValueError):
    pass


def save_decision(application_id: str, request: DecisionRequest, db_path: Path | None = None) -> dict:
    from letters import letter_text

    with connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM applications WHERE application_id = ?", (application_id,)).fetchone()
        if row is None:
            raise KeyError(application_id)
        if row["status"] != "PROPOSED" or not row["latest_proposal"]:
            raise DecisionConflict("This application has no pending proposal; refresh before deciding")
        proposal = CompletionProposal.model_validate_json(row["latest_proposal"])
        if request.expected_proposal_timestamp != proposal.agent_timestamp:
            raise DecisionConflict("The proposal has changed; refresh and review the latest evidence")
        final_status = "READY" if request.action == "reject" else proposal.proposed_status
        issues = proposal.issues_detail
        if request.action == "modify":
            final_status, issues = request.final_status, request.modified_issues
        elif request.final_status is not None and request.final_status != final_status:
            raise ValueError("Approve accepts the proposed status; use modify for an override")
        decision = UnderwriterDecision(decided_by=request.decided_by.strip(), action=request.action,
            final_status=final_status, notes=request.notes, final_issues=issues)
        stored_letter = ""
        if final_status == "NIBO":
            if request.action == "approve":
                stored_letter = proposal.recommended_letter_text
            else:
                name = next((d.fields.get("applicant_name") for d in
                    proposal.evidence_snapshot.extracted_documents.values()
                    if d.fields.get("applicant_name")), "Applicant")
                stored_letter = letter_text(application_id, name, [i.issue for i in issues])
        execution = SystemExecution(status_updated=True, letter_generated=bool(stored_letter),
                                    stored_letter_text=stored_letter or None)
        conn.execute("""UPDATE applications SET status = ?, final_issues = ?, stored_letter_text = ?,
                        updated_at = ? WHERE application_id = ?""",
                     (final_status, json.dumps([i.model_dump() for i in issues]), stored_letter,
                      utcnow().isoformat(), application_id))
        _insert_audit(conn, AuditLog(application_id=application_id, event_type="underwriter_decision",
                                    underwriter_decision=decision))
        _insert_audit(conn, AuditLog(application_id=application_id, event_type="system_execution",
                                    system_execution=execution))
    return {"application_id": application_id, "status": final_status,
            "decision": decision.model_dump(mode="json"), "execution": execution.model_dump(mode="json"),
            "stored_letter_text": stored_letter}


def append_audit(entry: AuditLog, db_path: Path | None = None) -> AuditLog:
    with connect(db_path) as conn:
        _insert_audit(conn, entry)
    return entry


def _insert_audit(conn: sqlite3.Connection, entry: AuditLog) -> None:
    dump = lambda m: m.model_dump_json() if m else None  # noqa: E731
    conn.execute(
        """INSERT INTO audit_logs (id, application_id, timestamp, event_type, agent_proposal,
                                   underwriter_decision, system_execution)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (entry.id, entry.application_id, entry.timestamp.isoformat(), entry.event_type,
         dump(entry.agent_proposal), dump(entry.underwriter_decision), dump(entry.system_execution)),
    )


def get_audit_log(application_id: str, db_path: Path | None = None) -> list[AuditLog]:
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM audit_logs WHERE application_id = ? ORDER BY timestamp, rowid",
            (application_id,),
        ).fetchall()
    return [
        AuditLog(
            id=r["id"], application_id=r["application_id"], timestamp=r["timestamp"],
            event_type=r["event_type"],
            agent_proposal=json.loads(r["agent_proposal"]) if r["agent_proposal"] else None,
            underwriter_decision=json.loads(r["underwriter_decision"]) if r["underwriter_decision"] else None,
            system_execution=json.loads(r["system_execution"]) if r["system_execution"] else None,
        )
        for r in rows
    ]


def get_application(application_id: str, db_path: Path | None = None) -> dict | None:
    with connect(db_path) as conn:
        row = conn.execute("SELECT * FROM applications WHERE application_id = ?", (application_id,)).fetchone()
    return dict(row) if row else None


if __name__ == "__main__":
    init_db()
    print(f"Initialised {settings.database_path}")
    for p in known_products():
        print(f"  {p}: {[r.id for r in get_rules_for_product(p)]}")
