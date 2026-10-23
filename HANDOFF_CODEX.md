# Continue the NIBO agent — October 21, 2026

> **Latest status: read "Claude session handoff — October 23, 2026 (latest)" at the end of this
> file first.** It supersedes older sections where they conflict.

Work only in `C:\Maven\Projects\Week3\completeness-agent`. Read `AGENTS.md`, `README.md`, and the
product design `nibo-agent-design.md` first (the original also exists one folder above locally). Use `.venv/Scripts/python`; keep the existing app.

The owner approved all remaining steps in this chat ("go for all steps") and chose **React**
(option 2) for the UI. The work below is implemented. During the application implementation, no deployment, Git commands, or email sending
were authorized or performed. The owner subsequently authorized Git setup and push to
`https://github.com/algoshank-pat/NIBO` on October 21, 2026. A regex bypass was not approved or implemented.

## Baseline and current validation

- Initial Day 2 baseline: 40 tests passed after resolving sandbox temporary-directory and local
  socket restrictions. All three mock samples matched NIBO / READY / NIBO with alternative READY.
- After evidence fallback: 54 model-free tests passed.
- Current suite: 80 model-free tests; run `.venv/Scripts/python -m pytest -q` after every change.
- `npm run build` builds the React portal; FastAPI serves it at `/`. Vite development proxy targets
  FastAPI port 8000. Python dependencies remain in `requirements.txt`, JS dependencies in the
  root `package.json` / lockfile. No second application was scaffolded.
- Browser verification used synthetic data and a separate `.ui-smoke.db`: upload/analyze, approve
  NIBO with stored letter, approve READY without letter, reject-to-READY, modify-and-approve with
  regenerated letter, persisted review lookup and audit trail. Desktop and narrow layouts were
  checked; the narrow view had no horizontal overflow; no browser console warnings/errors appeared.

## Evidence quotes — implemented and live verified

- `extractors.locate_field_quote()` reuses the document-type `LABELS` map, then a unique
  extracted-value match, preserving source line and page. Date aliases are supported and numeric
  substring matches (including decimals) are rejected.
- `rules._quote()` preserves nonempty extractor quotes, then uses the locator for missing/empty
  quotes. Extraction values and pass/fail checks remain unchanged. Unmatched or ambiguous values
  keep the explicit missing-evidence message.
- Live baseline gemma4 supplied no field quotes; proposals still quoted the blank beneficiary line
  and stale exam date correctly, at page 1 of the corresponding PDFs.

## Slow runs — measured, target remains unmet

See `PERFORMANCE.md` and the `benchmark_*.jsonl` records. All three baseline packets took
356.993 seconds in total. Lower context, keep-alive, schema trimming and qwen3.6 were tested.
Gemma4 small-packet runs were roughly 87–104 seconds; qwen's warm packet took 154.706 seconds,
and its cold run timed out on two documents. One gemma tuning run omitted medical age.

Retained defaults: gemma4, 16384 context, full schema, explicit 15-minute model residency,
one extraction worker, and a concise-notes instruction that preserves genuine ambiguity.
Earlier cold regression before scheduling/notes changes: APP_1001 in 113.810 seconds, all sample fields matched,
and exactly the expected beneficiary issue/evidence was recovered (`benchmark_final.jsonl`).
Follow-up serial-only APP_1001 runs: 91.181 / 106.638 seconds, all fields matched. Serial + concise
notes across all packets: 99.513 / 88.027 / 137.141 seconds, total 324.681 seconds (9.05% below the
original 356.993-second baseline). All fields, statuses, issues, evidence and locations matched.
These are small ordered trials, not a guaranteed speedup. `NIBO_EXTRACTION_WORKERS=4` and
`NIBO_OLLAMA_CONCISE_NOTES=false` restore the earlier scheduling/prompt. Benchmark flags
`--workers 1 --concise-notes` reproduce the new defaults; its CLI defaults retain the legacy profile.
Smaller context and schema trimming are configurable, not forced defaults. Five seconds per
whole application was not achieved. Do not claim otherwise or bypass the LLM without asking the
owner: reaching it requires a separate model/hardware/strategy decision. No hosted LLM API is used.
The benchmark never persists proposals or decisions; it still initializes/seeds the rules DB.

## Day 3 — implemented

- `GET /applications/{id}` returns the original proposal, current status, final issues and letter.
- `POST /applications/{id}/decision` accepts `DecisionRequest`: underwriter ID, action, reviewed
  proposal's `expected_proposal_timestamp`, optional notes. Approve accepts the proposed status;
  reject selects READY. Modify requires edited `modified_issues` and explicit `final_status`,
  and is an explicit **edit-and-approve** action (the UI says "Save changes & approve").
- Stale/already-finalized proposals return 409; unknown IDs return 404; invalid decisions return
  422. `BEGIN IMMEDIATE` serializes finalization. Status, final issues, stored letter and both audit
  entries commit together. Rollback and concurrent duplicate submissions are tested.
- Original proposals remain unchanged. Human-edited issues are separate and are snapshotted in
  `underwriter_decision`. `system_execution` snapshots newly stored letters as well as flags,
  preserving the exact letter in immutable history after reanalysis. READY generates no letter.
- Existing SQLite files add `final_issues` and `stored_letter_text` columns on initialization;
  existing audit rows and append-only triggers are preserved. Older execution events deserialize
  with no letter snapshot; they are not rewritten.
- React UI: PDF upload, Analyze, evidence/page locations, confidence and warning alternative,
  Approve / Reject / Modify, stored letter download, application lookup and full audit records.
  It handles server errors, elapsed-time progress, stale submissions, and disables finalized decisions.
- This local prototype records a supplied underwriter ID, not an authenticated identity-provider user.

## Hard constraints still apply

Local open-weights Ollama only. LLM reads documents; Python rules decide completeness. Keep mock
mode and model-free tests. Human approval is mandatory before final status. Audit is append-only.
Never send letters/email or deploy. Git setup, commits and push to the owner-specified NIBO
repository are now authorized; keep generated/local data excluded with `.gitignore`. Do not create
another hosted repository without an explicit request. Deployment hosting and the five-second
latency target remain owner-level follow-up decisions.


## Gap resolver extension — October 23, 2026

The owner supplied `GAP_RESOLVER.md` and approved all implementation files. The new scope forbids
committing/pushing until separately approved. This takes precedence over the earlier Git authorization.

Implemented a seventh `resolve_gaps` node after `propose_status`, Pydantic resolution/context/state
models, a mockable DMV provider adapter and mock-only medical connector. The existing rules, API
handler, database schema and underwriter decision logic are unchanged. `config.py` and `.env.example`
add bounded timeouts, parallelism, enabled/mock modes, explicit mock records and a placeholder URL.

Default DMV fixtures are empty and no issuing state is guessed. Pass optional `resolution_context`
to the analysis API; the current upload UI does not collect these identifiers. For a positive demo,
use an explicit matching synthetic DMV fixture or run the focused integration test in README.
Fetched identity must match the state, actual license number and applicant name; provider validity
alone cannot resolve a gap. Both the resolver and final node verify the unchanged deterministic rules.
Original documents remain in the audit evidence; verified provider evidence is a separate active source.

Medical gaps stay pending even if the mock supplies data. Errors/timeouts/invalid/expired/mismatched
records fall back to manual requests, and no messages are sent. Unknown rules are unresolvable.
Per-attempt timeout is 10 seconds; batch deadline 30 seconds; concurrent attempts are capped at four.

Resolution counts/statuses appear in the existing summary, and full traces/results are embedded in
the persisted proposal audit snapshot (not separate event rows). Mock successes are visibly labelled
simulation and require underwriter judgment. Final application status is still PROPOSED pending a human.

Validation: 114 model-free tests, including duplicate gaps, actual-ID lookup, invalid/expired data,
identity conflicts, parallel execution, cancellation/deadlines, HTTP MockTransport, PDF-to-READY
simulation, API audit persistence and unchanged approval, disabling, and async-caller compatibility.
No real DMV/EHR integration, provider keys, live lookup, or 80% real-world resolution rate is claimed.
The earlier five-second inference performance gap remains unchanged.


## Claude session handoff — October 23, 2026 (latest)

Written by Claude (claude.ai session) for Codex to take over. Claude had file access to this folder
but **no shell on the owner's PC**: nothing below was run on Windows, built with npm, or committed.

### Owner direction this session
- Screens must speak business language for an underwriter, not developer/database terms.
- The review screen is the underwriter's dashboard and must lead with **what is missing**.
- The audit trail must show visually **where in the process the case stopped**. No JSON on screen.
- The application number and product must be **read from the documents**, not typed.
- The owner approved each change below ("go") and then said "commit and push" for this work.

### Implemented (all uncommitted)
- `frontend.jsx`, `style.css`
  - **Dashboard:** headline "AI recommends: send back to applicant" / "...ready for underwriting";
    "N items missing before underwriting can continue"; missing items with impact tags
    (critical → Blocks underwriting, major → Needs attention, minor → Please verify), quoted
    evidence, "Found in" location, and an **AI follow-up** line read from
    `proposal.gap_resolutions` (previously never shown); "Resolved by the AI agent" list; file
    checklist (4 document types + `rules_checked` with ✓/✗). Buttons renamed only: approve →
    "Send back to applicant" (or "Approve: ready for underwriting"), reject → "Overrule AI: mark
    complete", modify → "Edit missing items". API actions are unchanged.
  - **Intake:** documents only + "Analyze case". Application number / product inputs appear only
    when `/analyze` returns 422 with `detail.needs`. Separate "Open case" lookup (`lookupId`).
    `api()` now attaches `error.detail` for structured errors.
  - **Audit trail:** `AuditView` + `caseSteps()` derive an 8-step process bar from the audit log
    (latest `agent_proposal` plus the decision/execution events after it): Case received → AI
    document review → Completeness check → AI follow-up → AI recommendation → Underwriter decision
    → Applicant notified → Ready for underwriting. The current step is amber with "STOPPED HERE"
    and waiting time; each step has plain-language details; re-reviews are compared with the
    previous review. The raw JSON dump was removed entirely (owner: "JSON doesn't make sense").
- `intake.py` (new): `detect_case_details(texts)`, deterministic regex only. ID from "Application
  ID/No/Number", then "Ref", then an `APP_` pattern; product from "Product:" normalised to
  `PRODUCT_RULES` keys. Conflicting IDs, missing values or unknown products return `None` plus a
  plain message; it never guesses.
- `models.py`: new `AnalyzeRequest` (optional `application_id` / `policy_product`) for the upload
  endpoint. `ApplicationRequest` and everything the agent uses are unchanged.
- `app.py`: `/analyze` takes `AnalyzeRequest`, reads PDF text with `agent._pypdf_pages`, fills a
  missing ID/product from the documents (supplied values win), and returns
  `422 {"message", "needs": [...]}` when detection fails. A packet with no text layer (scans only)
  requires `application_id`; its product falls back to the previous default.
- `rules.py`: **wording only** for five `error_message` strings: "Required field left blank",
  "Application not signed or dated", "Age on the application doesn't match the medical records",
  "Beneficiary details incomplete", "ID not valid". Rule IDs, logic, severities and
  `RULES_VERSION` 1.0.0 are unchanged; `init_db()` upserts the new text; applicant letters reuse
  these messages. The owner explicitly approved this despite the earlier "keep rules.py unchanged"
  note.
- `tests/test_intake.py` (new): 11 tests (detection, product spellings, conflicts, API round trip,
  supplied ID wins, mixed packet → 422 needs, scan-only → 422 needs).
- Line endings preserved (Python files CRLF, `frontend.jsx` / `style.css` LF).

### Validation so far
- Claude's Linux sandbox, fresh venv from `requirements.txt`: `pytest -q` → **124 passed,
  1 skipped**. Not yet run in the owner's Windows `.venv`.
- Frontend compiled with esbuild and rendered with mocked API data (Playwright): dashboard, intake
  (normal and "needs" states), audit (waiting on underwriter; sent back to applicant).
  **Not yet built with `npm run build` or tested against the live FastAPI/Ollama app.**

### Next steps for Codex, in order
1. `.venv/Scripts/python -m pytest -q`, then `npm run build`. Start uvicorn and analyze
   `sample_data/APP_1001` **without typing an ID**. Check the dashboard, then each decision (send
   back, overrule, edit), then the audit process bar after each. Also check the narrow layout.
2. Update `README.md` (the `/analyze` ID/product are optional; the 422 `needs` contract; the new UI)
   and the `AGENTS.md` file table if anything else changes.
3. Commit and push the files above to `https://github.com/algoshank-pat/NIBO` (the owner said
   "commit and push"). If `git status` also shows uncommitted gap-resolver extension files, confirm
   with the owner first: the earlier October 23 note required separate approval for those.
4. Investigate before the demo: the owner's screenshots showed APP_1001 proposals with 3 issues
   (Oct 22) and 1 issue (Oct 23), and two different audit histories for APP_1001 (possibly
   `nibo.db` vs `.ui-smoke.db` or a different `NIBO_DATABASE_PATH`). Explain both to the owner.

### Environment notes
- LangSmith tracing: the owner set `LANGSMITH_TRACING`, `LANGSMITH_API_KEY` and
  `LANGSMITH_PROJECT=nibo` as PowerShell session variables before starting uvicorn. `config.py`
  does not export `.env` values to `os.environ`, so LangSmith only sees real environment variables.
  A local `.env` was created with PowerShell-style lines, which causes harmless python-dotenv
  "could not parse statement" warnings. It is gitignored and **contains the API key: never print,
  copy or commit it.** The first trace has not been confirmed yet.
- LangSmith is a hosted service and traces include extracted document text. Synthetic data only;
  the owner must decide before any real data is traced (it cuts against the local-only principle).
- The owner switches between cmd and PowerShell. README commands are PowerShell.

### Presentation (outside this folder, for the owner's reference)
- `C:\Maven\Projects\Week3\NIBO_Maven_Presentation.pptx`: 5 slides, ivory/teal theme, business
  language, about 5.5 minutes of speaker notes. Edit it in PowerPoint from here on.
- Pending owner decisions: add a "Private by design" principle and security notes; clarify that
  Deny (reject) marks the case READY; notes say "John Doe" but sample APP_1001 is **Jane Doe**;
  slide 4 shows 2 issues (blank beneficiary + DOB mismatch) but the sample packet produces 1.
  Align slide 4 with the live demo.

## Codex takeover validation — October 23, 2026

Windows validation: 126 tests pass; Vite production build succeeds. Automatic browser intake of all APP_1001 PDFs identified Jane Doe / TermLife_10yr without entered metadata and found one beneficiary issue. Approve was verified through the browser; reject and modify through live HTTP against an isolated mock database. Each produced three saved events; READY had no letter, NIBO had a stored letter.

Corrected intake to detect conflicting IDs across label patterns. Corrected the audit so a stored letter does not imply applicant notification; staff sending remains pending. Removed preview wording implying automatic delivery. README documents the optional metadata and 422 needs response. The process bar is reconstructed from saved outcomes, not actual per-node telemetry.

History investigation: nibo.db APP_1001 proposals each contain one beneficiary issue. .ui-smoke.db has an October 22 proposal with date_consistency, beneficiary_designated and id_valid, then an October 23 proposal with only beneficiary_designated. Different databases and stored proposal snapshots explain the differing displays; the cause of the older extraction differences was not established.

Verification server: localhost:8001, mock extraction, tracing explicitly disabled, separate work/takeover-demo.db. Existing localhost:8000 process was preserved. LangSmith credentials were not printed or changed, and successful hosted tracing remains unverified.
