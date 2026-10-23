import base64

import pytest
from fastapi.testclient import TestClient

import database as db
import sample_applications as samples
from config import settings
from intake import detect_case_details


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


def _docs(request):
    return [d.model_dump(mode="json") for d in request.documents]


def test_detects_id_and_product_from_text():
    found = detect_case_details(["Applicant Name: Jane Doe\nProduct: TermLife_20yr\nRef APP_2044",
                                 "Beneficiary form\nRef APP_2044"])
    assert found.application_id == "APP_2044"
    assert found.policy_product == "TermLife_20yr"
    assert found.problems == []


@pytest.mark.parametrize("text,expected", [
    ("Product: Term Life 10 year", "TermLife_10yr"),
    ("Policy Product: Universal Life", "UniversalLife"),
    ("Product: termlife_20YR", "TermLife_20yr"),
])
def test_product_spellings(text, expected):
    assert detect_case_details([text + "\nApplication ID: APP_123"]).policy_product == expected


def test_conflicting_ids_are_not_guessed():
    found = detect_case_details(["Ref APP_1001", "Ref APP_1002"])
    assert found.application_id is None
    assert "APP_1001" in found.problems[0] and "APP_1002" in found.problems[0]


def test_missing_details_reported():
    found = detect_case_details(["Some text without either"])
    assert found.application_id is None and found.policy_product is None
    assert len(found.problems) == 2


def test_unknown_product_not_guessed():
    found = detect_case_details(["Ref APP_100\nProduct: Whole Life Platinum"])
    assert found.policy_product is None
    assert "Whole Life Platinum" in found.problems[0]


def test_api_reads_case_details_from_documents(client, packets):
    r = client.post("/analyze", json={"documents": _docs(packets["APP_1001"])})
    assert r.status_code == 200, r.text
    assert r.json()["application_id"] == "APP_1001"
    row = client.get("/applications/APP_1001").json()
    assert row["policy_product"] == "TermLife_10yr"


def test_api_supplied_id_still_wins(client, packets):
    r = client.post("/analyze", json={"application_id": "MANUAL_9", "documents": _docs(packets["APP_1002"])})
    assert r.status_code == 200, r.text
    assert r.json()["application_id"] == "MANUAL_9"


def test_api_mixed_packet_asks_for_id(client, packets):
    docs = _docs(packets["APP_1001"])[:2] + _docs(packets["APP_1002"])[2:]
    r = client.post("/analyze", json={"documents": docs})
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["needs"] == ["application_id"]
    assert "different applications" in detail["message"]
    assert db.get_application("APP_1001") is None


def test_api_scan_only_asks_for_id(client, packets):
    scan = next(d.path for d in packets["APP_1003"].documents if "scanned" in d.path)
    r = client.post("/analyze", json={"documents": [{"path": scan}]})
    assert r.status_code == 422
    assert r.json()["detail"]["needs"] == ["application_id"]


def test_conflicting_id_labels_are_not_guessed():
    found = detect_case_details(["Application ID: APP_1001", "Ref APP_1002"])
    assert found.application_id is None
    assert "different applications" in found.problems[0]
