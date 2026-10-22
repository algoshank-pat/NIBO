"""Stored applicant letters. This module never sends messages."""
from __future__ import annotations


def letter_text(application_id: str, name: str, issues: list[str]) -> str:
    items = "\n".join(f"  {n}. {issue}" for n, issue in enumerate(issues, 1))
    return (
        f"Dear {name},\n\n"
        f"Thank you for your life insurance application (reference {application_id}). "
        "Before we can continue our review, we need the following item(s):\n\n"
        f"{items}\n\n"
        "Please return the corrected or missing documents within 30 days. "
        "If you have questions, contact your agent or our underwriting team.\n\n"
        "Sincerely,\nUnderwriting Department"
    )
