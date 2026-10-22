"""Application Completeness Agent (NIBO) - LangGraph state machine.

load_documents -> classify_documents -> retrieve_rules -> analyze -> propose_status -> build_proposal

The agent only PROPOSES a status. It never approves, sends letters or changes the
application record; that happens after an underwriter decision (Day 3).
"""
from __future__ import annotations

import base64
import binascii
import io
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

from langgraph.graph import END, START, StateGraph
from pypdf import PdfReader
from pypdf.errors import PdfReadError

import database as db
from config import settings
from letters import letter_text
from extractors import ExtractionError, MockExtractor, OllamaExtractor, get_extractor
from models import (
    AnalysisState, ApplicationRequest, CompletionProposal, DocumentInput, DocumentType,
    EvidenceSnapshot, ExtractedDocumentSnapshot, Issue, IssueDetail, LoadedDocument, Proposal,
)
from rules import DEFAULT_PRODUCT, run_rules


class DocumentLoadError(Exception):
    """The document bytes could not be obtained (bad path / bad base64 / not a PDF)."""


def read_document_bytes(doc: DocumentInput) -> bytes:
    if doc.path:
        p = Path(doc.path)
        if not p.is_file():
            raise DocumentLoadError(f"File not found: {doc.path}")
        data = p.read_bytes()
    else:
        try:
            data = base64.b64decode(doc.content_base64, validate=True)
        except (binascii.Error, ValueError) as e:
            raise DocumentLoadError(f"{doc.filename}: invalid base64 ({e})") from e
    if not data.lstrip()[:5].startswith(b"%PDF"):
        raise DocumentLoadError(f"{doc.filename}: not a PDF file")
    return data


def _pypdf_pages(data: bytes) -> list[str]:
    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception as e:  # noqa: BLE001 - pypdf raises several types here
            raise PdfReadError("PDF is password-protected") from e
    return [(page.extract_text() or "").strip() for page in reader.pages]


def _log(state: AnalysisState, msg: str) -> list[str]:
    return [*state.trace, msg]


# --------------------------------------------------------------------------- nodes

def make_nodes(extractor):
    def load_documents(state: AnalysisState) -> dict:
        docs: list[LoadedDocument] = []
        for i, inp in enumerate(state.inputs):
            d = LoadedDocument(doc_id=f"doc_{i + 1}", filename=inp.filename, declared_type=inp.declared_type)
            try:
                data = read_document_bytes(inp)
                d.pages = _pypdf_pages(data)
                if not any(d.pages):
                    # No text layer (scanned image) - render pages and let the vision model read them.
                    d.pages = extractor.transcribe_pdf(data)
                    d.text_source = "vision_ocr"
                if not any(p.strip() for p in d.pages):
                    raise ExtractionError("no readable text")
            except (DocumentLoadError, PdfReadError, ExtractionError, ValueError) as e:
                d.readable, d.error, d.text_source = False, str(e), "none"
            docs.append(d)

        unreadable = [d.filename for d in docs if not d.readable]
        msg = f"load_documents: {len(docs)} loaded, {len(unreadable)} unreadable {unreadable or ''}".strip()
        return {"documents": docs, "trace": _log(state, msg)}

    def classify_documents(state: AnalysisState) -> dict:
        docs = [d.model_copy(deep=True) for d in state.documents]

        def work(d: LoadedDocument) -> LoadedDocument:
            if not d.readable:
                return d
            try:
                d.extraction = extractor.extract(d)
            except ExtractionError as e:
                d.readable, d.error = False, f"classification/extraction failed: {e}"
            return d

        with ThreadPoolExecutor(max_workers=settings.extraction_workers) as pool:
            docs = list(pool.map(work, docs))

        extracted_text: dict[str, str] = {}
        for d in docs:
            if d.readable:
                key = d.doc_type.value
                extracted_text[key] = (extracted_text.get(key, "") + "\n" + d.text).strip()

        summary = ", ".join(f"{d.filename}={d.doc_type.value}" for d in docs)
        return {"documents": docs, "extracted_text": extracted_text,
                "trace": _log(state, f"classify_documents: {summary}")}

    def retrieve_rules(state: AnalysisState) -> dict:
        product = state.policy_product
        rules = db.get_rules_for_product(product)
        note = ""
        if not rules:
            rules = db.get_rules_for_product(DEFAULT_PRODUCT)
            note = f" (no rules for '{product}', using {DEFAULT_PRODUCT})"
        return {"rules": rules,
                "trace": _log(state, f"retrieve_rules: {len(rules)} rules v{settings.rules_version}{note}")}

    def analyze(state: AnalysisState) -> dict:
        rule_ids = [r.id for r in state.rules]
        issues = run_rules(rule_ids, state.documents, state.as_of)
        return {"rules_applied": rule_ids, "issues_found": issues,
                "trace": _log(state, f"analyze: {len(issues)} issue(s) from {len(rule_ids)} rules")}

    def propose_status(state: AnalysisState) -> dict:
        proposal = compute_proposal(state)
        return {"proposal": proposal,
                "trace": _log(state, f"propose_status: {proposal.status} @ {proposal.confidence:.2f}")}

    def build_proposal(state: AnalysisState) -> dict:
        completion = assemble_completion(state, extractor)
        return {"completion": completion, "trace": _log(state, "build_proposal: done")}

    return load_documents, classify_documents, retrieve_rules, analyze, propose_status, build_proposal


# --------------------------------------------------------------------------- decision logic

def compute_proposal(state: AnalysisState) -> Proposal:
    issues = state.issues_found
    errors = [i for i in issues if i.severity == "error"]
    warnings = [i for i in issues if i.severity == "warning"]
    uncertain = [i for i in issues if i.uncertain]
    unknown_docs = [d for d in state.documents if d.readable and d.doc_type == DocumentType.UNKNOWN]

    # Confidence = how sure the agent is of its own verdict, not the pass rate:
    #   classification quality of the documents it read, and how many rules it could
    #   evaluate on solid data (rules that hit unreadable/unextractable data count against it).
    classified = [d.extraction.classification_confidence for d in state.documents if d.extraction]
    cls_conf = sum(classified) / len(classified) if classified else 0.0
    n_rules = max(len(state.rules_applied), 1)
    uncertain_rules = {i.rule for i in uncertain}
    coverage = 1 - len(uncertain_rules) / n_rules
    confidence = 0.6 * cls_conf + 0.4 * coverage
    if unknown_docs:
        confidence -= 0.05 * len(unknown_docs)

    next_steps: list[str] = []
    if errors:
        status, alternative = "NIBO", None
        reasoning = (f"{len(errors)} error-level issue(s) make the application incomplete"
                     + (f", plus {len(warnings)} warning(s)" if warnings else "") + ".")
        next_steps.append("Underwriter: review evidence and approve NIBO status")
        next_steps.append("On approval, store the outstanding-requirements letter for review")
    elif warnings:
        # Warnings alone still default to NIBO, but the underwriter may reasonably override to READY.
        status, alternative = "NIBO", "READY"
        confidence = min(confidence, 0.75)
        reasoning = (f"No errors, but {len(warnings)} warning(s). Warnings default to NIBO; "
                     "underwriter may override to READY.")
        next_steps.append("Underwriter: decide whether the warning(s) justify NIBO or can be waived (READY)")
    else:
        status, alternative = "READY", None
        reasoning = f"All {len(state.rules_applied)} rules passed on the documents provided."
        next_steps.append("Underwriter: confirm READY and route to underwriting")

    if uncertain:
        next_steps.append("Agent could not verify some items (see minor issues) - check source documents")
    for d in state.documents:
        if not d.readable:
            next_steps.append(f"Request re-upload of unreadable document: {d.filename}")
    for d in unknown_docs:
        next_steps.append(f"Review unrecognised document: {d.filename}")

    confidence = round(max(0.05, min(confidence, 0.99)), 2)
    return Proposal(
        status=status, confidence=confidence, reasoning=reasoning, next_steps=next_steps,
        alternative_status=alternative,
        requires_underwriter_judgment=bool(alternative or uncertain or confidence < 0.7),
    )


def _detail_severity(issue: Issue) -> str:
    if issue.uncertain:
        return "minor"
    return "critical" if issue.severity == "error" else "major"


def _applicant_name(state: AnalysisState) -> str | None:
    for d in state.documents:
        if d.extraction and d.extraction.application_form and d.extraction.application_form.applicant_name:
            return d.extraction.application_form.applicant_name
    return None


def build_letter(state: AnalysisState, proposal: Proposal) -> str:
    if proposal.status == "READY":
        return ""
    return letter_text(state.application_id, _applicant_name(state) or "Applicant",
                       [issue.message for issue in state.issues_found])


def assemble_completion(state: AnalysisState, extractor) -> CompletionProposal:
    p = state.proposal
    issues = state.issues_found
    details = [
        IssueDetail(issue=i.message, rule=i.rule, severity=_detail_severity(i), evidence=i.evidence,
                    location=i.location, field=i.field, expected=i.expected, actual=i.actual)
        for i in issues
    ]
    order = {"critical": 0, "major": 1, "minor": 2}
    details.sort(key=lambda d: order[d.severity])

    if issues:
        counts = {s: sum(d.severity == s for d in details) for s in order}
        parts = [f"{n} {s}" for s, n in counts.items() if n]
        summary = f"{p.status}: {len(issues)} issue(s) ({', '.join(parts)}). " + "; ".join(d.issue for d in details)
    else:
        summary = f"READY: all {len(state.rules_applied)} rules passed."

    snapshots: dict[str, ExtractedDocumentSnapshot] = {}
    for d in state.documents:
        ext = d.extraction
        fields = ext.model_dump(include={d.doc_type.value}).get(d.doc_type.value) or {} if ext else {}
        snapshots[d.doc_id] = ExtractedDocumentSnapshot(
            filename=d.filename, doc_type=d.doc_type,
            classification_confidence=ext.classification_confidence if ext else 0.0,
            readable=d.readable, text_source=d.text_source, fields=fields,
            text_excerpt=(d.text[:500] if d.readable else (d.error or "")),
        )

    return CompletionProposal(
        application_id=state.application_id,
        proposed_status=p.status,
        alternative_status=p.alternative_status,
        requires_underwriter_judgment=p.requires_underwriter_judgment,
        confidence_score=p.confidence,
        issues_summary=summary,
        issues_detail=details,
        reasoning=p.reasoning,
        next_steps=p.next_steps,
        evidence_snapshot=EvidenceSnapshot(
            extracted_documents=snapshots, rules_checked=state.rules_applied,
            rules_version=settings.rules_version,
        ),
        recommended_letter_text=build_letter(state, p),
        agent_mode=extractor.mode,
        model=extractor.model if isinstance(extractor, OllamaExtractor) else None,
    )


# --------------------------------------------------------------------------- graph

def build_graph(extractor=None):
    extractor = extractor or get_extractor()
    nodes = make_nodes(extractor)
    g = StateGraph(AnalysisState)
    names = []
    for fn in nodes:
        g.add_node(fn.__name__, fn)
        names.append(fn.__name__)
    g.add_edge(START, names[0])
    for a, b in zip(names, names[1:]):
        g.add_edge(a, b)
    g.add_edge(names[-1], END)
    return g.compile()


def analyze_application(
    request: ApplicationRequest, *, extractor=None, as_of: date | None = None, persist: bool = True,
) -> tuple[CompletionProposal, AnalysisState]:
    """Run the agent end to end. Persists the proposal + audit entry unless persist=False."""
    extractor = extractor or get_extractor()
    graph = build_graph(extractor)
    initial = AnalysisState(
        application_id=request.application_id, policy_product=request.policy_product,
        applicant_age=request.applicant_age, inputs=request.documents, as_of=as_of,
        agent_mode=extractor.mode,
    )
    result = graph.invoke(initial, config={"run_name": f"nibo-{request.application_id}",
                                           "metadata": {"run_id": uuid.uuid4().hex}})
    state = AnalysisState.model_validate(result)
    if persist:
        db.save_proposal(state.completion, request.policy_product)
    return state.completion, state


if __name__ == "__main__":
    import json
    import sys

    if len(sys.argv) < 3:
        print("usage: python agent.py APPLICATION_ID file1.pdf [file2.pdf ...]")
        sys.exit(1)
    db.init_db()
    req = ApplicationRequest(application_id=sys.argv[1], documents=[DocumentInput(path=p) for p in sys.argv[2:]])
    proposal, st = analyze_application(req)
    print("\n".join(st.trace))
    print(json.dumps(proposal.model_dump(mode="json"), indent=2))
