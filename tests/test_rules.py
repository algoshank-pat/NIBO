from datetime import date

import pytest

from models import (
    ApplicationFormFields, Beneficiary, BeneficiaryFormFields, DocumentExtraction, DocumentType,
    FieldQuote, IdDocumentFields, LoadedDocument, MedicalRecordFields,
)
from rules import RULES, run_rules

AS_OF = date(2026, 10, 21)
ALL = list(RULES)


def doc(doc_type: DocumentType, fields, quotes=None, **kw) -> LoadedDocument:
    ext = DocumentExtraction(doc_type=doc_type, classification_confidence=0.95,
                             quotes=quotes or [], **{doc_type.value: fields})
    return LoadedDocument(doc_id=doc_type.value, filename=f"{doc_type.value}.pdf",
                          pages=["x"], extraction=ext, **kw)


def good_packet(**overrides) -> list[LoadedDocument]:
    parts = {
        "app": ApplicationFormFields(applicant_name="Jane Doe", dob="1980-05-10", email="j@x.com",
                                     phone="555-0100", signature_present=True, signature_date="2026-10-01"),
        "med": MedicalRecordFields(patient_name="Jane Doe", applicant_age=46, exam_date="2026-09-15"),
        "id": IdDocumentFields(id_type="DL", full_name="Jane Doe", expiry_date="2029-01-01"),
        "ben": BeneficiaryFormFields(beneficiaries=[Beneficiary(name="John Doe", relationship="Spouse", percentage=100)]),
    }
    parts.update(overrides)
    return [
        doc(DocumentType.APPLICATION_FORM, parts["app"],
            [FieldQuote(field="phone", quote="Phone: ", page=1)]),
        doc(DocumentType.MEDICAL_RECORDS, parts["med"]),
        doc(DocumentType.ID_DOCUMENT, parts["id"]),
        doc(DocumentType.BENEFICIARY_FORM, parts["ben"],
            [FieldQuote(field="beneficiary_name", quote="Primary Beneficiary Name: ____", page=1)]),
    ]


def rule_ids(issues):
    return [i.rule for i in issues]


def test_clean_packet_has_no_issues():
    assert run_rules(ALL, good_packet(), AS_OF) == []


def test_missing_phone_quotes_evidence():
    app = ApplicationFormFields(applicant_name="Jane Doe", dob="1980-05-10", email="j@x.com",
                                phone=None, signature_present=True, signature_date="2026-10-01")
    issues = run_rules(ALL, good_packet(app=app), AS_OF)
    assert rule_ids(issues) == ["required_fields"]
    assert issues[0].field == "phone"
    assert issues[0].evidence == '"Phone:"'
    assert issues[0].location == "page 1 of application_form.pdf"


def test_unsigned_and_undated():
    app = ApplicationFormFields(applicant_name="Jane Doe", dob="1980-05-10", email="j@x.com",
                                phone="1", signature_present=False, signature_date=None)
    issues = run_rules(ALL, good_packet(app=app), AS_OF)
    assert [i.field for i in issues] == ["signature", "signature_date"]
    # date_consistency falls back to as_of when unsigned: age 46 still matches.
    assert all(i.rule == "signature_present" for i in issues)


@pytest.mark.parametrize("med_age,expect_issue", [(46, False), (45, False), (47, False), (48, True), (44, True)])
def test_age_tolerance_is_one_year(med_age, expect_issue):
    med = MedicalRecordFields(applicant_age=med_age, exam_date="2026-09-15")
    issues = run_rules(ALL, good_packet(med=med), AS_OF)
    assert ("date_consistency" in rule_ids(issues)) is expect_issue


def test_medical_age_derived_from_dob_when_not_stated():
    med = MedicalRecordFields(dob="1990-01-01", exam_date="2026-09-15")
    issues = run_rules(ALL, good_packet(med=med), AS_OF)
    assert rule_ids(issues) == ["date_consistency"]
    assert issues[0].actual == "36"


@pytest.mark.parametrize("exam,stale", [("2023-10-21", False), ("2023-10-20", True), ("2020-01-01", True)])
def test_medical_recency_boundary(exam, stale):
    med = MedicalRecordFields(applicant_age=46, exam_date=exam)
    issues = run_rules(ALL, good_packet(med=med), AS_OF)
    assert ("medical_recency" in rule_ids(issues)) is stale
    if stale:
        assert issues[0].severity == "warning"


def test_blank_beneficiary():
    ben = BeneficiaryFormFields(beneficiaries=[Beneficiary()])
    issues = run_rules(ALL, good_packet(ben=ben), AS_OF)
    assert rule_ids(issues) == ["beneficiary_designated"]
    assert "name, relationship, percentage" in issues[0].message
    assert issues[0].evidence == '"Primary Beneficiary Name: ____"'


def test_beneficiary_shares_must_total_100():
    ben = BeneficiaryFormFields(beneficiaries=[
        Beneficiary(name="A", relationship="Child", percentage=50),
        Beneficiary(name="B", relationship="Child", percentage=40),
    ])
    issues = run_rules(ALL, good_packet(ben=ben), AS_OF)
    assert rule_ids(issues) == ["beneficiary_designated"]
    assert issues[0].actual == "90"


@pytest.mark.parametrize("id_type,expiry,n", [("Passport", "2030-01-01", 0), ("Other", "2030-01-01", 1),
                                              ("DL", "2026-10-21", 1), ("DL", "2026-10-22", 0)])
def test_id_valid(id_type, expiry, n):
    idd = IdDocumentFields(id_type=id_type, expiry_date=expiry)
    issues = run_rules(ALL, good_packet(id=idd), AS_OF)
    assert rule_ids(issues).count("id_valid") == n


def test_missing_document_reported_once():
    packet = [d for d in good_packet() if d.doc_type != DocumentType.MEDICAL_RECORDS]
    issues = run_rules(ALL, packet, AS_OF)
    assert len(issues) == 1
    assert issues[0].missing_document == DocumentType.MEDICAL_RECORDS
    assert issues[0].rule == "date_consistency" and issues[0].severity == "error"


def test_unreadable_document_flagged_uncertain():
    packet = [d for d in good_packet() if d.doc_type != DocumentType.ID_DOCUMENT]
    packet.append(LoadedDocument(doc_id="x", filename="scan.pdf", readable=False, error="no text layer",
                                 declared_type=DocumentType.ID_DOCUMENT))
    issues = run_rules(ALL, packet, AS_OF)
    assert len(issues) == 1 and issues[0].uncertain
    assert "re-upload" in issues[0].message


def test_rules_are_deterministic():
    app = ApplicationFormFields(applicant_name="", dob="1980-05-10")
    packet = good_packet(app=app)
    assert run_rules(ALL, packet, AS_OF) == run_rules(ALL, packet, AS_OF)


@pytest.mark.parametrize("line", ["Beneficiary Name: ____", "PRIMARY BENEFICIARY NAME: N/A",
                                  "Beneficiary Name:", "Beneficiary Name"])
def test_missing_quotes_locates_blank_beneficiary_on_second_page(line):
    packet = good_packet(ben=BeneficiaryFormFields(beneficiaries=[Beneficiary()]))
    beneficiary = packet[-1]
    beneficiary.extraction.quotes = []
    beneficiary.pages = ["Cover sheet", line + "\nRelationship: ____"]
    before = beneficiary.model_dump()
    issues = run_rules(ALL, packet, AS_OF)
    assert rule_ids(issues) == ["beneficiary_designated"]
    assert issues[0].evidence == f'"{line}"'
    assert issues[0].location == "page 2 of beneficiary_form.pdf"
    assert beneficiary.model_dump() == before


def test_partial_quotes_preserved_and_missing_field_located():
    packet = good_packet(app=ApplicationFormFields(applicant_name="Jane Doe", dob="1980-05-10"))
    packet[0].pages = ["Phone: ____\nEmail: N/A\nApplicant Signature: ____\nDate Signed: ____"]
    issues = run_rules(ALL, packet, AS_OF)
    by_field = {issue.field: issue for issue in issues}
    assert by_field["phone"].evidence == '\"Phone:\"'
    assert by_field["email"].evidence == '\"Email: N/A\"'
    assert by_field["signature"].evidence == '\"Applicant Signature: ____\"'
    assert by_field["signature_date"].evidence == '\"Date Signed: ____\"'


@pytest.mark.parametrize("line", ["Recorded on 2020-01-01", "Recorded on 01/01/2020"])
def test_missing_quote_falls_back_to_extracted_date_value(line):
    packet = good_packet(med=MedicalRecordFields(applicant_age=46, exam_date="2020-01-01"))
    packet[1].pages = ["Cover", line]
    issue, = run_rules(ALL, packet, AS_OF)
    assert issue.evidence == f'"{line}"'
    assert issue.location == "page 2 of medical_records.pdf"


def test_no_match_retains_explicit_missing_evidence():
    packet = good_packet(med=MedicalRecordFields(applicant_age=46, exam_date="2020-01-01"))
    packet[1].pages = ["No examination date supplied"]
    issue, = run_rules(ALL, packet, AS_OF)
    assert issue.evidence == "No 'exam_date' entry found in medical_records.pdf"
    assert issue.location == "medical_records.pdf"


def test_value_lookup_rejects_substrings_and_ambiguous_locations():
    from extractors import locate_field_quote

    med = doc(DocumentType.MEDICAL_RECORDS, MedicalRecordFields(applicant_age=46))
    med.pages = ["Reference 1460; measurements 46.5 and 0.46"]
    assert locate_field_quote(med, "applicant_age") is None
    med.pages = ["Patient is 46 years old", "Reported age is 46"]
    assert locate_field_quote(med, "applicant_age") is None
    med.pages = ["Reference 1460", "Patient is 46 years old"]
    quote = locate_field_quote(med, "applicant_age")
    assert quote.quote == "Patient is 46 years old" and quote.page == 2


def test_blank_model_quote_uses_label_fallback():
    packet = good_packet(ben=BeneficiaryFormFields(beneficiaries=[]))
    packet[-1].extraction.quotes = [FieldQuote(field="beneficiary_name", quote=" ", page=1)]
    packet[-1].pages = ["Beneficiary Name: ____"]
    issue, = run_rules(ALL, packet, AS_OF)
    assert issue.evidence == '\"Beneficiary Name: ____\"'


def test_label_matching_is_exact_and_scoped_to_document_type():
    from extractors import locate_field_quote

    med = doc(DocumentType.MEDICAL_RECORDS, MedicalRecordFields())
    med.pages = ["Coverage: 46\nPercentage: 100\nPatient Name: Jane Doe"]
    assert locate_field_quote(med, "applicant_age") is None
    assert locate_field_quote(med, "percentage") is None
    assert locate_field_quote(med, "patient_name").quote == "Patient Name: Jane Doe"
