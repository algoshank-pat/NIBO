"""Case intake: read the application number and policy product from the documents themselves.

Deterministic pattern matching only (no LLM), so the same packet always yields the same case
details. Ambiguity is never guessed away: if the documents disagree or say nothing, the field
is left unset and the caller asks the underwriter.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from rules import PRODUCT_RULES

_ID = r"([A-Z]{2,5}[_-]?\d{3,})"
ID_PATTERNS = [
    re.compile(r"\bApplication\s*(?:ID|No\.?|Number|#)\s*[:#]?\s*" + _ID, re.IGNORECASE),
    re.compile(r"\bRef(?:erence)?\.?\s*[:#]?\s*" + _ID, re.IGNORECASE),
    re.compile(r"\b(APP[_-]\d{3,})\b", re.IGNORECASE),
]
PRODUCT_PATTERN = re.compile(r"\b(?:Policy\s+)?Product\s*[:#]\s*([^\n\r]+)", re.IGNORECASE)


def _norm(text: str) -> str:
    text = text.lower().replace("years", "yr").replace("year", "yr")
    return re.sub(r"[^a-z0-9]", "", text)


KNOWN_PRODUCTS = {_norm(p): p for p in PRODUCT_RULES}


@dataclass
class CaseDetails:
    application_id: str | None = None
    policy_product: str | None = None
    problems: list[str] = field(default_factory=list)


def _detect_id(texts: list[str]) -> tuple[str | None, str | None]:
    found = {m.group(1).upper().replace("-", "_")
             for pattern in ID_PATTERNS for t in texts for m in pattern.finditer(t)}
    if len(found) == 1:
        return found.pop(), None
    if len(found) > 1:
        return None, f"The documents reference different applications: {', '.join(sorted(found))}."
    return None, "Couldn't find the application number on these documents."


def _detect_product(texts: list[str]) -> tuple[str | None, str | None]:
    found = set()
    for t in texts:
        for m in PRODUCT_PATTERN.finditer(t):
            raw = m.group(1).strip()
            key = next((k for k in sorted(KNOWN_PRODUCTS, key=len, reverse=True) if _norm(raw).startswith(k)), None)
            found.add(KNOWN_PRODUCTS[key] if key else raw)
    known = {p for p in found if p in PRODUCT_RULES}
    if len(known) == 1 and len(found) == 1:
        return known.pop(), None
    if not found:
        return None, "Couldn't find the policy product on the application form."
    return None, f"Couldn't match the policy product on the documents: {', '.join(sorted(found))}."


def detect_case_details(texts: list[str]) -> CaseDetails:
    """`texts` holds the extracted text of every document in the packet."""
    details = CaseDetails()
    details.application_id, id_problem = _detect_id(texts)
    details.policy_product, product_problem = _detect_product(texts)
    details.problems = [p for p in (id_problem, product_problem) if p]
    return details
