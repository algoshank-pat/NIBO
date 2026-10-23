# Continue the NIBO agent — October 21, 2026

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
