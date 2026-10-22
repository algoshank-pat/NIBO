import json

import httpx
import pytest

from extractors import ExtractionError, OllamaExtractor, render_pdf_pages
from models import DocumentType, LoadedDocument

DOC = LoadedDocument(doc_id="d1", filename="med.pdf", pages=["MEDICAL EXAMINATION REPORT\nAge: 46"])
GOOD = {"doc_type": "medical_records", "classification_confidence": 0.9,
        "medical_records": {"patient_name": "Jane", "applicant_age": 46, "dob": None, "exam_date": "2026-09-01"},
        "quotes": [{"field": "applicant_age", "quote": "Age: 46", "page": 1}]}


def extractor(handler):
    return OllamaExtractor(model="gemma4", base_url="http://ollama.test",
                           client=httpx.Client(transport=httpx.MockTransport(handler)))


def reply(content):
    return httpx.Response(200, json={"message": {"role": "assistant", "content": content}})


def test_request_shape_and_parse():
    seen = {}

    def handler(req):
        seen.update(json.loads(req.content))
        return reply(json.dumps(GOOD))

    ext = extractor(handler).extract(DOC)
    assert ext.doc_type == DocumentType.MEDICAL_RECORDS and ext.medical_records.applicant_age == 46
    assert seen["model"] == "gemma4" and seen["stream"] is False and seen["think"] is False
    assert seen["options"]["temperature"] == 0
    assert seen["format"]["title"] == "DocumentExtraction"
    assert seen["messages"][0]["role"] == "system"


def test_retries_once_on_invalid_json():
    calls = []

    def handler(req):
        calls.append(json.loads(req.content))
        return reply("{not json" if len(calls) == 1 else json.dumps(GOOD))

    assert extractor(handler).extract(DOC).doc_type == DocumentType.MEDICAL_RECORDS
    assert len(calls) == 2 and "did not match the schema" in calls[1]["messages"][-1]["content"]


def test_gives_up_after_two_bad_replies():
    with pytest.raises(ExtractionError, match="invalid JSON twice"):
        extractor(lambda req: reply("nope")).extract(DOC)


def test_missing_model():
    with pytest.raises(ExtractionError, match="ollama pull gemma4"):
        extractor(lambda req: httpx.Response(404, json={"error": "model not found"})).extract(DOC)


def test_ollama_down():
    def handler(req):
        raise httpx.ConnectError("refused")
    with pytest.raises(ExtractionError, match="not reachable"):
        extractor(handler).extract(DOC)


def test_render_pdf_pages(tmp_path):
    from sample_applications import write_scanned_pdf
    pngs = render_pdf_pages(write_scanned_pdf(tmp_path / "s.pdf").read_bytes())
    assert len(pngs) == 1 and pngs[0][:4] == b"\x89PNG"


@pytest.mark.parametrize("quotes", [None, []])
def test_ollama_missing_quotes_still_produces_rule_evidence(quotes):
    from datetime import date
    from rules import run_rules

    payload = {"doc_type": "beneficiary_form", "classification_confidence": 0.9,
               "beneficiary_form": {"beneficiaries": []}}
    if quotes is not None:
        payload["quotes"] = quotes
    document = LoadedDocument(doc_id="b1", filename="beneficiary.pdf",
                              pages=["Cover", "Beneficiary Name: ____________________"])
    document.extraction = extractor(lambda req: reply(json.dumps(payload))).extract(document)
    issue, = run_rules(["beneficiary_designated"], [document], date(2026, 10, 21))
    assert issue.evidence == '\"Beneficiary Name: ____________________\"'
    assert issue.location == "page 2 of beneficiary.pdf"
    assert document.extraction.quotes == []


@pytest.mark.parametrize("compact", [False, True])
def test_tuning_options_preserve_output_validation(monkeypatch, compact):
    from config import settings
    from models import DocumentExtraction

    monkeypatch.setattr(settings, "ollama_compact_schema", compact)
    monkeypatch.setattr(settings, "ollama_keep_alive", "15m")
    monkeypatch.setattr(settings, "ollama_num_ctx", 4096)
    seen = {}
    def handler(request):
        seen.update(json.loads(request.content))
        return reply(json.dumps(GOOD))
    result = extractor(handler).extract(DOC)
    assert result.medical_records.applicant_age == 46
    assert seen["keep_alive"] == "15m" and seen["options"]["num_ctx"] == 4096
    assert ("title" not in seen["format"]) is compact
    assert seen["format"]["required"] == DocumentExtraction.model_json_schema()["required"]
    assert seen["format"]["$defs"]["IdDocumentFields"]["properties"]["id_type"]["anyOf"][0]["enum"] == ["DL", "Passport", "Other"]


def test_compact_schema_does_not_mutate_validation_schema():
    from extractors import compact_schema
    from models import DocumentExtraction

    original = DocumentExtraction.model_json_schema()
    trimmed = compact_schema(original)
    assert "title" in original and "title" not in trimmed
    assert len(json.dumps(trimmed)) < len(json.dumps(original))
    assert trimmed["properties"]["quotes"]["items"] == original["properties"]["quotes"]["items"]


def test_compact_schema_still_retries_invalid_output(monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "ollama_compact_schema", True)
    calls = []
    def handler(request):
        calls.append(request)
        return reply('{"doc_type":"wrong"}' if len(calls)==1 else json.dumps(GOOD))
    assert extractor(handler).extract(DOC).doc_type == DocumentType.MEDICAL_RECORDS
    assert len(calls) == 2


def test_timeout_message_does_not_claim_a_different_client_limit():
    def handler(request):
        raise httpx.ReadTimeout("custom client limit reached", request=request)
    with pytest.raises(ExtractionError, match="timed out while waiting for the model response"):
        extractor(handler).extract(DOC)


@pytest.mark.parametrize("concise", [False, True])
def test_genuine_ambiguity_notes_preserved_with_either_prompt(monkeypatch, concise):
    from config import settings
    monkeypatch.setattr(settings, "ollama_concise_notes", concise)
    ambiguity = "Two conflicting examination dates are present; the handwritten date is illegible."
    payload = {**GOOD, "notes": ambiguity}
    result = extractor(lambda request: reply(json.dumps(payload))).extract(DOC)
    assert result.notes == ambiguity
    assert result.medical_records.exam_date == GOOD["medical_records"]["exam_date"]
