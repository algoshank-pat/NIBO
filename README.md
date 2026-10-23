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

1. Choose the packet's PDFs and click **Analyze case**. The case number and product are read from document text; missing or conflicting details prompt for confirmation.
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

`POST /analyze` accepts an `AnalyzeRequest`. The application ID and product are optional overrides; omitted values are detected from document text:

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

`load_documents → classify_documents → retrieve_rules → analyze → propose_status → resolve_gaps → build_proposal`

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
The current suite has 126 tests. See `PERFORMANCE.md` for live model measurements, benchmark commands,
and the remaining latency gap.
Git setup and push to `https://github.com/algoshank-pat/NIBO` are authorized; deployment remains
prohibited. `.gitignore` excludes local databases, PDF packets, raw benchmark logs, environments,
dependencies and build output. Generate samples and build the portal after cloning; the raw timing
logs referenced in `PERFORMANCE.md` remain local. See `AGENTS.md`.


## Gap resolution extension (October 23, 2026)

After the initial status proposal, a seventh LangGraph node chooses a tool route for each gap,
executes bounded parallel attempts, and re-runs the unchanged rules before building the final proposal.
Routing is deterministic by rule/document type; this is a bounded agentic workflow, not an LLM planner.
The model does not make completeness or approval decisions.

- **Driver's license gaps:** try a DMV lookup only when an actual license number, issuing state,
  and applicant name are available. The fetched state, license number and name must match. A valid,
  unsuspended record must also pass the existing `id_valid` rule at the analysis date. The resolver
  cannot renew a license: an expired provider record leaves the gap pending.
- **Medical gaps:** the connector returns synthetic example data only. Medical findings remain
  pending manual requests, including missing medical records reported by another rule.
- **Other known gaps:** record a manual request; no email or external message is sent.
- **Unknown rule:** mark unresolvable for underwriter review.
- Provider errors, malformed responses, identity conflicts and timeouts fall back to manual handling.

DMV **mock mode is the default**, with no fixture records. It does not silently validate arbitrary
IDs. Successful mock resolutions are explicitly labelled as simulation and require underwriter
judgment. The generic HTTP adapter is only a contract placeholder (`POST /licenses/verify` over
HTTPS), tested with mocked HTTP. No real state DMV endpoint, access credentials or provider contract
has been integrated or verified. Do not claim a real DMV integration or an 80% auto-resolution rate.

`POST /analyze` accepts optional lookup context, separate from PDF-extracted facts:

```json
"resolution_context": {
  "applicant_state": "CA",
  "driver_license_number": "THE_ACTUAL_LICENSE_NUMBER",
  "applicant_id": "OPTIONAL_MEDICAL_SUBJECT_ID",
  "provider_npi": "OPTIONAL_PROVIDER_ID"
}
```

The license number can be omitted from context if extracted from the uploaded driver's license.
Issuing state must be supplied; it is not guessed from the applicant's address or the issue's field
name. The current upload form does not collect this extra context; use the API for a DMV demo.

Configure synthetic fixture data with `NIBO_GAP_DMV_RECORDS` as a JSON object keyed by
`STATE:LICENSE_NUMBER`. Each record needs `state`, `driver_license_number`, `full_name`, boolean
`valid`, and ISO `expiration_date`. Optional fields: integer `violations` and boolean `suspensions`.
Fixtures must match the test applicant exactly. Example fixture shape (not an installed record):

```json
{"CA:DEMO123":{"state":"CA","driver_license_number":"DEMO123","full_name":"Demo Applicant","valid":true,"expiration_date":"2035-01-01","violations":0,"suspensions":false}}
```

`NIBO_GAP_RESOLUTION_ENABLED=false` disables attempts. Other settings are listed in `.env.example`:
per-attempt timeout defaults to 10 seconds, batch deadline to 30 seconds, and parallelism to four.
The original PDFs/extractions are preserved. Verified API evidence is prepended as a separately
identified source; unverified candidates are excluded from the active rule inputs.

Responses add `gap_resolutions`, `resolved_issues_count`, `pending_issues_count`, and
`unresolvable_issues_count`. Each resolution includes the original issue, method/status, timestamp,
confidence, trace, returned data/evidence and simulation indicator. Counts appear in the existing
portal summary and business-language audit follow-up details. The final proposal and all
attempt records are serialized into the existing append-only `agent_proposal` audit snapshot when
analysis is persisted, rather than introducing new database tables or standalone audit event types.
Application status stays `PROPOSED` until the unchanged underwriter decision flow executes.

Run the focused PDF-to-resolution demonstration without Ollama, keys or real provider calls:

```powershell
.venv/Scripts/python -m pytest -q tests/test_gap_resolver.py::test_full_pdf_to_verified_proposal_and_append_only_audit
.venv/Scripts/python -m pytest -q tests/test_gap_resolver.py
```

The integration test creates synthetic PDFs in a temporary directory, starts with an expired ID,
retrieves an explicitly configured mock DMV record, verifies it with the deterministic rules,
proposes READY, and checks the persisted proposal/audit while status remains PROPOSED.

## Case intake and visual audit

The dashboard leads with missing information, evidence and agent follow-up results. Use **Open case** to retrieve an existing review. The audit process bar summarizes the latest review and later human decision; it is reconstructed from saved events, not live node telemetry. Notification remains pending because letters are stored only and never sent.

When document text cannot identify the case or product, `/analyze` returns HTTP 422 with `detail: {"message": "...", "needs": ["application_id", "policy_product"]}` (only missing fields are listed). Supply those fields and retry. Scan-only packets require a supplied case ID and use the existing default product unless overridden. Intake uses deterministic text matching; document classification/extraction still uses the configured extractor.
