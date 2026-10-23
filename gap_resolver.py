"""Bounded tool routing and deterministic verification before underwriter review."""
from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import date
from typing import Any

from integrations.dmv import DMVConnector, DMVRecord
from integrations.medical_records import MedicalRecordsConnector
from models import (DocumentExtraction, DocumentType, FieldQuote, GapResolution,
                    GapResolutionContext, IdDocumentFields, Issue, LoadedDocument)
from rules import RULES, run_rules


def _option(config: Any, name: str, default: Any) -> Any:
    return config.get(name, default) if isinstance(config, dict) else getattr(config, name, default)


def _gap_id(gap: Issue) -> str:
    payload = json.dumps(gap.model_dump(mode="json"), sort_keys=True).encode()
    return "gap_" + hashlib.sha256(payload).hexdigest()[:16]


class GapResolver:
    def __init__(self, config: Any, *, context: GapResolutionContext | None = None,
                 dmv: DMVConnector | None = None, medical: MedicalRecordsConnector | None = None,
                 as_of: date | None = None):
        self.context = context or GapResolutionContext()
        self.as_of = as_of or date.today()
        self.timeout_s = _option(config, "gap_timeout_s", 10)
        self.batch_timeout_s = _option(config, "gap_batch_timeout_s", 30)
        self.parallelism = _option(config, "gap_parallelism", 4)
        self.dmv = dmv or DMVConnector({
            "mock_mode": _option(config, "gap_dmv_mock_mode", True),
            "records": _option(config, "gap_dmv_records", {}),
            "base_url": _option(config, "gap_dmv_base_url", None), "timeout_s": self.timeout_s})
        self.medical = medical or MedicalRecordsConnector()

    def _manual(self, gap: Issue, reason: str, *, fetched_data: dict | None = None,
                trace: list[str] | None = None, error: str | None = None,
                simulated: bool = False) -> GapResolution:
        return GapResolution(gap_id=_gap_id(gap), original_issue=gap,
            resolution_method="manual_request", confidence=0.5, fetched_data=fetched_data,
            trace=[*(trace or []), reason, "Manual request recorded; no message sent"],
            error_message=error, simulated=simulated)

    async def resolve_gap(self, gap: Issue, extracted_text: dict[str, str],
                          documents: list[LoadedDocument]) -> GapResolution:
        try:
            return await asyncio.wait_for(self._resolve_gap(gap, documents), timeout=self.timeout_s)
        except TimeoutError:
            return self._manual(gap, "Resolution attempt timed out", error="TimeoutError")
        except Exception as error:
            return self._manual(gap, "Provider unavailable or response invalid", error=type(error).__name__,
                                trace=[f"Route selected for {gap.rule}", "Provider attempt failed"])

    async def _resolve_gap(self, gap: Issue, documents: list[LoadedDocument]) -> GapResolution:
        if gap.rule == "id_valid":
            return await self._resolve_id(gap, documents)
        if gap.rule in ("medical_recency", "medical_records") or gap.missing_document == DocumentType.MEDICAL_RECORDS:
            data = None
            if self.context.applicant_id and self.context.provider_npi:
                data = await self.medical.fetch(self.context.applicant_id, self.context.provider_npi)
            return self._manual(gap, "Medical integration is mock-only; request provider documents",
                fetched_data=data, simulated=True, trace=["Route: medical records", "No medical fields replaced"])
        if gap.rule not in RULES:
            return GapResolution(gap_id=_gap_id(gap), original_issue=gap, status="unresolvable",
                resolution_method="unresolvable", confidence=0,
                trace=["No supported resolution route for this rule", "Underwriter review required"])
        return self._manual(gap, "Applicant must supply the missing information",
                            trace=[f"Route: manual request for {gap.rule}"])

    async def _resolve_id(self, gap: Issue, documents: list[LoadedDocument]) -> GapResolution:
        trace = ["Route: DMV verification"]
        original = next((d for d in documents if d.readable and d.doc_type == DocumentType.ID_DOCUMENT and
                         d.extraction and d.extraction.id_document is not None), None)
        fields = original.extraction.id_document if original else None
        if fields and fields.id_type != "DL":
            return self._manual(gap, "DMV lookup only supports driver's licenses", trace=trace)
        number = self.context.driver_license_number or (fields.id_number if fields else None)
        state = self.context.applicant_state
        if not number or not state:
            return self._manual(gap, "Issuing state and actual license number are required", trace=trace)
        if fields and fields.id_number and fields.id_number != number:
            return self._manual(gap, "Lookup identity conflicts with the uploaded ID", trace=trace)
        applicant_name = next((d.extraction.application_form.applicant_name for d in documents
                               if d.readable and d.doc_type == DocumentType.APPLICATION_FORM and d.extraction
                               and d.extraction.application_form and d.extraction.application_form.applicant_name), None)
        name = applicant_name or (fields.full_name if fields else None)
        if applicant_name and fields and fields.full_name and applicant_name.casefold().split() != fields.full_name.casefold().split():
            return self._manual(gap, "Uploaded ID name conflicts with the applicant", trace=trace)
        if not name:
            return self._manual(gap, "Applicant name is required to verify the fetched identity", trace=trace)
        trace.append("Fetch DMV record for supplied identity")
        payload = await self.dmv.fetch(state, number)
        record = DMVRecord.model_validate(payload)
        simulated = self.dmv.mock_mode
        data = {key: payload[key] for key in DMVRecord.model_fields if key in payload}
        matches = (record.state == state and record.driver_license_number == number and
                   record.full_name is not None and record.full_name.casefold().split() == name.casefold().split())
        if not matches:
            return self._manual(gap, "Fetched identity does not match the applicant", fetched_data=data,
                                trace=trace, simulated=simulated)
        if not record.valid or record.suspensions:
            return self._manual(gap, "Provider did not confirm a valid, unsuspended license", fetched_data=data,
                                trace=trace, simulated=simulated)
        updated = IdDocumentFields(id_type="DL", full_name=name, id_number=number,
                                   expiry_date=record.expiration_date.isoformat())
        source = json.dumps(data, indent=2, sort_keys=True)
        expiry_line = next(line.strip() for line in source.splitlines() if '"expiration_date"' in line)
        candidate = LoadedDocument(doc_id="dmv_" + _gap_id(gap), filename="DMV_verified_record.json",
            pages=[source], text_source="api", extraction=DocumentExtraction(
                doc_type=DocumentType.ID_DOCUMENT,
                classification_confidence=original.extraction.classification_confidence if original else 0.95,
                id_document=updated, quotes=[FieldQuote(field="expiry_date", quote=expiry_line, page=1)]))
        if run_rules(["id_valid"], [candidate, *documents], self.as_of):
            return self._manual(gap, "Fetched record still fails the deterministic ID rule", fetched_data=data,
                                trace=[*trace, "Re-ran id_valid; gap remains"], simulated=simulated)
        return GapResolution(gap_id=_gap_id(gap), original_issue=gap, resolved=True, status="resolved",
            resolution_method="auto_fetch", fetched_data=data, fetched_document=candidate, confidence=0.95,
            simulated=simulated, trace=[*trace, "Identity matched", "Re-ran id_valid; gap resolved",
                                        "Synthetic fixture" if simulated else "Provider record"])

    async def batch_resolve(self, gaps: list[Issue], extracted_text: dict[str, str],
                            documents: list[LoadedDocument]) -> dict[str, GapResolution]:
        if not gaps:
            return {}
        semaphore = asyncio.Semaphore(self.parallelism)
        async def attempt(gap: Issue) -> GapResolution:
            async with semaphore:
                return await self.resolve_gap(gap, extracted_text, documents)
        tasks = [asyncio.create_task(attempt(gap)) for gap in gaps]
        try:
            _, pending = await asyncio.wait(tasks, timeout=self.batch_timeout_s)
            for task in pending:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        resolutions = {}
        for index, (gap, task) in enumerate(zip(gaps, tasks)):
            if task.cancelled():
                result = self._manual(gap, "Batch resolution deadline reached", error="BatchTimeout")
            elif task.exception() is not None:
                result = self._manual(gap, "Resolution attempt failed", error=type(task.exception()).__name__)
            else:
                result = task.result()
            result.gap_id = f"{_gap_id(gap)}_{index}"
            if result.fetched_document:
                result.fetched_document.doc_id = "dmv_" + result.gap_id
            resolutions[result.gap_id] = result
        return resolutions
