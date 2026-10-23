"""Deterministic rules engine.

Every rule is a pure function of the extracted document fields plus an `as_of` date,
so the same documents always produce the same issues. The LLM only extracts fields;
it never decides whether a rule passes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Callable

from config import settings
from extractors import locate_field_quote
from models import (
    DocumentType,
    Issue,
    LoadedDocument,
    RuleDefinition,
)

RULES_VERSION = settings.rules_version


# --------------------------------------------------------------------------- context

@dataclass
class RuleContext:
    documents: list[LoadedDocument]
    as_of: date = field(default_factory=date.today)

    def doc(self, doc_type: DocumentType) -> LoadedDocument | None:
        """First readable document of this type; falls back to an unreadable one."""
        matches = [d for d in self.documents if d.doc_type == doc_type]
        if not matches:
            # An unreadable doc has no extraction; trust the uploader's declared type.
            matches = [d for d in self.documents if not d.readable and d.declared_type == doc_type]
        readable = [d for d in matches if d.readable and d.extraction]
        return (readable or matches or [None])[0]

    def fields(self, doc_type: DocumentType):
        d = self.doc(doc_type)
        if not d or not d.extraction:
            return None
        return getattr(d.extraction, doc_type.value)


def _quote(ctx: RuleContext, doc_type: DocumentType, field_name: str) -> tuple[str, str]:
    """Return (evidence, location) for a field, using an extractor quote or a deterministic source lookup."""
    d = ctx.doc(doc_type)
    if not d:
        return "", ""
    quotes = d.extraction.quotes if d.extraction else []
    for q in quotes:
        if q.field == field_name and q.quote.strip():
            return f'"{q.quote.strip()}"', f"page {q.page} of {d.filename}"
    fallback = locate_field_quote(d, field_name)
    if fallback:
        return f'"{fallback.quote}"', f"page {fallback.page} of {d.filename}"
    return f"No '{field_name}' entry found in {d.filename}", d.filename


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def _age_on(dob: date, on: date) -> int:
    return on.year - dob.year - ((on.month, on.day) < (dob.month, dob.day))


DOC_LABELS = {
    DocumentType.APPLICATION_FORM: "Application form",
    DocumentType.MEDICAL_RECORDS: "Medical records",
    DocumentType.ID_DOCUMENT: "ID document",
    DocumentType.BENEFICIARY_FORM: "Beneficiary form",
}


def _missing_doc_issue(rule: "Rule", doc_type: DocumentType, ctx: RuleContext) -> Issue:
    d = ctx.doc(doc_type)
    label = DOC_LABELS[doc_type]
    pretty = label.lower() if doc_type != DocumentType.ID_DOCUMENT else label
    if d and not d.readable:
        return Issue(
            rule=rule.id, severity=rule.severity,
            message=f"{label} could not be read - please re-upload",
            field="document", expected="readable PDF", actual=d.error or "unreadable",
            evidence=f"Text extraction failed for {d.filename}: {d.error or 'no text'}",
            location=d.filename, document_type=doc_type, missing_document=doc_type, uncertain=True,
        )
    return Issue(
        rule=rule.id, severity=rule.severity,
        message=f"{label} not provided",
        field="document", expected=f"{pretty} in application packet", actual="not found",
        evidence=f"No document in the packet was classified as {pretty}",
        location="application packet", document_type=doc_type, missing_document=doc_type,
    )


# --------------------------------------------------------------------------- checks

REQUIRED_APP_FIELDS = ["applicant_name", "dob", "email", "phone"]


def check_required_fields(rule: "Rule", ctx: RuleContext) -> list[Issue]:
    app = ctx.fields(DocumentType.APPLICATION_FORM)
    if app is None:
        return [_missing_doc_issue(rule, DocumentType.APPLICATION_FORM, ctx)]
    issues = []
    for f in REQUIRED_APP_FIELDS:
        value = getattr(app, f)
        if value is None or not str(value).strip():
            evidence, location = _quote(ctx, DocumentType.APPLICATION_FORM, f)
            issues.append(Issue(
                rule=rule.id, severity=rule.severity,
                message=f"{rule.error_message}: {f.replace('_', ' ')}",
                field=f, expected="non-empty value", actual="blank",
                evidence=evidence, location=location,
                document_type=DocumentType.APPLICATION_FORM,
            ))
    return issues


def check_signature_present(rule: "Rule", ctx: RuleContext) -> list[Issue]:
    app = ctx.fields(DocumentType.APPLICATION_FORM)
    if app is None:
        return [_missing_doc_issue(rule, DocumentType.APPLICATION_FORM, ctx)]
    issues = []
    if not app.signature_present:
        ev, loc = _quote(ctx, DocumentType.APPLICATION_FORM, "signature")
        issues.append(Issue(
            rule=rule.id, severity=rule.severity, message="Applicant signature missing",
            field="signature", expected="applicant signature", actual="not signed",
            evidence=ev, location=loc, document_type=DocumentType.APPLICATION_FORM,
        ))
    if not _parse_date(app.signature_date):
        ev, loc = _quote(ctx, DocumentType.APPLICATION_FORM, "signature_date")
        issues.append(Issue(
            rule=rule.id, severity=rule.severity, message="Signature date missing or invalid",
            field="signature_date", expected="date signed (YYYY-MM-DD)",
            actual=app.signature_date or "blank",
            evidence=ev, location=loc, document_type=DocumentType.APPLICATION_FORM,
        ))
    return issues


def check_date_consistency(rule: "Rule", ctx: RuleContext) -> list[Issue]:
    app = ctx.fields(DocumentType.APPLICATION_FORM)
    med = ctx.fields(DocumentType.MEDICAL_RECORDS)
    if app is None:
        return [_missing_doc_issue(rule, DocumentType.APPLICATION_FORM, ctx)]
    if med is None:
        return [_missing_doc_issue(rule, DocumentType.MEDICAL_RECORDS, ctx)]

    dob = _parse_date(app.dob)
    if dob is None:
        return []  # required_fields already reports a missing DOB
    signed = _parse_date(app.signature_date) or ctx.as_of
    app_age = _age_on(dob, signed)

    med_age = med.applicant_age
    if med_age is None and _parse_date(med.dob):
        med_age = _age_on(_parse_date(med.dob), _parse_date(med.exam_date) or ctx.as_of)
    if med_age is None:
        ev, loc = _quote(ctx, DocumentType.MEDICAL_RECORDS, "applicant_age")
        return [Issue(
            rule=rule.id, severity="warning", message="Age not stated in medical records - cannot verify",
            field="applicant_age", expected=str(app_age), actual="not found",
            evidence=ev, location=loc, document_type=DocumentType.MEDICAL_RECORDS, uncertain=True,
        )]

    if abs(app_age - med_age) > 1:
        app_ev, app_loc = _quote(ctx, DocumentType.APPLICATION_FORM, "dob")
        med_ev, med_loc = _quote(ctx, DocumentType.MEDICAL_RECORDS, "applicant_age")
        return [Issue(
            rule=rule.id, severity=rule.severity,
            message=f"{rule.error_message}: application implies {app_age}, medical records say {med_age}",
            field="age", expected=f"{app_age} (+/-1, from DOB {dob.isoformat()} at signing)",
            actual=str(med_age),
            evidence=f"Application: {app_ev} | Medical: {med_ev}",
            location=f"{app_loc}; {med_loc}", document_type=DocumentType.MEDICAL_RECORDS,
        )]
    return []


def check_medical_recency(rule: "Rule", ctx: RuleContext) -> list[Issue]:
    med = ctx.fields(DocumentType.MEDICAL_RECORDS)
    if med is None:
        return [_missing_doc_issue(rule, DocumentType.MEDICAL_RECORDS, ctx)]
    exam = _parse_date(med.exam_date)
    ev, loc = _quote(ctx, DocumentType.MEDICAL_RECORDS, "exam_date")
    if exam is None:
        return [Issue(
            rule=rule.id, severity=rule.severity, message="Medical exam date missing - cannot verify recency",
            field="exam_date", expected="exam date", actual=med.exam_date or "blank",
            evidence=ev, location=loc, document_type=DocumentType.MEDICAL_RECORDS, uncertain=True,
        )]
    years = settings.medical_recency_years
    try:
        cutoff = ctx.as_of.replace(year=ctx.as_of.year - years)
    except ValueError:  # Feb 29
        cutoff = ctx.as_of.replace(year=ctx.as_of.year - years, day=28)
    if exam < cutoff:
        age_days = (ctx.as_of - exam).days
        return [Issue(
            rule=rule.id, severity=rule.severity,
            message=f"{rule.error_message} (exam dated {exam.isoformat()}, {age_days / 365.25:.1f} years ago)",
            field="exam_date", expected=f"on or after {cutoff.isoformat()}", actual=exam.isoformat(),
            evidence=ev, location=loc, document_type=DocumentType.MEDICAL_RECORDS,
        )]
    return []


def check_beneficiary_designated(rule: "Rule", ctx: RuleContext) -> list[Issue]:
    ben = ctx.fields(DocumentType.BENEFICIARY_FORM)
    if ben is None:
        return [_missing_doc_issue(rule, DocumentType.BENEFICIARY_FORM, ctx)]

    valid = [
        b for b in ben.beneficiaries
        if b.name and b.name.strip() and b.relationship and b.relationship.strip()
        and b.percentage is not None and b.percentage > 0
    ]
    if not valid:
        ev, loc = _quote(ctx, DocumentType.BENEFICIARY_FORM, "beneficiary_name")
        missing = []
        first = ben.beneficiaries[0] if ben.beneficiaries else None
        if not first or not (first.name or "").strip():
            missing.append("name")
        if not first or not (first.relationship or "").strip():
            missing.append("relationship")
        if not first or not first.percentage:
            missing.append("percentage")
        return [Issue(
            rule=rule.id, severity=rule.severity,
            message=f"{rule.error_message}: missing {', '.join(missing) or 'complete designation'}",
            field="beneficiary", expected="named beneficiary with relationship and share > 0%",
            actual="blank" if missing else "incomplete",
            evidence=ev, location=loc, document_type=DocumentType.BENEFICIARY_FORM,
        )]

    total = sum(b.percentage for b in valid)
    if abs(total - 100) > 0.01:
        ev, loc = _quote(ctx, DocumentType.BENEFICIARY_FORM, "percentage")
        return [Issue(
            rule=rule.id, severity=rule.severity,
            message=f"{rule.error_message}: shares total {total:g}% (must be 100%)",
            field="percentage", expected="100", actual=f"{total:g}",
            evidence=ev, location=loc, document_type=DocumentType.BENEFICIARY_FORM,
        )]
    return []


def check_id_valid(rule: "Rule", ctx: RuleContext) -> list[Issue]:
    idd = ctx.fields(DocumentType.ID_DOCUMENT)
    if idd is None:
        return [_missing_doc_issue(rule, DocumentType.ID_DOCUMENT, ctx)]
    issues = []
    if idd.id_type not in ("DL", "Passport"):
        ev, loc = _quote(ctx, DocumentType.ID_DOCUMENT, "id_type")
        issues.append(Issue(
            rule=rule.id, severity=rule.severity,
            message=f"{rule.error_message}: ID type '{idd.id_type or 'unknown'}' is not accepted",
            field="id_type", expected="Driver's license or Passport", actual=idd.id_type or "unknown",
            evidence=ev, location=loc, document_type=DocumentType.ID_DOCUMENT,
        ))
    expiry = _parse_date(idd.expiry_date)
    ev, loc = _quote(ctx, DocumentType.ID_DOCUMENT, "expiry_date")
    if expiry is None:
        issues.append(Issue(
            rule=rule.id, severity=rule.severity, message=f"{rule.error_message}: expiry date not found",
            field="expiry_date", expected="future expiry date", actual=idd.expiry_date or "blank",
            evidence=ev, location=loc, document_type=DocumentType.ID_DOCUMENT, uncertain=True,
        ))
    elif expiry <= ctx.as_of:
        issues.append(Issue(
            rule=rule.id, severity=rule.severity,
            message=f"{rule.error_message}: expired on {expiry.isoformat()}",
            field="expiry_date", expected=f"after {ctx.as_of.isoformat()}", actual=expiry.isoformat(),
            evidence=ev, location=loc, document_type=DocumentType.ID_DOCUMENT,
        ))
    return issues


# --------------------------------------------------------------------------- registry

@dataclass
class Rule:
    definition: RuleDefinition
    check: Callable[["Rule", RuleContext], list[Issue]]

    @property
    def id(self) -> str:
        return self.definition.id

    @property
    def severity(self):
        return self.definition.severity

    @property
    def error_message(self) -> str:
        return self.definition.error_message

    def run(self, ctx: RuleContext) -> list[Issue]:
        return self.check(self, ctx)


def _rule(id, description, severity, error_message, docs, check) -> Rule:
    return Rule(
        RuleDefinition(
            id=id, description=description, severity=severity, error_message=error_message,
            version=RULES_VERSION, required_documents=docs,
        ),
        check,
    )


D = DocumentType
RULES: dict[str, Rule] = {r.id: r for r in [
    _rule("required_fields", "All required fields on application must be filled (name, DOB, email, phone)",
          "error", "Required field left blank", [D.APPLICATION_FORM], check_required_fields),
    _rule("signature_present", "Application must be signed and dated by applicant",
          "error", "Application not signed or dated", [D.APPLICATION_FORM], check_signature_present),
    _rule("date_consistency", "Age on application must match medical records (within 1 year)",
          "error", "Age on the application doesn't match the medical records",
          [D.APPLICATION_FORM, D.MEDICAL_RECORDS], check_date_consistency),
    _rule("medical_recency", f"Medical records must not be older than {settings.medical_recency_years} years",
          "warning", f"Medical records are older than {settings.medical_recency_years} years",
          [D.MEDICAL_RECORDS], check_medical_recency),
    _rule("beneficiary_designated", "Beneficiary must be named, with relationship and share > 0% (shares total 100%)",
          "error", "Beneficiary details incomplete", [D.BENEFICIARY_FORM], check_beneficiary_designated),
    _rule("id_valid", "Valid, unexpired government ID provided (driver's license or passport)",
          "error", "ID not valid", [D.ID_DOCUMENT], check_id_valid),
]}

# Which rules apply to which product. Seeded into the `rules` table by database.init_db().
PRODUCT_RULES: dict[str, list[str]] = {
    "TermLife_10yr": list(RULES),
    "TermLife_20yr": list(RULES),
    "UniversalLife": list(RULES),
}
DEFAULT_PRODUCT = "TermLife_10yr"


def run_rules(rule_ids: list[str], documents: list[LoadedDocument], as_of: date | None = None) -> list[Issue]:
    """Run the given rules. A missing/unreadable document is reported once, by the first rule that needs it."""
    ctx = RuleContext(documents=documents, as_of=as_of or date.today())
    issues: list[Issue] = []
    reported_missing: set[DocumentType] = set()
    for rid in rule_ids:
        for issue in RULES[rid].run(ctx):
            if issue.missing_document:
                if issue.missing_document in reported_missing:
                    continue
                reported_missing.add(issue.missing_document)
            issues.append(issue)
    return issues
