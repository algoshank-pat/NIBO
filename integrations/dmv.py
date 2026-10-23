"""Mockable provider adapter; no state DMV endpoint is configured by default."""
from __future__ import annotations

from datetime import date
from typing import Any

import httpx
from pydantic import BaseModel, Field, StrictBool


class IntegrationUnavailable(Exception):
    pass


class DMVRecord(BaseModel):
    state: str
    driver_license_number: str
    valid: StrictBool
    expiration_date: date
    full_name: str | None = None
    violations: int = Field(default=0, ge=0)
    suspensions: StrictBool = False


class DMVConnector:
    def __init__(self, config: dict[str, Any], client: httpx.AsyncClient | None = None):
        self.mock_mode = config.get("mock_mode", True)
        self.records = config.get("records", {})
        self.base_url = config.get("base_url")
        self.timeout_s = config.get("timeout_s", 10.0)
        self.client = client

    async def fetch(self, state: str, driver_license_number: str) -> dict:
        if self.mock_mode:
            record = self.records.get(f"{state}:{driver_license_number}")
            if record is None:
                raise IntegrationUnavailable("No matching mock DMV record")
            return dict(record)
        if not self.base_url:
            raise IntegrationUnavailable("A contracted DMV provider endpoint is not configured")
        if not self.base_url.startswith("https://"):
            raise IntegrationUnavailable("DMV provider requires HTTPS")
        # Generic provider contract only; a real state/provider adapter is still required.
        if self.client is not None:
            response = await self.client.post(self.base_url.rstrip("/") + "/licenses/verify",
                json={"state": state, "driver_license_number": driver_license_number},
                timeout=self.timeout_s, follow_redirects=False)
        else:
            async with httpx.AsyncClient(timeout=self.timeout_s, follow_redirects=False) as client:
                response = await client.post(self.base_url.rstrip("/") + "/licenses/verify",
                    json={"state": state, "driver_license_number": driver_license_number})
        response.raise_for_status()
        return response.json()
