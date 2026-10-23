"""FastAPI service for the Application Completeness Agent.

    uvicorn app:app --reload

POST /analyze  -> reads the case details from the documents, runs the agent, stores the proposal + audit
                  entry, returns CompletionProposal.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import database as db
from agent import DocumentLoadError, _pypdf_pages, analyze_application, read_document_bytes
from extractors import OllamaExtractor, get_extractor
from config import settings
from intake import detect_case_details
from models import AnalyzeRequest, ApplicationRequest, CompletionProposal, DecisionRequest

log = logging.getLogger("nibo")


def settings_default_product() -> str:
    return ApplicationRequest.model_fields["policy_product"].default


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db()
    mode = get_extractor().mode
    log.info("NIBO agent ready (mode=%s, model=%s)", mode, settings.ollama_model if mode == "ollama" else None)
    yield


app = FastAPI(title="Application Completeness Agent (NIBO)", version="0.3.0", lifespan=lifespan)


@app.get("/health")
def health() -> dict:
    mode = get_extractor().mode
    return {"status": "ok", "agent_mode": mode,
            "model": settings.ollama_model if mode == "ollama" else None,
            "rules_version": settings.rules_version}


def _packet_texts(blobs: list[bytes]) -> list[str]:
    texts = []
    for data in blobs:
        try:
            texts.append("\n".join(_pypdf_pages(data)))
        except Exception:  # noqa: BLE001 - unreadable here is handled later by the agent
            texts.append("")
    return texts


@app.post("/analyze", response_model=CompletionProposal)
async def analyze(upload: AnalyzeRequest) -> CompletionProposal:
    request = upload
    if not request.documents:
        raise HTTPException(400, detail="No documents provided. Upload at least the application form.")
    if settings.llm_mode == "ollama" and not OllamaExtractor().is_available():
        raise HTTPException(503, detail=f"Ollama model '{settings.ollama_model}' is not being served at "
                                        f"{settings.ollama_base_url}")

    # Fail fast on documents we can't even open (bad path, bad base64, not a PDF).
    load_errors, blobs = [], []
    for doc in request.documents:
        try:
            blobs.append(read_document_bytes(doc))
        except DocumentLoadError as e:
            load_errors.append(str(e))
    if load_errors:
        raise HTTPException(400, detail={"message": "One or more documents could not be loaded",
                                         "errors": load_errors})

    # Case intake: the application number and product come from the documents unless supplied.
    texts = _packet_texts(blobs)
    if any(t.strip() for t in texts):
        found = detect_case_details(texts)
        needs, problems = [], []
        if not upload.application_id:
            if found.application_id is None:
                needs.append("application_id")
                problems.append(next(p for p in found.problems if "application" in p.lower()))
        if not upload.policy_product:
            if found.policy_product is None:
                needs.append("policy_product")
                problems.append(next(p for p in found.problems if "product" in p.lower()))
        if needs:
            raise HTTPException(422, detail={"message": " ".join(problems), "needs": needs})
        application_id = upload.application_id or found.application_id
        policy_product = upload.policy_product or found.policy_product
    else:
        # No text layer at all (e.g. only scans): the number can't be read, so ask for it.
        if not upload.application_id:
            raise HTTPException(422, detail={
                "message": "These documents have no readable text, so the application number couldn't be found.",
                "needs": ["application_id"]})
        application_id = upload.application_id
        policy_product = upload.policy_product or settings_default_product()
    request = ApplicationRequest(
        application_id=application_id, policy_product=policy_product, documents=upload.documents,
        resolution_context=upload.resolution_context, applicant_age=upload.applicant_age)

    try:
        proposal, state = await run_in_threadpool(analyze_application, request, persist=False)
    except Exception as e:  # noqa: BLE001 - never leak a stack trace to the portal
        log.exception("Agent failed for %s", request.application_id)
        raise HTTPException(500, detail=f"Agent failed: {type(e).__name__}: {e}") from e

    if state.documents and not any(d.readable for d in state.documents):
        raise HTTPException(422, detail={
            "message": "PDF extraction failed for every document; please re-upload readable PDFs",
            "errors": {d.filename: d.error for d in state.documents},
        })
    db.save_proposal(proposal, request.policy_product)
    return proposal


@app.get("/applications/{application_id}/audit")
def audit(application_id: str) -> dict:
    """Read-only proposal, decision and execution audit trail."""
    app_row = db.get_application(application_id)
    if not app_row:
        raise HTTPException(404, detail=f"Unknown application {application_id}")
    app_row.pop("latest_proposal", None)
    return {"application": app_row,
            "audit_log": [e.model_dump(mode="json") for e in db.get_audit_log(application_id)]}


@app.get("/applications/{application_id}")
def application(application_id: str) -> dict:
    row = db.get_application(application_id)
    if row is None:
        raise HTTPException(404, detail=f"Unknown application {application_id}")
    row["proposal"] = json.loads(row.pop("latest_proposal")) if row["latest_proposal"] else None
    row["final_issues"] = json.loads(row["final_issues"]) if row.get("final_issues") else None
    return row


@app.post("/applications/{application_id}/decision")
def decide(application_id: str, request: DecisionRequest) -> dict:
    try:
        return db.save_decision(application_id, request)
    except KeyError:
        raise HTTPException(404, detail=f"Unknown application {application_id}") from None
    except db.DecisionConflict as error:
        raise HTTPException(409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(422, detail=str(error)) from error


DIST_DIR = Path(__file__).resolve().parent / "dist"
if (DIST_DIR / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=DIST_DIR / "assets"), name="assets")


@app.get("/", include_in_schema=False)
def portal():
    if not (DIST_DIR / "index.html").is_file():
        raise HTTPException(503, detail="Build the portal with npm install and npm run build")
    return FileResponse(DIST_DIR / "index.html")
