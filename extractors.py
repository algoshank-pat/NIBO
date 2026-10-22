"""Document text extraction, classification and field extraction.

OllamaExtractor  - a local open-weights model (via Ollama) returns a validated DocumentExtraction.
MockExtractor    - regex-based extractor for the sample PDFs (no model needed; used by tests).
"""
from __future__ import annotations

import base64
import io
import re
from datetime import datetime

import httpx
import pypdfium2 as pdfium
from pydantic import BaseModel, ValidationError

from config import settings
from models import (
    ApplicationFormFields, Beneficiary, BeneficiaryFormFields, DocumentExtraction, DocumentType,
    FieldQuote, IdDocumentFields, LoadedDocument, MedicalRecordFields,
)


class ExtractionError(Exception):
    pass


def _pages_block(doc: LoadedDocument) -> str:
    return "\n".join(f"=== Page {i} ===\n{text}" for i, text in enumerate(doc.pages, 1))


# --------------------------------------------------------------------------- Ollama

EXTRACTION_SYSTEM = """You extract data from life-insurance application documents for an underwriting completeness check.

1. Classify the document as exactly one of:
   - application_form: the life insurance application (applicant details, signature)
   - medical_records: medical exam / attending physician statement
   - id_document: government photo ID (driver's license, passport, state ID, etc.)
   - beneficiary_form: beneficiary designation form
   - unknown: anything else
2. Fill ONLY the field object matching that type; leave the other three null.
3. Report values exactly as written. Do not infer, correct or invent values. A blank line,
   underscores, "N/A" or an empty box means the field is missing: return null.
   - Dates: normalise to YYYY-MM-DD. If a date is illegible or partial, return null.
   - signature_present: true only if there is an actual signature or "/s/ Name" mark, not just a label.
   - id_type: "DL" for a driver's license, "Passport" for a passport, "Other" for any other ID.
   - percentage: number without the % sign.
4. For EVERY field you report (including ones you found blank), add a `quotes` entry with the
   verbatim line from the document and its page number. Use these field names:
   application_form: applicant_name, dob, email, phone, signature, signature_date
   medical_records: patient_name, applicant_age, dob, exam_date
   id_document: id_type, full_name, id_number, expiry_date
   beneficiary_form: beneficiary_name, relationship, percentage
5. Use `notes` for anything ambiguous (e.g. hard-to-read handwriting, conflicting values)."""


CONCISE_NOTES_INSTRUCTION = (
    "\nFor notes, return null unless the source contains genuine ambiguity, illegible text, "
    "or conflicting values. Do not restate fields, describe blank fields, explain date "
    "normalization, or explain classification. Keep genuine ambiguity notes brief."
)


class _Transcription(BaseModel):
    text: str


def compact_schema(value):
    """Remove descriptive metadata while preserving JSON-schema constraints."""
    if isinstance(value, dict):
        return {key: compact_schema(item) for key, item in value.items()
                if key not in {"title", "description", "default"}}
    if isinstance(value, list):
        return [compact_schema(item) for item in value]
    return value


class OllamaExtractor:
    """Open-weights model served locally by Ollama. Documents never leave the machine.

    Uses Ollama's JSON-schema constrained output, then validates with Pydantic and retries
    once with the validation error if the model still returns something malformed.
    """
    mode = "ollama"

    def __init__(self, model: str | None = None, vision_model: str | None = None,
                 base_url: str | None = None, client: httpx.Client | None = None):
        self.model = model or settings.ollama_model
        self.vision_model = vision_model or settings.ollama_vision_model or self.model
        self.base_url = (base_url or settings.ollama_base_url).rstrip("/")
        self.client = client or httpx.Client(timeout=settings.ollama_timeout_s)

    def _chat(self, model: str, messages: list[dict], schema: type[BaseModel]) -> BaseModel:
        output_schema = schema.model_json_schema()
        if settings.ollama_compact_schema:
            output_schema = compact_schema(output_schema)
        body = {
            "model": model,
            "messages": messages,
            "format": output_schema,
            "keep_alive": settings.ollama_keep_alive,
            "stream": False,
            "think": settings.ollama_think,
            "options": {"temperature": 0, "num_ctx": settings.ollama_num_ctx},
        }
        last_error = ""
        for _ in range(2):
            try:
                r = self.client.post(f"{self.base_url}/api/chat", json=body)
            except httpx.ConnectError as e:
                raise ExtractionError(f"Ollama not reachable at {self.base_url} - is it running?") from e
            except httpx.TimeoutException as e:
                raise ExtractionError("Ollama timed out while waiting for the model response") from e
            if r.status_code == 404:
                raise ExtractionError(f"Model '{model}' not found in Ollama - run: ollama pull {model}")
            if r.status_code >= 400:
                raise ExtractionError(f"Ollama error {r.status_code}: {r.text[:200]}")

            content = r.json().get("message", {}).get("content", "")
            try:
                return schema.model_validate_json(content)
            except ValidationError as e:
                last_error = str(e)[:500]
                body["messages"] = [*messages,
                                    {"role": "assistant", "content": content},
                                    {"role": "user", "content": "That JSON did not match the schema: "
                                     f"{last_error}\nReturn corrected JSON only."}]
        raise ExtractionError(f"Model returned invalid JSON twice: {last_error}")

    def transcribe_pdf(self, pdf_bytes: bytes) -> list[str]:
        """Fallback for scanned/image PDFs: render each page and have the vision model read it."""
        pages = []
        for png in render_pdf_pages(pdf_bytes):
            result = self._chat(self.vision_model, [{
                "role": "user",
                "content": "Transcribe all text on this scanned page verbatim. Keep the 'Label: value' "
                           "layout of form fields. If there is no legible text, return an empty string.",
                "images": [base64.b64encode(png).decode()],
            }], _Transcription)
            pages.append(result.text.strip())
        return pages

    def extract(self, doc: LoadedDocument) -> DocumentExtraction:
        hint = f"\nUploader's declared type (may be wrong): {doc.declared_type.value}" if doc.declared_type else ""
        system = EXTRACTION_SYSTEM + (CONCISE_NOTES_INSTRUCTION if settings.ollama_concise_notes else "")
        return self._chat(self.model, [
            {"role": "system", "content": system},
            {"role": "user", "content": f"Document filename: {doc.filename}{hint}\n\n"
                                        f"<document>\n{_pages_block(doc)}\n</document>"},
        ], DocumentExtraction)

    def is_available(self) -> bool:
        try:
            r = self.client.get(f"{self.base_url}/api/tags", timeout=3)
            served = {m["name"] for m in r.json().get("models", [])}
            return self.model in served or f"{self.model}:latest" in served
        except (httpx.HTTPError, ValueError, KeyError):
            return False


def render_pdf_pages(pdf_bytes: bytes, scale: float = 2.0) -> list[bytes]:
    """Rasterise PDF pages to PNG bytes (pypdfium2, no system dependencies)."""
    pdf = pdfium.PdfDocument(pdf_bytes)
    out = []
    try:
        for page in pdf:
            buf = io.BytesIO()
            page.render(scale=scale).to_pil().save(buf, format="PNG")
            out.append(buf.getvalue())
    finally:
        pdf.close()
    return out


# --------------------------------------------------------------------------- Mock

_BLANK = re.compile(r"^[\s_\-]*$|^(n/?a|none)$", re.I)

CLASSIFY_KEYWORDS = [
    (DocumentType.BENEFICIARY_FORM, ["BENEFICIARY DESIGNATION"]),
    (DocumentType.MEDICAL_RECORDS, ["MEDICAL EXAMINATION", "ATTENDING PHYSICIAN", "MEDICAL RECORD"]),
    (DocumentType.ID_DOCUMENT, ["DRIVER LICENSE", "DRIVER'S LICENSE", "PASSPORT", "IDENTIFICATION CARD"]),
    (DocumentType.APPLICATION_FORM, ["LIFE INSURANCE APPLICATION"]),
]

LABELS = {
    DocumentType.APPLICATION_FORM: {
        "applicant name": "applicant_name", "full name": "applicant_name", "date of birth": "dob",
        "email": "email", "phone": "phone", "applicant signature": "signature", "date signed": "signature_date",
    },
    DocumentType.MEDICAL_RECORDS: {
        "patient name": "patient_name", "age": "applicant_age", "date of birth": "dob",
        "exam date": "exam_date", "date of examination": "exam_date",
    },
    DocumentType.ID_DOCUMENT: {
        "document type": "id_type", "name": "full_name", "license number": "id_number",
        "passport number": "id_number", "expiration date": "expiry_date", "expiry date": "expiry_date",
    },
    DocumentType.BENEFICIARY_FORM: {
        "primary beneficiary name": "beneficiary_name", "beneficiary name": "beneficiary_name",
        "relationship": "relationship", "percentage": "percentage", "share": "percentage",
    },
}


def locate_field_quote(doc: LoadedDocument, field_name: str) -> FieldQuote | None:
    """Locate source evidence without changing extracted values or rule outcomes."""
    labels = LABELS.get(doc.doc_type, {})
    lines = [(page_no, line.strip()) for page_no, page in enumerate(doc.pages, 1)
             for line in page.splitlines() if line.strip()]
    for page_no, line in lines:
        label = line.partition(":")[0].strip().lower()
        if labels.get(label) == field_name:
            return FieldQuote(field=field_name, quote=line, page=page_no)

    fields = getattr(doc.extraction, doc.doc_type.value, None) if doc.extraction else None
    if fields is None:
        return None
    if doc.doc_type == DocumentType.BENEFICIARY_FORM:
        attribute = "name" if field_name == "beneficiary_name" else field_name
        values = [getattr(b, attribute, None) for b in fields.beneficiaries]
    else:
        values = [getattr(fields, field_name, None)]

    candidates: set[str] = set()
    for value in values:
        if value is None or isinstance(value, bool):
            continue
        text = str(value).strip()
        if not text or _BLANK.fullmatch(text):
            continue
        candidates.add(text)
        if isinstance(value, float):
            candidates.add(f"{value:g}")
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            parsed = datetime.strptime(text, "%Y-%m-%d") if _norm_date(text) else None
            if parsed:
                candidates.update(parsed.strftime(fmt) for fmt in
                                  ("%m/%d/%Y", "%d %b %Y", "%B %d, %Y"))
    if not candidates:
        return None
    patterns = []
    for value in sorted(candidates):
        escaped = re.escape(value)
        if re.fullmatch(r"\d+(?:\.\d+)?", value):
            patterns.append(r"(?<![\w.])" + escaped + r"(?!\w|\.\d)")
        else:
            patterns.append(r"(?<!\w)" + escaped + r"(?!\w)")
    pattern = re.compile("|".join(patterns), re.I)
    matches = [(page_no, line) for page_no, line in lines if pattern.search(line)]
    if len(matches) == 1:
        page_no, line = matches[0]
        return FieldQuote(field=field_name, quote=line, page=page_no)
    return None


def _norm_date(value: str | None) -> str | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d %b %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(value.strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return None


class MockExtractor:
    """Deterministic 'Label: value' parser. Good enough for the sample packets, not for real forms."""
    mode = "mock"

    def transcribe_pdf(self, pdf_bytes: bytes) -> list[str]:
        raise ExtractionError("No text layer and no model configured (mock mode) - cannot OCR")

    def classify(self, doc: LoadedDocument) -> tuple[DocumentType, float]:
        upper = doc.text.upper()
        for doc_type, words in CLASSIFY_KEYWORDS:
            if any(w in upper for w in words):
                return doc_type, 0.95
        if doc.declared_type:
            return doc.declared_type, 0.6
        return DocumentType.UNKNOWN, 0.4

    def extract(self, doc: LoadedDocument) -> DocumentExtraction:
        doc_type, conf = self.classify(doc)
        if doc_type == DocumentType.UNKNOWN:
            return DocumentExtraction(doc_type=doc_type, classification_confidence=conf)

        labels = LABELS[doc_type]
        values: list[tuple[str, str | None]] = []
        quotes: list[FieldQuote] = []
        for page_no, page in enumerate(doc.pages, 1):
            for line in page.splitlines():
                if ":" not in line:
                    continue
                label, _, raw = line.partition(":")
                key = labels.get(label.strip().lower())
                if not key:
                    continue
                raw = raw.strip()
                values.append((key, None if _BLANK.match(raw) else raw))
                quotes.append(FieldQuote(field=key, quote=line.strip(), page=page_no))

        first = {}
        for k, v in values:
            first.setdefault(k, v)

        ext = DocumentExtraction(doc_type=doc_type, classification_confidence=conf, quotes=quotes)
        if doc_type == DocumentType.APPLICATION_FORM:
            ext.application_form = ApplicationFormFields(
                applicant_name=first.get("applicant_name"), dob=_norm_date(first.get("dob")),
                email=first.get("email"), phone=first.get("phone"),
                signature_present=bool(first.get("signature")),
                signature_date=_norm_date(first.get("signature_date")),
            )
        elif doc_type == DocumentType.MEDICAL_RECORDS:
            age = first.get("applicant_age")
            ext.medical_records = MedicalRecordFields(
                patient_name=first.get("patient_name"),
                applicant_age=int(re.sub(r"\D", "", age)) if age and re.search(r"\d", age) else None,
                dob=_norm_date(first.get("dob")), exam_date=_norm_date(first.get("exam_date")),
            )
        elif doc_type == DocumentType.ID_DOCUMENT:
            t = (first.get("id_type") or "").lower()
            id_type = "DL" if "driver" in t else "Passport" if "passport" in t else ("Other" if t else None)
            ext.id_document = IdDocumentFields(
                id_type=id_type, full_name=first.get("full_name"), id_number=first.get("id_number"),
                expiry_date=_norm_date(first.get("expiry_date")),
            )
        else:
            bens: list[Beneficiary] = []
            for k, v in values:
                if k == "beneficiary_name":
                    bens.append(Beneficiary(name=v))
                elif bens and k == "relationship":
                    bens[-1].relationship = v
                elif bens and k == "percentage":
                    num = re.sub(r"[^\d.]", "", v or "")
                    bens[-1].percentage = float(num) if num else None
            ext.beneficiary_form = BeneficiaryFormFields(beneficiaries=bens)
        return ext


def get_extractor():
    """`ollama` -> always Ollama; `mock` -> regex extractor; `auto` -> Ollama if the model is being served."""
    if settings.llm_mode == "mock":
        return MockExtractor()
    ollama = OllamaExtractor()
    if settings.llm_mode == "ollama" or ollama.is_available():
        return ollama
    return MockExtractor()
