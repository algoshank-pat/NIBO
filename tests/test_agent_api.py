import base64

import pytest
from fastapi.testclient import TestClient

import database as db
import sample_applications as samples
from agent import analyze_application
from config import settings
from extractors import MockExtractor


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_path", tmp_path / "test.db")
    monkeypatch.setattr(settings, "llm_mode", "mock")
    monkeypatch.setattr(samples, "SAMPLE_DIR", tmp_path / "samples")
    db.init_db()


@pytest.fixture
def packets():
    return samples.generate()


@pytest.fixture
def client():
    from app import app
    with TestClient(app) as c:
        yield c


@pytest.mark.parametrize("workers", [1, 4])
def test_three_samples_produce_expected_status(packets, monkeypatch, workers):
    monkeypatch.setattr(settings, "extraction_workers", workers)
    results = {k: analyze_application(r, extractor=MockExtractor())[0] for k, r in packets.items()}

    nibo = results["APP_1001"]
    assert nibo.proposed_status == "NIBO" and nibo.alternative_status is None
    assert [d.rule for d in nibo.issues_detail] == ["beneficiary_designated"]
    assert nibo.issues_detail[0].severity == "critical"
    assert "Beneficiary Name" in nibo.issues_detail[0].evidence
    assert "Dear Jane Doe" in nibo.recommended_letter_text

    ready = results["APP_1002"]
    assert ready.proposed_status == "READY" and ready.issues_detail == []
    assert ready.recommended_letter_text == ""
    assert ready.confidence_score >= 0.9

    edge = results["APP_1003"]
    assert edge.proposed_status == "NIBO" and edge.alternative_status == "READY"
    assert edge.requires_underwriter_judgment
    assert [d.rule for d in edge.issues_detail] == ["medical_recency"]
    assert edge.issues_detail[0].severity == "major"
    assert any("scanned_attachment.pdf" in s for s in edge.next_steps)
    assert edge.confidence_score <= 0.75


def test_proposal_persisted_with_audit(packets):
    analyze_application(packets["APP_1001"], extractor=MockExtractor())
    assert db.get_application("APP_1001")["status"] == "PROPOSED"
    log = db.get_audit_log("APP_1001")
    assert [e.event_type for e in log] == ["agent_proposal"]
    assert log[0].agent_proposal.proposed_status == "NIBO"


def test_api_analyze_with_paths(client, packets):
    body = packets["APP_1002"].model_dump(mode="json")
    r = client.post("/analyze", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["proposed_status"] == "READY"
    audit = client.get("/applications/APP_1002/audit").json()
    assert audit["application"]["proposed_status"] == "READY"
    assert len(audit["audit_log"]) == 1


def test_api_analyze_with_base64(client, packets):
    docs = [{"filename": d.filename,
             "content_base64": base64.b64encode(open(d.path, "rb").read()).decode()}
            for d in packets["APP_1001"].documents]
    r = client.post("/analyze", json={"application_id": "B64_1", "documents": docs})
    assert r.status_code == 200, r.text
    assert r.json()["proposed_status"] == "NIBO"


def test_api_no_documents(client):
    r = client.post("/analyze", json={"application_id": "X", "documents": []})
    assert r.status_code == 400
    assert "No documents" in r.json()["detail"]


def test_api_missing_file(client):
    r = client.post("/analyze", json={"application_id": "X", "documents": [{"path": "C:/nope/missing.pdf"}]})
    assert r.status_code == 400
    assert "File not found" in r.json()["detail"]["errors"][0]


def test_api_not_a_pdf(client):
    b64 = base64.b64encode(b"hello, not a pdf").decode()
    r = client.post("/analyze", json={"application_id": "X",
                                      "documents": [{"filename": "a.txt", "content_base64": b64}]})
    assert r.status_code == 400
    assert "not a PDF" in r.json()["detail"]["errors"][0]


def test_api_both_sources_rejected(client):
    r = client.post("/analyze", json={"application_id": "X",
                                      "documents": [{"path": "a.pdf", "content_base64": "AAAA"}]})
    assert r.status_code == 422


def test_api_all_documents_unreadable(client, packets):
    scan = next(d.path for d in packets["APP_1003"].documents if "scanned" in d.path)
    r = client.post("/analyze", json={"application_id": "SCAN_1", "documents": [{"path": scan}]})
    assert r.status_code == 422
    assert "PDF extraction failed" in r.json()["detail"]["message"]
    assert db.get_application("SCAN_1") is None  # nothing persisted for a failed run


def test_missing_documents_flagged(client, packets):
    only_app = [d for d in packets["APP_1002"].documents if "application_form" in d.path]
    body = {"application_id": "PARTIAL", "documents": [d.model_dump(mode="json") for d in only_app]}
    r = client.post("/analyze", json=body)
    assert r.status_code == 200
    data = r.json()
    assert data["proposed_status"] == "NIBO"
    missing = {d["issue"] for d in data["issues_detail"]}
    assert missing == {"Medical records not provided", "Beneficiary form not provided", "ID document not provided"}


def test_sample_proposals_recover_evidence_without_model_quotes(packets):
    class NoQuotesExtractor(MockExtractor):
        def extract(self, document):
            extraction = super().extract(document)
            extraction.quotes = []
            return extraction

    for application_id, request in packets.items():
        baseline, _ = analyze_application(request, extractor=MockExtractor(), persist=False)
        recovered, _ = analyze_application(request, extractor=NoQuotesExtractor(), persist=False)
        assert recovered.proposed_status == baseline.proposed_status
        assert recovered.alternative_status == baseline.alternative_status
        assert recovered.confidence_score == baseline.confidence_score
        assert recovered.issues_detail == baseline.issues_detail
        if application_id == "APP_1001":
            assert recovered.issues_detail[0].evidence == '\"Beneficiary Name: ____________________\"'
            assert recovered.issues_detail[0].location == "page 1 of beneficiary_form.pdf"


@pytest.mark.parametrize("packet,action,status,letter", [
    ("APP_1001", "approve", "NIBO", True),
    ("APP_1002", "approve", "READY", False),
    ("APP_1001", "reject", "READY", False),
])
def test_decision_sets_final_status_and_stores_letter(client, packets, packet, action, status, letter):
    proposal = client.post("/analyze", json=packets[packet].model_dump(mode="json")).json()
    response = client.post(f"/applications/{packet}/decision", json={
        "decided_by": "UW_Jane", "action": action, "notes": "Reviewed the evidence",
        "expected_proposal_timestamp": proposal["agent_timestamp"]})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] == status
    assert bool(result["stored_letter_text"]) is letter
    assert result["execution"]["letter_generated"] is letter
    if letter:
        assert result["stored_letter_text"] == proposal["recommended_letter_text"]
    row = client.get(f"/applications/{packet}").json()
    assert row["status"] == status
    assert row["proposal"] == proposal
    log = client.get(f"/applications/{packet}/audit").json()["audit_log"]
    assert [entry["event_type"] for entry in log] == ["agent_proposal", "underwriter_decision", "system_execution"]
    assert log[2]["system_execution"]["stored_letter_text"] == (result["stored_letter_text"] or None)
    assert log[1]["underwriter_decision"]["decided_by"] == "UW_Jane"
    assert log[1]["underwriter_decision"]["final_issues"] == proposal["issues_detail"]


def test_modify_approves_edited_issues_and_regenerates_letter(client, packets):
    proposal = client.post("/analyze", json=packets["APP_1001"].model_dump(mode="json")).json()
    edited = [{**proposal["issues_detail"][0], "issue": "Please provide the signed beneficiary designation"}]
    result = client.post("/applications/APP_1001/decision", json={
        "decided_by": "UW_1", "action": "modify", "final_status": "NIBO", "modified_issues": edited,
        "expected_proposal_timestamp": proposal["agent_timestamp"]})
    assert result.status_code == 200, result.text
    assert edited[0]["issue"] in result.json()["stored_letter_text"]
    assert proposal["issues_detail"][0]["issue"] not in result.json()["stored_letter_text"]
    row = client.get("/applications/APP_1001").json()
    assert row["final_issues"] == edited and row["proposal"] == proposal


def test_modify_can_clear_issues_and_choose_ready(client, packets):
    proposal = client.post("/analyze", json=packets["APP_1001"].model_dump(mode="json")).json()
    response = client.post("/applications/APP_1001/decision", json={
        "decided_by": "UW_1", "action": "modify", "final_status": "READY", "modified_issues": [],
        "expected_proposal_timestamp": proposal["agent_timestamp"]})
    assert response.status_code == 200
    assert response.json()["status"] == "READY" and response.json()["stored_letter_text"] == ""


def test_duplicate_and_stale_decisions_are_rejected(client, packets):
    request = packets["APP_1001"].model_dump(mode="json")
    old = client.post("/analyze", json=request).json()
    current = client.post("/analyze", json=request).json()
    body = {"decided_by": "UW_1", "action": "approve", "expected_proposal_timestamp": old["agent_timestamp"]}
    assert client.post("/applications/APP_1001/decision", json=body).status_code == 409
    body["expected_proposal_timestamp"] = current["agent_timestamp"]
    assert client.post("/applications/APP_1001/decision", json=body).status_code == 200
    assert client.post("/applications/APP_1001/decision", json=body).status_code == 409
    assert len(db.get_audit_log("APP_1001")) == 4


@pytest.mark.parametrize("extra", [
    {"decided_by": " "}, {"action": "modify"},
    {"action": "reject", "final_status": "NIBO"},
    {"action": "approve", "final_status": "READY"},
    {"action": "approve", "modified_issues": []},
    {"action": "modify", "final_status": "NIBO", "modified_issues": []},
    {"action": "modify", "final_status": "NIBO", "modified_issues": [{"issue":" ","rule":"review","severity":"major","evidence":"","location":""}]},
])
def test_invalid_decision_does_not_write(client, packets, extra):
    proposal = client.post("/analyze", json=packets["APP_1001"].model_dump(mode="json")).json()
    body = {"decided_by": "UW_1", "action": "approve", "expected_proposal_timestamp": proposal["agent_timestamp"], **extra}
    assert client.post("/applications/APP_1001/decision", json=body).status_code == 422
    assert db.get_application("APP_1001")["status"] == "PROPOSED"
    assert len(db.get_audit_log("APP_1001")) == 1


def test_unknown_application_decision_and_view(client):
    assert client.get("/applications/missing").status_code == 404
    response = client.post("/applications/missing/decision", json={"decided_by": "UW_1",
        "action": "approve", "expected_proposal_timestamp": "2026-10-21T12:00:00Z"})
    assert response.status_code == 404


def test_decision_transaction_rolls_back_all_writes(packets, monkeypatch):
    from models import DecisionRequest

    proposal, _ = analyze_application(packets["APP_1001"], extractor=MockExtractor())
    original = db._insert_audit
    def fail_execution(conn, entry):
        if entry.event_type == "system_execution":
            raise RuntimeError("simulated audit failure")
        original(conn, entry)
    monkeypatch.setattr(db, "_insert_audit", fail_execution)
    with pytest.raises(RuntimeError, match="simulated audit failure"):
        db.save_decision("APP_1001", DecisionRequest(decided_by="UW_1", action="approve",
                         expected_proposal_timestamp=proposal.agent_timestamp))
    row = db.get_application("APP_1001")
    assert row["status"] == "PROPOSED" and row["stored_letter_text"] is None
    assert len(db.get_audit_log("APP_1001")) == 1


def test_reanalysis_clears_previous_letter_and_final_issues(client, packets):
    body = packets["APP_1001"].model_dump(mode="json")
    proposal = client.post("/analyze", json=body).json()
    client.post("/applications/APP_1001/decision", json={"decided_by": "UW_1", "action": "approve",
        "expected_proposal_timestamp": proposal["agent_timestamp"]})
    assert client.post("/analyze", json=body).status_code == 200
    row = db.get_application("APP_1001")
    assert row["status"] == "PROPOSED"
    assert row["stored_letter_text"] is None and row["final_issues"] is None
    assert "Dear Jane Doe" in db.get_audit_log("APP_1001")[2].system_execution.stored_letter_text
    assert [entry.event_type for entry in db.get_audit_log("APP_1001")] == [
        "agent_proposal", "underwriter_decision", "system_execution", "agent_proposal"]


def test_concurrent_decisions_only_finalize_once(packets):
    from concurrent.futures import ThreadPoolExecutor
    from models import DecisionRequest

    proposal, _ = analyze_application(packets["APP_1001"], extractor=MockExtractor())
    request = DecisionRequest(decided_by="UW_1", action="approve",
                              expected_proposal_timestamp=proposal.agent_timestamp)
    def submit():
        try:
            return db.save_decision("APP_1001", request)["status"]
        except db.DecisionConflict:
            return "conflict"
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: submit(), range(2))) == ["NIBO", "conflict"]
    assert len(db.get_audit_log("APP_1001")) == 3


def test_built_portal_served(client):
    from app import DIST_DIR
    if not (DIST_DIR / "index.html").is_file():
        pytest.skip("Run npm run build to verify the production portal")
    response = client.get("/")
    assert response.status_code == 200 and "NIBO" in response.text
    import re
    assets = re.findall(r'(?:src|href)="(/assets/[^" ]+)"', response.text)
    assert assets
    for asset in assets:
        assert client.get(asset).status_code == 200
