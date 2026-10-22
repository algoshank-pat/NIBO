# Local performance measurements — October 21, 2026

## Outcome

The five-second whole-application target is **not met** on this CPU-only machine with the
installed local models. No LLM bypass, hosted model, or automatic underwriting decision was added.
Gemma4 remains the default. The deterministic evidence fallback is verified against live gemma4
responses with empty quotes as well as model-free regression tests.

## Method

`benchmark.py` reads the existing synthetic PDFs in `sample_data`, runs the existing LangGraph
pipeline with `persist=False`, and writes JSONL records for completed model calls and applications.
It records wall time plus Ollama loading, prompt evaluation, generation durations and token counts.
It does not store new proposals or underwriting decisions (database initialization still seeds rules).

Hardware: CPU only, approximately 31 GB RAM; Ollama reported zero VRAM usage. Models:
`gemma4:latest` (8B Q4_K_M) and `qwen3.6:latest` (36B Q4_K_M). The initial trials used four
extraction workers; a follow-up tested one worker. Elapsed calls include queueing and therefore must
not be summed as packet time.
All experiments use temperature 0 and thinking disabled. Each tuning profile ran APP_1001 twice;
these are small, ordered local experiments during development, not statistically controlled trials.
The benchmark client has a 180-second request timeout, configurable with `--timeout`.

## Measurements

| Profile | Packet | Run 1 (s) | Run 2 (s) | Extraction result |
|---|---|---:|---:|---|
| Gemma4, 16384 context, full schema, default Ollama residency | APP_1001 | 111.251 | — | Expected NIBO; fields match sample parser |
| Same baseline | APP_1002 | 102.438 | — | Expected READY; fields match sample parser |
| Same baseline, includes scan OCR | APP_1003 | 143.304 | — | Expected NIBO, alternative READY; fields match on readable forms |
| Gemma4, 4096 context | APP_1001 | 103.560 | 99.606 | Expected NIBO; fields match in both runs |
| Gemma4, 4096 context, keep_alive=15m | APP_1001 | 93.673 | 87.243 | Expected NIBO; run 1 omitted medical age and added an uncertainty issue |
| Gemma4, 4096 context, keep_alive=15m, trimmed schema | APP_1001 | 92.324 | 95.002 | Expected NIBO; fields match in both runs |
| Qwen3.6, 4096 context, keep_alive=15m, trimmed schema | APP_1001 | 182.454 | 154.706 | Cold run: 2/4 docs timed out. Warm run: all four parsed; fields match |

Baseline total: **356.993 seconds for 13 PDFs** (about 27.5 seconds/PDF throughput).
Scan transcription alone took **38.570 seconds**. Baseline gemma4 returned zero field quotes;
the beneficiary blank and stale exam date nevertheless appeared as verbatim evidence at page 1
of their respective PDFs through the deterministic fallback.

The full schema is 4,396 JSON bytes; trimming `title`, `description`, and `default` metadata reduces
it to 2,462 bytes (44% smaller). Pydantic validation and schema constraints remain unchanged.
The smaller schema did not show a consistent latency improvement in these runs.

Qwen used about 23 GB of resident memory and spent about 50.5 seconds loading during its cold run.
Its warm run supplied 17 field quotes, unlike gemma4, but was slower. Its cold NIBO verdict is
**not** counted as a successful accuracy result because two required documents were unreadable.
The old timeout message in that raw run said 300 seconds (the application setting) even though
the injected benchmark client used 180 seconds; that misleading message has now been corrected.

Earlier retained-default regression (before the scheduling/notes follow-up): APP_1001, gemma4 / 16384 context / full schema / 15-minute
keep-alive, **113.810 seconds** after unloading the models. All four documents were readable,
all expected sample fields matched, and the proposal contained exactly the expected beneficiary
issue with the recovered verbatim quote/page. This does not demonstrate a latency improvement.
Raw record: `benchmark_final.jsonl`.

## Follow-up: CPU scheduling and concise notes

Serial requests retain Ollama classification/extraction for every readable document. An APP_1001
serial-only comparison took 91.181 and 106.638 seconds, with all expected fields matching. A second
experiment added an instruction reserving `notes` for genuine ambiguity, illegibility or conflicting
values, excluding routine normalization commentary. Returned notes are never stripped after extraction.

| Packet | Four-worker baseline (s) | One worker + concise notes (s) |
|---|---:|---:|
| APP_1001 | 111.251 | 99.513 |
| APP_1002 | 102.438 | 88.027 |
| APP_1003 (scan OCR included) | 143.304 | 137.141 |
| Total | 356.993 | 324.681 |

The observed total fell by 32.312 seconds (**9.05%**). Every expected field on all readable sample
forms matched the sample parser; all three statuses, issue lists, evidence strings and page locations
matched the original live baseline. OCR alone still took 38.946 seconds in the follow-up.
This small, ordered comparison does not establish a guaranteed improvement or isolate the effect
of serial scheduling from the notes instruction. The five-second target remains unmet.

These two bounded changes are now defaults: `NIBO_EXTRACTION_WORKERS=1` and
`NIBO_OLLAMA_CONCISE_NOTES=true`. Restore four workers / the earlier prompt with `4` / `false`.
The model, full schema and 16384-token context remain unchanged. Model-free tests cover both
worker counts and preservation of genuine ambiguity notes. The current suite has 80 passing tests.
Raw records: `benchmark_serial.jsonl` and `benchmark_concise.jsonl`.

Reproduce the current profile:

```powershell
.venv/Scripts/python benchmark.py --workers 1 --concise-notes --ctx 16384 --keep-alive 15m --output serial-concise-new.jsonl
```

The benchmark CLI deliberately defaults to four workers and the earlier notes prompt to reproduce
the historical baseline; select the flags above for current production settings.

## Retained configuration

- Model: `gemma4`; context: **16384**, retaining the original document capacity.
- `NIBO_OLLAMA_KEEP_ALIVE=15m` explicitly retains the local model between reviews. This is a
  residency choice, not a demonstrated five-second latency fix.
- `NIBO_OLLAMA_COMPACT_SCHEMA=false`: schema trimming is available as an opt-in experiment.
- `NIBO_OLLAMA_NUM_CTX=4096` is available for small packets, but is not the default: larger
  document capacity is useful and one tuning run lost a field. These samples alone do not prove
  that lowering context caused the omission or that all real forms would fit.
- Qwen is an available configuration alternative, not the new default. Its OCR performance and
  accuracy on APP_1002/APP_1003 were not benchmarked, since it was slower on APP_1001.

No reliable route to five seconds was demonstrated through these settings. Observed token
generation alone exceeds that target for individual documents. Reaching it requires a separate
model/hardware/strategy decision. A regex bypass remains unimplemented and requires the owner's
explicit strategy approval.

## Reproduce

Use the existing virtualenv from the project root. Output files append JSONL; choose a fresh name
for a new experiment. `--unload` unloads both installed models to measure a cold start.

```powershell
.venv/Scripts/python benchmark.py --unload --output baseline-new.jsonl
.venv/Scripts/python benchmark.py --ctx 4096 --packet APP_1001 --repeat 2 --output context-new.jsonl
.venv/Scripts/python benchmark.py --ctx 4096 --keep-alive 15m --packet APP_1001 --repeat 2 --output keep-new.jsonl
.venv/Scripts/python benchmark.py --ctx 4096 --keep-alive 15m --compact --packet APP_1001 --repeat 2 --output compact-new.jsonl
.venv/Scripts/python benchmark.py --unload --model qwen3.6 --ctx 4096 --keep-alive 15m --compact --packet APP_1001 --repeat 2 --output qwen-new.jsonl
```

Raw evidence is retained in `benchmark_baseline.jsonl`, `benchmark_ctx.jsonl`,
`benchmark_keep.jsonl`, `benchmark_compact.jsonl`, and `benchmark_qwen.jsonl`.
