"""Pydantic models: API input/output, agent state, rules, issues and audit log."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, model_validator


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- enums

class DocumentType(str, Enum):
    APPLICATION_FORM = "application_form"
    MEDICAL_RECORDS = "medical_records"
    ID_DOCUMENT = "id_document"
    BENEFICIARY_FORM = "beneficiary_form"
    UNKNOWN = "unknown"


Status = Literal["NIBO", "READY"]
RuleSeverity = Literal["error", "warning"]
DetailSeverity = Literal["critical", "major", "minor"]


# --------------------------------------------------------------------------- input

class DocumentInput(BaseModel):
    """One uploaded document. Supply exactly one of `path` or `content_base64`."""
    filename: str | None = None
    path: str | None = None
    content_base64: str | None = None
    declared_type: DocumentType | None = Field(
        default=None, description="Optional hint from the uploader; the agent still classifies."
    )
    uploaded_at: datetime = Field(default_factory=utcnow)

    @model_validator(mode="after")
    def _one_source(self) -> "DocumentInput":
        if bool(self.path) == bool(self.content_base64):
            raise ValueError("Provide exactly one of 'path' or 'content_base64' per document")
        if not self.filename:
            self.filename = self.path.replace("\\", "/").rsplit("/", 1)[-1] if self.path else "upload.pdf"
        return self


class ApplicationRequest(BaseModel):
    application_id: str = Field(min_length=1)
    documents: list[DocumentInput]
    policy_product: str = "TermLife_10yr"
    applicant_age: int | None = None


# --------------------------------------------------------------------------- extraction
# These schemas double as the model's JSON-schema output formats, so keep them flat and explicit.

class FieldQuote(BaseModel):
    field: str = Field(description="Field name, e.g. 'phone' or 'beneficiary_name'")
    quote: str = Field(description="Verbatim text from the document supporting the value (or showing it blank)")
    page: int = Field(description="1-based page number where the quote appears")


class ApplicationFormFields(BaseModel):
    applicant_name: str | None = None
    dob: str | None = Field(default=None, description="Date of birth, YYYY-MM-DD")
    email: str | None = None
    phone: str | None = None
    signature_present: bool = Field(default=False, description="True only if a signature (name/mark) is actually present")
    signature_date: str | None = Field(default=None, description="Date signed, YYYY-MM-DD")


class MedicalRecordFields(BaseModel):
    patient_name: str | None = None
    applicant_age: int | None = Field(default=None, description="Age as stated in the medical record")
    dob: str | None = Field(default=None, description="Date of birth, YYYY-MM-DD")
    exam_date: str | None = Field(default=None, description="Date of examination, YYYY-MM-DD")


class IdDocumentFields(BaseModel):
    id_type: Literal["DL", "Passport", "Other"] | None = Field(
        default=None, description="DL = driver's license"
    )
    full_name: str | None = None
    id_number: str | None = None
    expiry_date: str | None = Field(default=None, description="YYYY-MM-DD")


class Beneficiary(BaseModel):
    name: str | None = None
    relationship: str | None = None
    percentage: float | None = None


class BeneficiaryFormFields(BaseModel):
    beneficiaries: list[Beneficiary] = Field(default_factory=list)


class DocumentExtraction(BaseModel):
    """Classification + field extraction for a single document (LLM structured output)."""
    doc_type: DocumentType
    classification_confidence: float = Field(description="0-1 confidence in doc_type")
    application_form: ApplicationFormFields | None = None
    medical_records: MedicalRecordFields | None = None
    id_document: IdDocumentFields | None = None
    beneficiary_form: BeneficiaryFormFields | None = None
    quotes: list[FieldQuote] = Field(default_factory=list)
    notes: str | None = Field(default=None, description="Anything ambiguous or hard to read")


class LoadedDocument(BaseModel):
    doc_id: str
    filename: str
    declared_type: DocumentType | None = None
    pages: list[str] = Field(default_factory=list)
    readable: bool = True
    error: str | None = None
    text_source: Literal["pypdf", "vision_ocr", "none"] = "pypdf"
    extraction: DocumentExtraction | None = None

    @property
    def text(self) -> str:
        return "\n".join(self.pages)

    @property
    def doc_type(self) -> DocumentType:
        return self.extraction.doc_type if self.extraction else DocumentType.UNKNOWN


# --------------------------------------------------------------------------- rules & issues

class RuleDefinition(BaseModel):
    """Serializable rule metadata. The check function lives in rules.py."""
    id: str
    description: str
    severity: RuleSeverity
    error_message: str
    version: str = "1.0.0"
    required_documents: list[DocumentType] = Field(default_factory=list)


class Issue(BaseModel):
    rule: str
    severity: RuleSeverity
    message: str
    field: str | None = None
    expected: str | None = None
    actual: str | None = None
    evidence: str = ""
    location: str = ""
    document_type: DocumentType | None = None
    missing_document: DocumentType | None = None
    uncertain: bool = Field(default=False, description="Agent could not read/extract reliably")


class Proposal(BaseModel):
    status: Status
    confidence: float
    reasoning: str
    next_steps: list[str] = Field(default_factory=list)
    alternative_status: Status | None = None
    requires_underwriter_judgment: bool = False


# --------------------------------------------------------------------------- agent state

class AnalysisState(BaseModel):
    """LangGraph state. Nodes return partial dict updates."""
    application_id: str
    policy_product: str = "TermLife_10yr"
    applicant_age: int | None = None
    as_of: date | None = Field(default=None, description="Evaluation date for rules; defaults to today")
    inputs: list[DocumentInput] = Field(default_factory=list)

    documents: list[LoadedDocument] = Field(default_factory=list)
    extracted_text: dict[str, str] = Field(default_factory=dict)
    rules: list[RuleDefinition] = Field(default_factory=list)
    rules_applied: list[str] = Field(default_factory=list)
    issues_found: list[Issue] = Field(default_factory=list)
    proposal: Proposal | None = None
    completion: "CompletionProposal | None" = None

    agent_mode: Literal["ollama", "mock"] = "mock"
    trace: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- output

class IssueDetail(BaseModel):
    issue: str
    rule: str
    severity: DetailSeverity
    evidence: str
    location: str
    field: str | None = None
    expected: str | None = None
    actual: str | None = None


class ExtractedDocumentSnapshot(BaseModel):
    filename: str
    doc_type: DocumentType
    classification_confidence: float
    readable: bool
    text_source: str
    fields: dict
    text_excerpt: str


class EvidenceSnapshot(BaseModel):
    extracted_documents: dict[str, ExtractedDocumentSnapshot]
    rules_checked: list[str]
    rules_version: str


class CompletionProposal(BaseModel):
    application_id: str
    agent_timestamp: datetime = Field(default_factory=utcnow)
    proposed_status: Status
    alternative_status: Status | None = None
    requires_underwriter_judgment: bool = False
    confidence_score: float
    issues_summary: str
    issues_detail: list[IssueDetail]
    reasoning: str
    next_steps: list[str]
    evidence_snapshot: EvidenceSnapshot
    recommended_letter_text: str
    agent_mode: Literal["ollama", "mock"]
    model: str | None = None


# --------------------------------------------------------------------------- audit

class UnderwriterDecision(BaseModel):
    decided_by: str = Field(min_length=1, pattern=r"\S")
    action: Literal["approve", "reject", "modify"]
    final_status: Status
    notes: str | None = None
    timestamp: datetime = Field(default_factory=utcnow)
    final_issues: list[IssueDetail] | None = None


class DecisionRequest(BaseModel):
    decided_by: str = Field(min_length=1, pattern=r"\S")
    action: Literal["approve", "reject", "modify"]
    expected_proposal_timestamp: datetime
    final_status: Status | None = None
    modified_issues: list[IssueDetail] | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def _consistent_action(self) -> "DecisionRequest":
        if self.action == "modify":
            if self.modified_issues is None or self.final_status is None:
                raise ValueError("Modify requires modified_issues and an explicit final_status")
            if any(not issue.issue.strip() for issue in self.modified_issues):
                raise ValueError("Each modified issue must describe an outstanding requirement")
            if self.final_status == "NIBO" and not self.modified_issues:
                raise ValueError("NIBO requires at least one outstanding issue")
        elif self.modified_issues is not None:
            raise ValueError("Only modify accepts modified_issues")
        if self.action == "reject" and self.final_status not in (None, "READY"):
            raise ValueError("Reject overrides the proposal to READY")
        return self


class SystemExecution(BaseModel):
    status_updated: bool
    letter_generated: bool
    stored_letter_text: str | None = None
    timestamp: datetime = Field(default_factory=utcnow)


class AuditLog(BaseModel):
    id: str = Field(default_factory=lambda: f"audit_{uuid.uuid4().hex[:12]}")
    application_id: str
    timestamp: datetime = Field(default_factory=utcnow)
    event_type: Literal["agent_proposal", "underwriter_decision", "system_execution"]
    agent_proposal: CompletionProposal | None = None
    underwriter_decision: UnderwriterDecision | None = None
    system_execution: SystemExecution | None = None


AnalysisState.model_rebuild()
