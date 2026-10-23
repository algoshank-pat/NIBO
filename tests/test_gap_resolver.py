from __future__ import annotations

import asyncio
import json
from datetime import date
from pathlib import Path

import httpx
import pytest

import database as db
import sample_applications as samples
from agent import _pypdf_pages, analyze_application, build_graph
from config import settings
from extractors import MockExtractor
from gap_resolver import GapResolver
from integrations.dmv import DMVConnector
from models import (ApplicationFormFields, DocumentExtraction, DocumentType, GapResolution,
                    GapResolutionContext, IdDocumentFields, Issue, LoadedDocument)

AS_OF = date(2026, 10, 23)
CONTEXT = GapResolutionContext(applicant_state="CA")
RECORD = {"state": "CA", "driver_license_number": "D12345", "full_name": "Jane Doe",
          "valid": True, "expiration_date": "2035-01-01", "violations": 0, "suspensions": False}


def id_doc(**overrides) -> LoadedDocument:
    fields = {"id_type": "DL", "id_number": "D12345", "full_name": "Jane Doe", "expiry_date": "2020-01-01", **overrides}
    return LoadedDocument(doc_id="id", filename="id.pdf", pages=["Expiration Date: 01/01/2020"],
        extraction=DocumentExtraction(doc_type=DocumentType.ID_DOCUMENT, classification_confidence=0.95,
                                      id_document=IdDocumentFields(**fields)))


def id_gap(**overrides) -> Issue:
    return Issue(**{"rule": "id_valid", "severity": "error", "message": "Expired ID",
                    "field": "expiry_date", "document_type": DocumentType.ID_DOCUMENT, **overrides})


def resolver(record=None, **kwargs) -> GapResolver:
    connector = DMVConnector({"mock_mode": True, "records": {"CA:D12345": record or RECORD}})
    return GapResolver({}, context=CONTEXT, dmv=connector, as_of=AS_OF, **kwargs)


def test_valid_record_is_verified_and_original_document_unchanged():
    doc = id_doc()
    original = doc.model_dump()
    result = asyncio.run(resolver().resolve_gap(id_gap(), {}, [doc]))
    assert result.resolved and result.status == "resolved" and result.resolution_method == "auto_fetch"
    assert result.simulated and result.fetched_document.text_source == "api"
    assert result.fetched_document.extraction.id_document.expiry_date == "2035-01-01"
    assert "Re-ran id_valid; gap resolved" in result.trace
    assert doc.model_dump() == original
    assert result.timestamp.tzinfo is not None


@pytest.mark.parametrize("updates", [{"valid": False}, {"suspensions": True},
    {"expiration_date": "2020-01-01"}, {"expiration_date": "2026-10-23"},
    {"state": "TX"}, {"driver_license_number": "OTHER"}, {"full_name": "Other Applicant"},
    {"valid": "yes"}, {"expiration_date": "not-a-date"}])
def test_invalid_or_mismatched_provider_data_never_resolves(updates):
    result = asyncio.run(resolver({**RECORD, **updates}).resolve_gap(id_gap(), {}, [id_doc()]))
    assert not result.resolved and result.status == "pending"
    assert result.resolution_method == "manual_request"
    assert result.fetched_document is None


def test_lookup_uses_actual_number_not_issue_field():
    class RecordingDMV(DMVConnector):
        async def fetch(self, state: str, driver_license_number: str) -> dict:
            assert (state, driver_license_number) == ("CA", "D12345")
            return RECORD
    tool = RecordingDMV({"mock_mode": True})
    result = asyncio.run(GapResolver({}, context=CONTEXT, dmv=tool, as_of=AS_OF)
                         .resolve_gap(id_gap(field="expiry_date"), {}, [id_doc()]))
    assert result.resolved


@pytest.mark.parametrize("context,document", [(GapResolutionContext(), id_doc()),
    (CONTEXT, id_doc(id_number=None)), (CONTEXT, id_doc(id_type="Passport")),
    (GapResolutionContext(applicant_state="CA", driver_license_number="CONFLICT"), id_doc())])
def test_incomplete_or_conflicting_identity_does_not_call_provider(context, document):
    class MustNotCall(DMVConnector):
        async def fetch(self, state: str, driver_license_number: str) -> dict:
            raise AssertionError("Provider must not be called")
    result = asyncio.run(GapResolver({}, context=context, dmv=MustNotCall({}), as_of=AS_OF)
                         .resolve_gap(id_gap(), {}, [document]))
    assert result.status == "pending" and result.error_message is None


def test_missing_id_can_use_explicit_context_and_matching_applicant():
    app = LoadedDocument(doc_id="app", filename="app.pdf", extraction=DocumentExtraction(
        doc_type=DocumentType.APPLICATION_FORM, classification_confidence=0.95,
        application_form=ApplicationFormFields(applicant_name="Jane Doe")))
    context = GapResolutionContext(applicant_state="CA", driver_license_number="D12345")
    tool = DMVConnector({"records": {"CA:D12345": RECORD}})
    result = asyncio.run(GapResolver({}, context=context, dmv=tool, as_of=AS_OF)
                         .resolve_gap(id_gap(field="document"), {}, [app]))
    assert result.resolved


def test_medical_placeholder_remains_manual_even_with_mock_data():
    context = GapResolutionContext(applicant_id="synthetic-person", provider_npi="1234567890")
    gap = Issue(rule="medical_recency", severity="warning", message="Old exam")
    result = asyncio.run(GapResolver({}, context=context).resolve_gap(gap, {}, []))
    assert not result.resolved and result.resolution_method == "manual_request"
    assert result.simulated and result.fetched_data["simulated"]
    assert result.fetched_document is None


def test_missing_medical_document_routes_by_document_type():
    gap = Issue(rule="date_consistency", severity="error", message="Medical missing",
                missing_document=DocumentType.MEDICAL_RECORDS)
    result = asyncio.run(GapResolver({}).resolve_gap(gap, {}, []))
    assert result.status == "pending" and result.simulated
    assert "Route: medical records" in result.trace


def test_required_field_requests_manual_and_unknown_rule_is_unresolvable():
    manual = Issue(rule="required_fields", severity="error", message="Missing phone", field="phone")
    unknown = manual.model_copy(update={"rule": "unknown_rule"})
    result = asyncio.run(GapResolver({}).batch_resolve([manual, unknown], {}, []))
    assert [r.status for r in result.values()] == ["pending", "unresolvable"]
    assert "no message sent" in next(iter(result.values())).trace[-1]


def test_default_mock_dmv_does_not_invent_a_valid_license():
    result = asyncio.run(GapResolver({}, context=CONTEXT).resolve_gap(id_gap(), {}, [id_doc()]))
    assert not result.resolved and result.resolution_method == "manual_request"
    assert result.error_message == "IntegrationUnavailable"


def test_network_failure_degrades_without_exposing_exception_data():
    class FailingDMV(DMVConnector):
        async def fetch(self, state: str, driver_license_number: str) -> dict:
            raise httpx.ConnectError("secret-provider-detail")
    result = asyncio.run(GapResolver({}, context=CONTEXT, dmv=FailingDMV({}))
                         .resolve_gap(id_gap(), {}, [id_doc()]))
    assert result.status == "pending" and result.error_message == "ConnectError"
    assert "secret-provider-detail" not in result.model_dump_json()


def test_parallel_batch_preserves_duplicate_gaps_and_distinct_evidence_ids():
    async def scenario():
        started = 0
        both_started = asyncio.Event()
        class ConcurrentDMV(DMVConnector):
            async def fetch(self, state: str, driver_license_number: str) -> dict:
                nonlocal started
                started += 1
                if started == 2:
                    both_started.set()
                await both_started.wait()
                return RECORD
        service = GapResolver({"gap_parallelism": 2, "gap_timeout_s": 1}, context=CONTEXT,
                              dmv=ConcurrentDMV({}), as_of=AS_OF)
        results = await service.batch_resolve([id_gap(), id_gap()], {}, [id_doc()])
        assert started == 2 and len(results) == 2 and all(r.resolved for r in results.values())
        assert len({r.fetched_document.doc_id for r in results.values()}) == 2
    asyncio.run(scenario())


@pytest.mark.parametrize("batch_timeout,gap_timeout,error", [(1, 0.01, "TimeoutError"), (0.01, 1, "BatchTimeout")])
def test_timeouts_cancel_fetch_and_record_each_gap(batch_timeout, gap_timeout, error):
    async def scenario():
        active = 0
        class SlowDMV(DMVConnector):
            async def fetch(self, state: str, driver_license_number: str) -> dict:
                nonlocal active
                active += 1
                try:
                    await asyncio.sleep(5)
                    return RECORD
                finally:
                    active -= 1
        service = GapResolver({"gap_batch_timeout_s": batch_timeout, "gap_timeout_s": gap_timeout},
                              context=CONTEXT, dmv=SlowDMV({}), as_of=AS_OF)
        results = await service.batch_resolve([id_gap(), id_gap()], {}, [id_doc()])
        assert len(results) == 2 and active == 0
        assert all(r.status == "pending" and r.error_message == error for r in results.values())
    asyncio.run(scenario())


def test_http_adapter_is_mockable_and_unconfigured_live_adapter_fails_closed():
    async def scenario():
        def handler(request):
            assert request.method == "POST" and str(request.url) == "https://dmv.test/licenses/verify"
            assert json.loads(request.content) == {"state": "CA", "driver_license_number": "D12345"}
            return httpx.Response(200, json=RECORD)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            tool = DMVConnector({"mock_mode": False, "base_url": "https://dmv.test"}, client=client)
            assert await tool.fetch("CA", "D12345") == RECORD
        service = GapResolver({"gap_dmv_mock_mode": False}, context=CONTEXT)
        result = await service.resolve_gap(id_gap(), {}, [id_doc()])
        assert result.status == "pending" and result.error_message == "IntegrationUnavailable"
    asyncio.run(scenario())


def test_gap_model_rejects_false_resolution_without_evidence():
    with pytest.raises(ValueError, match="fetched evidence"):
        GapResolution(gap_id="g", original_issue=id_gap(), resolved=True, status="resolved",
                      resolution_method="auto_fetch", confidence=0.95)


@pytest.fixture
def packet(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_path", tmp_path / "test.db")
    monkeypatch.setattr(settings, "llm_mode", "mock")
    monkeypatch.setattr(settings, "gap_resolution_enabled", True)
    monkeypatch.setattr(settings, "gap_dmv_mock_mode", True)
    monkeypatch.setattr(samples, "SAMPLE_DIR", tmp_path / "samples")
    db.init_db()
    request = samples.generate()["APP_1002"]
    request.application_id = "GAP_DEMO"
    request.resolution_context = CONTEXT
    source = next(d for d in request.documents if d.filename == "id_document.pdf")
    doc = LoadedDocument(doc_id="id", filename=source.filename, pages=_pypdf_pages(Path(source.path).read_bytes()))
    original = MockExtractor().extract(doc).id_document
    samples.write_pdf(Path(source.path), "DRIVER LICENSE", ["Document Type: Driver's license",
        f"Name: {original.full_name}", f"License Number: {original.id_number}", "Expiration Date: 01/01/2020"])
    monkeypatch.setattr(settings, "gap_dmv_records", {f"CA:{original.id_number}": {
        **RECORD, "driver_license_number": original.id_number, "full_name": original.full_name}})
    return request


def test_full_pdf_to_verified_proposal_and_append_only_audit(packet):
    completion, state = analyze_application(packet, extractor=MockExtractor(), as_of=AS_OF)
    assert completion.proposed_status == "READY" and completion.issues_detail == []
    assert completion.recommended_letter_text == "" and completion.resolved_issues_count == 1
    assert completion.pending_issues_count == 0 and completion.requires_underwriter_judgment
    result, = completion.gap_resolutions.values()
    assert result.original_issue.rule == "id_valid" and result.original_issue.actual == "2020-01-01"
    assert result.resolved and result.simulated and "Simulation" in completion.issues_summary
    ids = [doc for doc in state.documents if doc.doc_type == DocumentType.ID_DOCUMENT]
    assert "2035-01-01" in state.extracted_text["id_document"]
    assert len(ids) == 2 and ids[0].extraction.id_document.expiry_date == "2035-01-01"
    assert ids[1].extraction.id_document.expiry_date == "2020-01-01"
    assert any("propose_status: NIBO" in line for line in state.trace)
    assert any("resolve_gaps: 1/1 resolved" in line for line in state.trace)
    assert db.get_application(packet.application_id)["status"] == "PROPOSED"
    log, = db.get_audit_log(packet.application_id)
    assert log.agent_proposal.gap_resolutions == completion.gap_resolutions
    assert log.agent_proposal.resolved_issues_count == 1


def test_api_exposes_resolution_history_without_changing_approval_flow(packet):
    from fastapi.testclient import TestClient
    from app import app
    with TestClient(app) as client:
        response = client.post("/analyze", json=packet.model_dump(mode="json"))
        assert response.status_code == 200, response.text
        proposal = response.json()
        assert proposal["proposed_status"] == "READY" and proposal["resolved_issues_count"] == 1
        audit = client.get(f"/applications/{packet.application_id}/audit").json()
        assert audit["application"]["status"] == "PROPOSED"
        assert len(audit["audit_log"][0]["agent_proposal"]["gap_resolutions"]) == 1
        decision = client.post(f"/applications/{packet.application_id}/decision", json={
            "decided_by": "TEST_UNDERWRITER", "action": "approve",
            "expected_proposal_timestamp": proposal["agent_timestamp"]})
        assert decision.status_code == 200 and decision.json()["status"] == "READY"


def test_failed_fetch_keeps_nibo_and_is_audited(packet, monkeypatch):
    monkeypatch.setattr(settings, "gap_dmv_records", {})
    completion, _ = analyze_application(packet, extractor=MockExtractor(), as_of=AS_OF)
    assert completion.proposed_status == "NIBO" and completion.pending_issues_count == 1
    result, = completion.gap_resolutions.values()
    assert result.error_message == "IntegrationUnavailable"
    assert db.get_audit_log(packet.application_id)[0].agent_proposal.gap_resolutions[result.gap_id].trace


def test_resolver_can_be_disabled_and_sync_graph_works_inside_async_caller(packet, monkeypatch):
    monkeypatch.setattr(settings, "gap_resolution_enabled", False)
    async def call():
        return analyze_application(packet, extractor=MockExtractor(), as_of=AS_OF, persist=False)
    completion, state = asyncio.run(call())
    assert completion.proposed_status == "NIBO" and completion.gap_resolutions == {}
    assert "resolve_gaps: disabled" in state.trace


def test_enabled_graph_works_inside_async_caller(packet):
    async def call():
        return analyze_application(packet, extractor=MockExtractor(), as_of=AS_OF, persist=False)
    completion, _ = asyncio.run(call())
    assert completion.proposed_status == "READY"


def test_graph_adds_only_one_node():
    nodes = set(build_graph(MockExtractor()).get_graph().nodes)
    assert nodes == {"__start__", "__end__", "load_documents", "classify_documents", "retrieve_rules",
                     "analyze", "propose_status", "resolve_gaps", "build_proposal"}


def test_conflicting_application_name_is_not_auto_resolved():
    app = LoadedDocument(doc_id="app", filename="app.pdf", extraction=DocumentExtraction(
        doc_type=DocumentType.APPLICATION_FORM, classification_confidence=0.95,
        application_form=ApplicationFormFields(applicant_name="Different Applicant")))
    result = asyncio.run(resolver().resolve_gap(id_gap(), {}, [app, id_doc()]))
    assert result.status == "pending" and result.fetched_data is None
    assert "Uploaded ID name conflicts" in " ".join(result.trace)


def test_final_node_rechecks_tool_claim_and_discards_unverified_candidate(packet, monkeypatch):
    import agent
    real_service = agent.GapResolver
    class UnverifiedResolver(real_service):
        async def batch_resolve(self, gaps, extracted_text, documents):
            candidate = id_doc()
            candidate.doc_id = "unverified"
            result = GapResolution(gap_id="unverified_gap", original_issue=gaps[0], resolved=True,
                status="resolved", resolution_method="auto_fetch", fetched_data=RECORD,
                fetched_document=candidate, confidence=0.95)
            return {result.gap_id: result}
    monkeypatch.setattr(agent, "GapResolver", UnverifiedResolver)
    completion, state = analyze_application(packet, extractor=MockExtractor(), as_of=AS_OF, persist=False)
    assert completion.proposed_status == "NIBO" and completion.resolved_issues_count == 0
    assert completion.pending_issues_count == 1
    assert not any(doc.doc_id == "unverified" for doc in state.documents)
    result, = completion.gap_resolutions.values()
    assert result.status == "pending" and "Final rule pass did not confirm" in " ".join(result.trace)
