"""Generate 3 sample application packets as real PDFs and run them through the agent.

    python sample_applications.py            # generate PDFs + analyze (Ollama, or mock if Ollama isn't running)
    python sample_applications.py --generate # PDFs only

Dates are relative to today so the packets stay valid whenever the demo runs.

  APP_1001  NIBO   - beneficiary form left blank
  APP_1002  READY  - complete, consistent packet
  APP_1003  EDGE   - medical exam just over 3 years old (warning only), medical age off by
                     exactly 1 year (allowed), ID expiring in 12 days (still valid), two
                     beneficiaries 60/40, plus a scanned page with no text layer
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from reportlab.lib.pagesizes import LETTER
from reportlab.pdfgen import canvas

from models import ApplicationRequest, DocumentInput

SAMPLE_DIR = Path(__file__).resolve().parent / "sample_data"
TODAY = date.today()
BLANK = "____________________"


def us(d: date) -> str:
    return d.strftime("%m/%d/%Y")


def years_ago(n: int, extra_days: int = 0) -> date:
    try:
        d = TODAY.replace(year=TODAY.year - n)
    except ValueError:
        d = TODAY.replace(year=TODAY.year - n, day=28)
    return d - timedelta(days=extra_days)


def age_on(dob: date, on: date) -> int:
    return on.year - dob.year - ((on.month, on.day) < (dob.month, dob.day))


def write_pdf(path: Path, title: str, lines: list[str], footer: str | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(path), pagesize=LETTER)
    c.setTitle(title)
    width, height = LETTER
    c.setFont("Helvetica-Bold", 15)
    c.drawString(72, height - 72, title)
    c.setLineWidth(0.5)
    c.line(72, height - 80, width - 72, height - 80)
    c.setFont("Helvetica", 11)
    y = height - 110
    for line in lines:
        if line.startswith("## "):
            y -= 8
            c.setFont("Helvetica-Bold", 12)
            c.drawString(72, y, line[3:])
            c.setFont("Helvetica", 11)
        else:
            c.drawString(72, y, line)
        y -= 20
    if footer:
        c.setFont("Helvetica-Oblique", 8)
        c.drawString(72, 50, footer)
    c.save()
    return path


def write_scanned_pdf(path: Path) -> Path:
    """A 'scan': vector shapes only, no text layer (pypdf extracts nothing)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(path), pagesize=LETTER)
    c.setFillGray(0.85)
    c.rect(60, 120, 490, 600, fill=1, stroke=0)
    c.setFillGray(0.4)
    for i in range(18):
        c.rect(90, 680 - i * 30, 300 + (i * 37) % 120, 8, fill=1, stroke=0)
    c.save()
    return path


@dataclass
class Person:
    name: str
    dob: date
    email: str
    phone: str


def application_form(p: Person, signed: date, *, signature: bool = True, product="TermLife_10yr") -> list[str]:
    return [
        "## Section 1 - Proposed Insured",
        f"Applicant Name: {p.name}",
        f"Date of Birth: {us(p.dob)}",
        f"Email: {p.email}",
        f"Phone: {p.phone}",
        "## Section 2 - Coverage",
        f"Product: {product}",
        "Face Amount: $500,000",
        "## Section 3 - Authorization",
        f"Applicant Signature: {'/s/ ' + p.name if signature else BLANK}",
        f"Date Signed: {us(signed)}",
    ]


def medical(p: Person, exam: date, age: int) -> list[str]:
    return [
        f"Patient Name: {p.name}",
        f"Date of Birth: {us(p.dob)}",
        f"Age: {age}",
        f"Exam Date: {us(exam)}",
        "Examining Physician: Dr. A. Patel, MD",
        "Height: 5 ft 8 in    Weight: 165 lb",
        "Blood Pressure: 122/78",
        "Findings: No abnormalities noted.",
    ]


def drivers_license(p: Person, expiry: date) -> list[str]:
    return [
        "Document Type: Driver's License",
        f"Name: {p.name}",
        "License Number: D1234-5678-9012",
        f"Date of Birth: {us(p.dob)}",
        f"Expiration Date: {us(expiry)}",
        "Issuing State: CA",
    ]


def beneficiary(entries: list[tuple[str, str, str]]) -> list[str]:
    lines = []
    for n, (name, rel, pct) in enumerate(entries, 1):
        lines += [f"## Beneficiary {n}", f"Beneficiary Name: {name}", f"Relationship: {rel}", f"Percentage: {pct}"]
    return lines


TITLES = {
    "application_form": "LIFE INSURANCE APPLICATION",
    "medical_records": "MEDICAL EXAMINATION REPORT",
    "id_document": "DRIVER LICENSE - IDENTIFICATION COPY",
    "beneficiary_form": "BENEFICIARY DESIGNATION FORM",
}


def generate() -> dict[str, ApplicationRequest]:
    signed = TODAY - timedelta(days=5)
    packets: dict[str, ApplicationRequest] = {}

    def build(app_id: str, docs: dict[str, list[str]], extra: list[Path] = ()) -> None:
        folder = SAMPLE_DIR / app_id
        paths = [write_pdf(folder / f"{kind}.pdf", TITLES[kind], lines, footer=f"Ref {app_id}")
                 for kind, lines in docs.items()]
        paths += list(extra)
        packets[app_id] = ApplicationRequest(
            application_id=app_id, documents=[DocumentInput(path=str(p)) for p in paths],
        )

    # --- APP_1001: NIBO - beneficiary left blank
    jane = Person("Jane Doe", date(1980, 5, 10), "jane.doe@example.com", "(415) 555-0142")
    build("APP_1001", {
        "application_form": application_form(jane, signed),
        "medical_records": medical(jane, TODAY - timedelta(days=40), age_on(jane.dob, TODAY - timedelta(days=40))),
        "id_document": drivers_license(jane, TODAY + timedelta(days=900)),
        "beneficiary_form": beneficiary([(BLANK, BLANK, BLANK)]),
    })

    # --- APP_1002: READY - everything in order
    robert = Person("Robert Chen", date(1975, 11, 2), "r.chen@example.com", "(212) 555-0199")
    build("APP_1002", {
        "application_form": application_form(robert, signed),
        "medical_records": medical(robert, TODAY - timedelta(days=60), age_on(robert.dob, TODAY - timedelta(days=60))),
        "id_document": drivers_license(robert, TODAY + timedelta(days=1500)),
        "beneficiary_form": beneficiary([("Linda Chen", "Spouse", "100%")]),
    })

    # --- APP_1003: EDGE - borderline everywhere, only a recency warning
    maria = Person("Maria Garcia", date(1988, 2, 14), "maria.g@example.com", "(305) 555-0123")
    exam = years_ago(3, extra_days=60)                       # ~3y 2m old -> warning
    stated_age = age_on(maria.dob, signed) + 1               # off by exactly 1 -> allowed
    scan = write_scanned_pdf(SAMPLE_DIR / "APP_1003" / "scanned_attachment.pdf")
    build("APP_1003", {
        "application_form": application_form(maria, signed),
        "medical_records": medical(maria, exam, stated_age),
        "id_document": drivers_license(maria, TODAY + timedelta(days=12)),
        "beneficiary_form": beneficiary([("Carlos Garcia", "Spouse", "60%"), ("Sofia Garcia", "Daughter", "40%")]),
    }, extra=[scan])

    (SAMPLE_DIR / "requests.json").write_text(
        json.dumps({k: v.model_dump(mode="json", exclude={"documents": {"__all__": {"uploaded_at"}}})
                    for k, v in packets.items()}, indent=2)
    )
    return packets


EXPECTED = {"APP_1001": "NIBO", "APP_1002": "READY", "APP_1003": "NIBO (alt READY)"}


def main() -> None:
    packets = generate()
    print(f"Wrote sample PDFs to {SAMPLE_DIR}")
    if "--generate" in sys.argv:
        return

    import database as db
    from agent import analyze_application

    db.init_db()
    for app_id, req in packets.items():
        proposal, state = analyze_application(req)
        alt = f" (alt {proposal.alternative_status})" if proposal.alternative_status else ""
        print(f"\n=== {app_id}: {proposal.proposed_status}{alt}  confidence={proposal.confidence_score:.2f}"
              f"  mode={proposal.agent_mode}  expected={EXPECTED[app_id]}")
        for line in state.trace:
            print(f"   . {line}")
        for d in proposal.issues_detail:
            print(f"   [{d.severity}] {d.issue}\n       evidence: {d.evidence}  @ {d.location}")
        for s in proposal.next_steps:
            print(f"   -> {s}")


if __name__ == "__main__":
    main()
