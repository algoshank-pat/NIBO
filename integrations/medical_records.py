"""Medical provider placeholder. Synthetic responses never resolve real medical gaps."""
from __future__ import annotations


class MedicalRecordsConnector:
    async def fetch(self, applicant_id: str, provider_npi: str) -> dict:
        return {"simulated": True, "applicant_id": applicant_id, "provider_npi": provider_npi,
                "applicant_age": 45, "exam_date": "2024-01-15",
                "note": "Synthetic example only; obtain records from the applicant/provider"}
