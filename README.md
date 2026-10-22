# NIBO Application Completeness Agent

A local life-insurance packet review app: PDF upload → deterministic completeness checks →
evidence-backed proposal → explicit underwriter decision → stored letter and append-only audit.
The React portal is served by the existing FastAPI app. Product design: `nibo-agent-design.md` (included for a self-contained clone).

## Run (PowerShell)

Use the existing Python 3.14 virtualenv. From this project root:

```powershell
.venv/Scripts/python -m pip install -r requirements.txt
npm ci
npm run build
.venv/Scripts/python database.py
.venv/Scripts/python -m uvicorn app:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000/`. API docs: `/docs`.
For a model-free demonstration, set `$env:NIBO_LLM_MODE='mock'` before starting the server.
Generate the synthetic sample PDFs with:

```powershell
$env:NIBO_LLM_MODE='mock'
.venv/Scripts/python sample_applications.py
```

Mock mode understands the sample forms only. It never calls Ollama. For real documents,
run local Ollama with `gemma4` installed and set `$env:NIBO_LLM_MODE='ollama'`.
`auto` uses Ollama when the configured model is available, otherwise the sample-only mock.
The portal displays the selected mode.

During frontend development, run `npm run dev` in a second terminal. Vite proxies the API to
FastAPI on port 8000. Rebuild with `npm run build` for FastAPI to serve the updated UI.

## Review workflow

1. Enter an application ID and product; choose the packet's PDFs; click **Analyze packet**.
2. Review the proposed status, evidence quotes, page locations, reasoning, and letter preview.
   The stored application status remains `PROPOSED`.
3. Enter your underwriter name/ID and notes, then choose:
   - **Approve:** accepts the proposed NIBO/READY status.
   - **Reject proposal:** overrides to READY.
   - **Modify requirements:** edit/add/remove issues and choose a final status, then explicitly
     **Save changes & approve**. Cancel leaves the original proposal unchanged.
4. Review/download the stored letter and inspect the audit trail. Nothing sends email.
   READY decisions do not create an outstanding-requirements letter.

Use **Open review** with an existing application ID to resume or view a completed review.
The original agent proposal stays intact; human-edited issues are stored separately and included
in the decision audit record. Reanalysis creates a fresh pending proposal and clears the current
final issues/letter, while earlier proposals, decisions, and newly recorded letter snapshots remain in the immutable audit.

## API

`POST /analyze` accepts an `ApplicationRequest`:

```json
{
  "application_id": "APP_1001",
  "policy_product": "TermLife_10yr",
  "documents": [{"path": "sample_data/APP_1001/application_form.pdf"}]
}
```

Each document accepts either a local `path` or `filename` + `content_base64`.
Returns `CompletionProposal`. Errors include 400 for missing/invalid PDFs, 422 if all documents
are unreadable, and 503 if required Ollama is unavailable.

- `GET /health`: active extractor mode/model.
- `GET /applications/{id}`: proposal, current status, final issues and stored letter.
- `GET /applications/{id}/audit`: proposal/decision/execution audit entries.
- `POST /applications/{id}/decision`:

```json
{
  "decided_by": "UW_Jane",
  "action": "approve",
  "expected_proposal_timestamp": "COPY agent_timestamp FROM THE REVIEWED PROPOSAL",
  "notes": "Reviewed the evidence"
}
```

For `modify`, also provide `modified_issues` (the edited `issues_detail` array) and explicit
`final_status`. A NIBO modification needs at least one described issue. `approve` cannot override
the proposed status; `reject` always selects READY. Unknown applications return 404; stale or
already-finalized proposals return 409. Status, letter, decision, and both audit writes share one
SQLite transaction; concurrent submissions can finalize a proposal only once. Decision timestamps
are assigned by the server. The local prototype records a supplied underwriter ID; it has no
identity-provider integration.

## Extraction, rules, and evidence

`load_documents → classify_documents → retrieve_rules → analyze → propose_status → build_proposal`

Only open-weights models served by local Ollama read/classify/extract documents. No hosted LLM
API is used. Scanned PDFs are rendered with pypdfium2 and transcribed by the vision model.
The model never decides pass/fail: the six Python rules remain deterministic and versioned.
Model output is Pydantic-validated with one corrective retry.
CPU extraction defaults to one worker (`NIBO_EXTRACTION_WORKERS`, range 1–4). The concise-notes
instruction (`NIBO_OLLAMA_CONCISE_NOTES=true`) reserves notes for genuine ambiguity; returned
notes are preserved. All document classification and field extraction still go through Ollama.


When a model omits a field quote, a deterministic locator searches the document-type-specific
label map, then a unique extracted-value match, preserving the source line and page number.
Unmatched or ambiguous value searches retain the explicit missing-evidence message. This does
not replace LLM classification or field extraction.

Locked behavior: rule errors → critical, warnings → major, uncertain findings → minor;
warning-only proposals are NIBO with alternative READY and require underwriter judgment.
Confidence expresses verdict certainty (classification + solid rule coverage), not rules passed;
warning-only confidence is capped at 0.75. Beneficiary shares must total 100%.
Audit UPDATE/DELETE is blocked by SQLite triggers. Existing databases migrate additively on startup.

## Validation and performance

```powershell
.venv/Scripts/python -m pytest -q
npm run build
```

Tests are model-free (mock extractor / mocked Ollama HTTP), covering extraction, evidence,
rules, API, database migration, human decisions, concurrency, rollback, and stored letters.
The current suite has 80 tests. See `PERFORMANCE.md` for live model measurements, benchmark commands,
and the remaining latency gap.
Git setup and push to `https://github.com/algoshank-pat/NIBO` are authorized; deployment remains
prohibited. `.gitignore` excludes local databases, PDF packets, raw benchmark logs, environments,
dependencies and build output. Generate samples and build the portal after cloning; the raw timing
logs referenced in `PERFORMANCE.md` remain local. See `AGENTS.md`.
