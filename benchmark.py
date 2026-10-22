"""Local Ollama benchmark. Synthetic sample PDFs only; never persists proposals."""
from __future__ import annotations

import argparse
import json
import threading
import time
from pathlib import Path

import httpx


import database as db
from agent import analyze_application
from config import settings
from extractors import OllamaExtractor, compact_schema
from models import ApplicationRequest, DocumentInput



class MeasuredTransport(httpx.HTTPTransport):
    def __init__(self, output, keep_alive=None, compact=False):
        super().__init__()
        self.output, self.keep_alive, self.compact = output, keep_alive, compact
        self.lock = threading.Lock()

    def handle_request(self, request):
        body = json.loads(request.content)
        body.pop("keep_alive", None)
        if self.keep_alive:
            body["keep_alive"] = self.keep_alive
        if self.compact:
            body["format"] = compact_schema(body["format"])
        headers = dict(request.headers)
        headers.pop("content-length", None)
        request = httpx.Request(request.method, request.url, headers=headers, json=body,
                               extensions=request.extensions)
        start = time.perf_counter()
        response = super().handle_request(request)
        response.read()
        payload = response.json()
        record = {"kind": "call", "model": body["model"],
                  "seconds": round(time.perf_counter() - start, 3),
                  "ocr": any("images" in m for m in body["messages"]),
                  "schema_bytes": len(json.dumps(body["format"])),
                  **{k: payload.get(k) for k in ("total_duration", "load_duration",
                     "prompt_eval_duration", "eval_duration", "prompt_eval_count", "eval_count")}}
        with self.lock:
            with self.output.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record) + "\n")
        return response


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, choices=(1, 2, 4), default=4)
    parser.add_argument("--concise-notes", action="store_true")
    parser.add_argument("--model", default="gemma4")
    parser.add_argument("--vision-model")
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--ctx", type=int, default=16384)
    parser.add_argument("--keep-alive")
    parser.add_argument("--compact", action="store_true")
    parser.add_argument("--packet", default="all")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--output", required=True)
    parser.add_argument("--unload", action="store_true")
    args = parser.parse_args()
    output = Path(args.output)
    settings.extraction_workers = args.workers
    settings.ollama_concise_notes = args.concise_notes
    settings.ollama_num_ctx = args.ctx
    settings.ollama_compact_schema = False  # profiles control schema trimming in the transport
    db.init_db()
    if args.unload:
        with httpx.Client(timeout=60) as client:
            for model in ("gemma4", "qwen3.6"):
                client.post(settings.ollama_base_url + "/api/generate",
                            json={"model": model, "keep_alive": 0}).raise_for_status()
    with httpx.Client(transport=MeasuredTransport(output, args.keep_alive, args.compact),
                      timeout=args.timeout) as client:
        extractor = OllamaExtractor(model=args.model, vision_model=args.vision_model or args.model, client=client)
        packets = sorted(Path("sample_data").glob("APP_*"))
        if args.packet != "all":
            packets = [p for p in packets if p.name == args.packet]
        for repeat in range(args.repeat):
            for packet in packets:
                request = ApplicationRequest(application_id=packet.name,
                    documents=[DocumentInput(path=str(p)) for p in sorted(packet.glob("*.pdf"))])
                start = time.perf_counter()
                proposal, state = analyze_application(request, extractor=extractor, persist=False)
                record = {"kind": "application", "packet": packet.name, "repeat": repeat,
                          "model": args.model, "workers": args.workers, "concise_notes": args.concise_notes,
                          "timeout_s": args.timeout, "ctx": args.ctx, "keep_alive": args.keep_alive,
                          "compact": args.compact, "seconds": round(time.perf_counter()-start, 3),
                          "status": proposal.proposed_status, "alternative": proposal.alternative_status,
                          "issues": [i.model_dump() for i in proposal.issues_detail],
                          "documents": [{"filename": d.filename, "readable": d.readable,
                              "error": d.error, "extraction": d.extraction.model_dump() if d.extraction else None}
                              for d in state.documents]}
                with output.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(record) + "\n")
                print(json.dumps({k:v for k,v in record.items() if k not in {"issues", "documents"}}), flush=True)


if __name__ == "__main__":
    main()
