# AGENTS.md — NIBO Application Completeness Agent

Instructions for any coding agent (Codex, Claude Code, etc.) working in this folder.

## Where to work
- Project root: `C:\Maven\Projects\Week3\completeness-agent\`. All code lives here. Do not create a new
  project folder, do not move files, do not scaffold a second app.
- Design doc (source of truth for product behaviour): `C:\Maven\Projects\Week3\nibo-agent-design.md`.
  A copy is included as `nibo-agent-design.md` for repository clones.
- Python 3.14 virtualenv already exists: always use `.venv/Scripts/python` (Windows). Add new packages
  to `requirements.txt` and install them into `.venv`.

## Working agreement with the owner
- Propose each build step and **wait for approval** before writing it, unless told "go until done".
- The owner authorized Git initialization, commits, and push to `https://github.com/algoshank-pat/NIBO`
  on October 21, 2026. Keep local data and secrets excluded using `.gitignore`. Do not create another
  hosted repository without an explicit request. **Never** deploy (Render etc.).
- Run `.venv/Scripts/python -m pytest -q` after every change; all tests must pass before you report done.
- Report honestly: what ran, what didn't, measured numbers, known gaps.

## Strategy (do not change without asking)
1. **Open-weights LLM only, via local Ollama.** No Claude/Anthropic/OpenAI or any hosted LLM API.
   Default model `gemma4` (8B, vision-capable); `qwen3.6` (36B MoE) is installed as an alternative.
   Machine: CPU only (no NVIDIA GPU), 31 GB RAM.
2. **The LLM only reads documents** (classify + extract fields + verbatim quotes). It never decides
   pass/fail. **All decisions come from the deterministic rules in `rules.py`.**
3. **Human-in-the-loop:** the agent only *proposes* NIBO/READY. Application status is `PROPOSED`
   until an underwriter decides. Audit log is append-only (SQLite triggers enforce it).
4. Mock mode (`MockExtractor`) must keep working so tests and demos run without Ollama.
   Tests must never require a running model (use `httpx.MockTransport` for Ollama calls).

## Architecture (files)
| File | Role |
|---|---|
| `agent.py` | LangGraph StateGraph: load_documents -> classify_documents -> retrieve_rules -> analyze -> propose_status -> resolve_gaps -> build_proposal. `analyze_application()` is the entry point. |
| `extractors.py` | `OllamaExtractor` (POST /api/chat, JSON-schema `format`, temperature 0, think false, Pydantic validation + 1 retry; vision OCR for scanned PDFs via pypdfium2) and `MockExtractor` (regex for sample PDFs). `get_extractor()` picks by `NIBO_LLM_MODE` (auto/ollama/mock). |
| `rules.py` | 6 rules: required_fields, signature_present, date_consistency, medical_recency, beneficiary_designated, id_valid. Pure functions of extracted fields + `as_of` date. `run_rules()` dedupes missing-document issues. |
| `models.py` | Pydantic models: AnalyzeRequest (upload), ApplicationRequest, DocumentInput, DocumentExtraction (also the LLM output schema), LoadedDocument, RuleDefinition, Issue, Proposal, AnalysisState, CompletionProposal, AuditLog, UnderwriterDecision, SystemExecution. |
| `database.py` | SQLite (`nibo.db`): applications, audit_logs (append-only), rules (versioned, per product). `python database.py` initialises. |
| `app.py` | FastAPI analysis, application view, audit and underwriter decision endpoints; serves the built React portal. `/analyze` accepts `AnalyzeRequest` and fills a missing application ID / product from the documents via `intake.py`. |
| `intake.py` | Deterministic case intake: reads application number and product from document text (regex, no LLM); never guesses on conflicts. |
| `config.py` | pydantic-settings, env prefix `NIBO_` (e.g. NIBO_OLLAMA_MODEL, NIBO_LLM_MODE). |
| `sample_applications.py` | Generates 3 sample packets as PDFs (dates relative to today) and runs them. APP_1001 NIBO (blank beneficiary), APP_1002 READY, APP_1003 edge (warning only -> NIBO alt READY, plus an unreadable scan). |
| `gap_resolver.py`, `integrations/` | Bounded tool routing; mock DMV identity verification + unchanged-rule rechecks; medical/manual fallback; provider timeouts. Real DMV contract not connected. |
| `letters.py` | Shared deterministic stored-letter text; never sends messages. |
| `frontend.jsx`, `style.css`, `index.html`, `vite.config.js` | React portal in this same project; npm build emits `dist/` for FastAPI. |
| `benchmark.py`, `PERFORMANCE.md` | Local synthetic-packet benchmark and measured results/limitations. |
| `tests/` | 126 tests (all pass in the Windows virtualenv): rules, database, agent+API (mock mode), Ollama extractor (mocked HTTP), gap resolver, case intake. |

## Locked decisions (from the owner)
- Severity mapping: rule error -> critical, warning -> major, agent-uncertain -> minor.
- Warning-only result -> NIBO with `alternative_status: READY` and `requires_underwriter_judgment: true`.
- Confidence = certainty of the verdict (0.6 x classification confidence + 0.4 x rule coverage on solid
  data; capped at 0.75 for warning-only), NOT "% of rules passed".
- Rules metadata stored in a versioned `rules` table; check logic stays in Python.
- beneficiary_designated also requires shares to total 100%.

## Conventions
- Match existing style: type hints, `from __future__ import annotations`, small pure functions,
  section-divider comments, sparse docstrings. Keep the rules engine deterministic.
- Write files as UTF-8 (Windows default cp1252 breaks on non-ASCII characters).

## Gap resolver scope approved October 23, 2026
- The owner approved all implementation files for the gap resolver. Do not commit or push this
  extension until the owner separately approves; this overrides the earlier general Git approval.
- Keep `rules.py`, the six rules, and underwriter approval flow unchanged.
- DMV mocks need explicit fixture records and issuing-state/license context; never invent matches.
- Medical provider results remain mock-only and do not resolve medical gaps. Manual requests are
  recorded, never sent. No real provider credentials or endpoint integration has been verified.
- Resolution attempts are preserved in the persisted proposal's append-only audit snapshot.

## UX, intake and rule wording approved October 23, 2026 (later)
- The owner approved business-language UI changes (underwriter dashboard, process-bar audit trail,
  no JSON on screen), case details read from documents (`intake.py`), and **wording-only** changes
  to five rule error messages in `rules.py`. Rule logic, IDs, severities and version are unchanged.
- The owner said "commit and push" for this work. See `HANDOFF_CODEX.md` (latest section) for the
  file list, what is verified, and what still needs a live check.
