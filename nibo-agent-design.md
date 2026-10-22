# Application Completeness Agent (NIBO) — Design Document

**Project:** Maven Week 3 — Agentic AI System  
**Due:** Sunday, October 25, 2026  
**Status:** Design Phase (Day 1)

---

## 1. One-Liner

My agent helps insurance underwriters triage life insurance applications in a web portal, autonomously analyzing documents to identify missing/inconsistent information, proposing a NIBO (Not In Best Order) status with evidence and reasoning, while the underwriter reviews and approves before the system marks the application, generates a letter to the applicant, and logs the decision. It works when an underwriter can triage 10 applications in under 30 minutes with full audit visibility of what the agent found and why.

---

## 2. The Problem

**Today's workflow:**
1. Underwriter receives application (PDF + metadata)
2. Manually reads application form + medical records + ID
3. Checks against mental checklist:
   - All required fields filled?
   - Signatures present and valid?
   - Ages/dates consistent across documents?
   - Medical records match application?
   - Beneficiary designation complete?
4. If issues found: manually drafts email/letter to applicant
5. Updates system status manually
6. **Time: 20-30 minutes per application**

**Pain:**
- Repetitive manual review
- High error rate (miss issues → costly delays)
- Bottleneck (underwriters are expensive; spend 40% of time on this)
- No audit trail (who reviewed what, when?)
- Inconsistent (different underwriters apply rules differently)

**Opportunity:**
Agent automates the analysis, leaving underwriter to make the final judgment call.

---

## 3. Agent Framework

| Field | Definition |
|-------|-----------|
| **Agent Goal** | Analyze an insurance application's documents; identify missing/inconsistent information; propose NIBO status with evidence |
| **Where Used** | Web form (underwriter portal) — underwriter uploads app PDFs, clicks "Analyze," sees agent's assessment |
| **Steps (In Order)** | 1) Upload documents (app form, medical records, ID, beneficiary) → 2) Agent extracts text from PDFs → 3) Agent retrieves rules → 4) Agent analyzes docs against rules → 5) Agent proposes status + list of issues → 6) Underwriter reviews proposal → 7) Underwriter approves/rejects → 8) System updates application status + generates letter → 9) Audit log written |
| **What It Can Do** | **Reads (autonomous):** Extract text from PDFs, classify document types, compare dates/names, retrieve policy rules. **Proposes (autonomous):** Identify missing fields, flag inconsistencies, suggest NIBO status, explain evidence. **Cannot do (always human):** Approve status change, send letter to applicant, update policy record |
| **What It Remembers** | This application's analysis (extracted docs, rules checked, issues found, proposal). Does NOT remember across applications (stateless per app) |
| **Hard Limits (Never)** | Never approve on its own. Never send applicant letter without human sign-off. Never delete/modify records. Never make exceptions to rules without human override |
| **Human-in-the-Loop** | Underwriter reviews agent's proposal (2-3 min read). Can accept, reject, or modify. Must explicitly approve before system executes. If agent is uncertain, escalate to supervisor |
| **If Something Breaks** | PDF extraction fails → flag doc as unreadable, ask underwriter to re-upload. Rule engine has edge case → agent flags uncertainty, underwriter decides. Document is missing → agent notes it, includes in proposal |
| **Success Metric** | Underwriter can assess application + make decision in under 5 minutes with high confidence. Audit log shows exactly what agent found + what underwriter approved |

---

## 4. Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                       UNDERWRITER PORTAL (Web UI)                   │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │ Application ID │ Upload Docs │ [Analyze] │ View Proposal    │  │
│  │                │ [app.pdf]   │           │ [Approve/Reject] │  │
│  └──────────────────────────────────────────────────────────────┘  │
└─────────────────┬──────────────────────────────────────────────────┘
                  │ API call (FastAPI)
                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│           COMPLETENESS AGENT (LangGraph)                            │
│                                                                     │
│  ┌──────────────────┐    ┌──────────────────┐                     │
│  │ Document Loader  │───▶│  PDF Extractor   │                     │
│  │ (receives PDFs)  │    │  (text + metadata)                      │
│  └──────────────────┘    └────────┬──────────┘                     │
│                                   │                                │
│                         ┌─────────▼──────────┐                    │
│                         │ Agent Orchestrator │                    │
│                         │   (LangGraph)      │                    │
│                         └─────────┬──────────┘                    │
│                                   │                                │
│          ┌────────────────────────┼────────────────────────┐      │
│          ▼                        ▼                        ▼      │
│  ┌──────────────────┐   ┌──────────────────┐   ┌──────────────┐ │
│  │ Rules Engine     │   │ Document         │   │ Evidence     │ │
│  │ (checks)         │   │ Classifier       │   │ Gatherer     │ │
│  └────────┬─────────┘   └────────┬─────────┘   └──────┬───────┘ │
│           │                      │                    │          │
│  • Required fields?       • App form?           • Missing field? │
│  • Signatures valid?      • Medical record?     • Wrong date?    │
│  • Dates consistent?      • ID document?        • Name mismatch? │
│  • Names match?           • Beneficiary form?                    │
│           │                      │                    │          │
│           └────────────────────────┴────────────────────┘        │
│                          │                                       │
│                   ┌──────▼──────────┐                            │
│                   │ Proposal Builder │                            │
│                   │ (NIBO or READY + │                            │
│                   │  evidence list)  │                            │
│                   └────────┬─────────┘                            │
└────────────────────────────┼──────────────────────────────────────┘
                             │ Agent output
                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│               HUMAN APPROVAL LAYER (Portal)                         │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │ Agent Proposal:                                              │  │
│  │ ├─ Status: NIBO                                              │  │
│  │ ├─ Confidence: 92%                                           │  │
│  │ ├─ Issues Found:                                             │  │
│  │ │  • Missing beneficiary designation (required)              │  │
│  │ │  • Age mismatch: App says 45, Medical says 46              │  │
│  │ │  • Medical records > 3 years old (dated 2023)              │  │
│  │ ├─ Evidence: [quoted text from docs]                         │  │
│  │ └─ [APPROVE] [REJECT] [MODIFY]                               │  │
│  └──────────────────────────────────────────────────────────────┘  │
└─────────────┬──────────────────────────────────────────────────────┘
              │ Underwriter decision
              ▼
┌─────────────────────────────────────────────────────────────────────┐
│                  EXECUTION & AUDIT LAYER                            │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │ If APPROVED:                                                 │  │
│  │ ├─ Update application status → NIBO or READY                 │  │
│  │ ├─ Generate letter to applicant (template + issues list)     │  │
│  │ ├─ Store decision in database                                │  │
│  │ └─ Write audit log:                                          │  │
│  │    {app_id, timestamp, agent_proposal, underwriter_decision, │  │
│  │     approved_by, evidence_snapshot}                          │  │
│  │                                                               │  │
│  │ If REJECTED:                                                 │  │
│  │ └─ Mark as READY (proceed to next step)                      │  │
│  │    Write audit log with reason                               │  │
│  └──────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 5. Data Model

### **Input**
```
ApplicationRequest {
  application_id: string (unique)
  documents: [
    {
      type: "application_form" | "medical_records" | "id_document" | "beneficiary_form"
      file: PDF (base64 or path)
      uploaded_at: timestamp
    }
  ]
  policy_product: string ("TermLife_10yr", "UniversalLife", etc.)
  applicant_age: int (from application)
}
```

### **Agent Internal State**
```
AnalysisState {
  application_id: string
  extracted_text: {
    application_form: string
    medical_records: string
    id_document: string
    beneficiary_form: string
  }
  rules_applied: [string] (list of rules checked)
  issues_found: [
    {
      rule: string ("required_field_missing", "date_mismatch", etc.)
      severity: "error" | "warning"
      field: string
      expected: string
      actual: string
      evidence: string (quoted from documents)
      location: string ("page X of document Y")
    }
  ]
  proposal: {
    status: "NIBO" | "READY"
    confidence: float (0-1)
    reasoning: string
    next_steps: [string]
  }
}
```

### **Output (Agent Proposal)**
```
CompletionProposal {
  application_id: string
  agent_timestamp: timestamp
  proposed_status: "NIBO" | "READY"
  confidence_score: float (0-1)
  issues_summary: string (human-readable)
  issues_detail: [
    {
      issue: string
      severity: "critical" | "major" | "minor"
      evidence: string (quoted text)
      location: string
    }
  ]
  evidence_snapshot: {
    extracted_documents: {...}
    rules_checked: [string]
  }
  recommended_letter_text: string (template fill-in)
}
```

### **Audit Log Entry**
```
AuditLog {
  id: string (auto-generated)
  application_id: string
  timestamp: datetime
  event_type: "agent_proposal" | "underwriter_decision"
  agent_proposal: CompletionProposal
  underwriter_decision: {
    decided_by: string (underwriter ID)
    action: "approve" | "reject" | "modify"
    final_status: "NIBO" | "READY"
    notes: string (optional)
    timestamp: datetime
  }
  system_execution: {
    status_updated: bool
    letter_generated: bool
    timestamp: datetime
  }
}
```

---

## 6. Rules Engine

Rules are **deterministic, versioned, configurable**. Agent checks all; flags violations.

```
RULES = {
  "required_fields": {
    "description": "All required fields on application must be filled",
    "check": (form) => form.has("applicant_name") && form.has("dob") && 
                       form.has("email") && form.has("phone"),
    "error_message": "Missing required fields",
    "severity": "error"
  },
  
  "signature_present": {
    "description": "Application must be signed by applicant",
    "check": (form) => form.signature_present && form.signature_date_present,
    "error_message": "Signature or signature date missing",
    "severity": "error"
  },
  
  "date_consistency": {
    "description": "Age on application must match medical records (within 1 year)",
    "check": (app, medical) => {
      app_age = calculate_age(app.dob, app.date_signed);
      med_age = medical.applicant_age;
      return Math.abs(app_age - med_age) <= 1;
    },
    "error_message": "Age mismatch between application and medical records",
    "severity": "error"
  },
  
  "medical_recency": {
    "description": "Medical records must not be older than 3 years",
    "check": (medical) => (now - medical.exam_date) < 3_years,
    "error_message": "Medical records are older than 3 years",
    "severity": "warning"
  },
  
  "beneficiary_designated": {
    "description": "Beneficiary must be named and designated",
    "check": (beneficiary_form) => 
      beneficiary_form.beneficiary_name && 
      beneficiary_form.relationship &&
      beneficiary_form.percentage > 0,
    "error_message": "Beneficiary not properly designated",
    "severity": "error"
  },
  
  "id_valid": {
    "description": "Valid government ID provided (driver's license, passport)",
    "check": (id_doc) => id_doc.type in ["DL", "Passport"] && id_doc.expiry > now,
    "error_message": "Invalid or expired ID document",
    "severity": "error"
  }
}
```

**Rule Severity Levels:**
- **Error** → NIBO status (application incomplete)
- **Warning** → NIBO status (but less critical; underwriter might override)

---

## 7. Agent Logic (Step-by-Step)

### **LangGraph State Machine**

```
START
  │
  ├─→ [load_documents] Extract text from PDFs
  │     └─→ Extract text, detect document type, flag unreadable
  │
  ├─→ [classify_documents] Match docs to types
  │     └─→ Application form? Medical records? ID? Beneficiary?
  │
  ├─→ [retrieve_rules] Load rules for this product
  │     └─→ Fetch rules from database based on policy_product
  │
  ├─→ [analyze] Run each rule against documents
  │     ├─→ For each rule:
  │     │   ├─→ Extract relevant data
  │     │   ├─→ Apply check
  │     │   ├─→ If fails: record issue + evidence
  │     │   └─→ If passes: continue
  │     └─→ Compile issues_found
  │
  ├─→ [propose_status] Determine NIBO vs READY
  │     ├─→ If any ERROR-severity issues → NIBO
  │     ├─→ If only WARNING-severity issues → offer both options
  │     └─→ Calculate confidence score (% of rules passed)
  │
  ├─→ [build_proposal] Create structured output
  │     ├─→ Format issues for human readability
  │     ├─→ Include evidence (quoted text)
  │     ├─→ Generate letter template with issues
  │     └─→ Serialize proposal
  │
  ├─→ [return_to_human] Hand off to underwriter
  │     └─→ Present CompletionProposal via portal
  │
  └─→ END (awaiting human decision)
```

---

## 8. Human-in-the-Loop: The Approval Gate

```
┌─ UNDERWRITER REVIEWS ──────────────────┐
│                                        │
│ Agent Proposal:                        │
│ ├─ Status: NIBO (92% confidence)       │
│ ├─ Issues:                             │
│ │  ├─ ERROR: Missing beneficiary       │
│ │  ├─ ERROR: Age mismatch (45 vs 46)   │
│ │  └─ WARNING: Medical records > 3yr   │
│ ├─ Evidence: [quoted from docs]        │
│                                        │
│ Options:                               │
│ ☐ APPROVE (accept NIBO status)        │
│ ☐ REJECT (override to READY)          │
│ ☐ MODIFY (edit issues list)            │
│                                        │
│ Approval Notes: ________________       │
│ [Submit]                               │
└────────────────────────────────────────┘
         │
         ├─→ APPROVE: Status updated, letter generated, audit logged
         ├─→ REJECT: Status → READY, reason logged, audit recorded
         └─→ MODIFY: Underwriter edits list, then approve/reject
```

**Key:** Underwriter sees agent's reasoning + evidence. Can second-guess agent. All decisions logged.

---

## 9. Audit Trail

Every decision is immutable, versioned, and traced:

```json
{
  "audit_log": [
    {
      "id": "audit_20261021_001",
      "application_id": "APP_12345",
      "timestamp": "2026-10-21T14:30:00Z",
      "event": "agent_analysis_complete",
      "agent_proposal": {
        "status": "NIBO",
        "confidence": 0.92,
        "issues_found": 3,
        "issues": [
          {
            "rule": "beneficiary_designated",
            "severity": "error",
            "evidence": "Beneficiary field left blank on form page 2"
          }
        ]
      }
    },
    {
      "id": "audit_20261021_002",
      "application_id": "APP_12345",
      "timestamp": "2026-10-21T14:33:00Z",
      "event": "underwriter_decision",
      "underwriter": {
        "id": "UW_sarah_chen",
        "name": "Sarah Chen",
        "action": "approve",
        "final_status": "NIBO",
        "notes": "Agreed with agent; applicant is missing beneficiary. Sending letter."
      }
    },
    {
      "id": "audit_20261021_003",
      "application_id": "APP_12345",
      "timestamp": "2026-10-21T14:33:15Z",
      "event": "system_execution",
      "execution": {
        "status_updated": true,
        "letter_generated": true,
        "letter_sent_to": "applicant@email.com"
      }
    }
  ]
}
```

**Why audit trail matters:**
- Compliance: Regulators can see every decision + reasoning
- Debugging: If something goes wrong, trace back to agent analysis + human decision
- Feedback: Review what agent flagged vs. what mattered; improve rules

---

## 10. Tech Stack & Architecture

| Layer | Technology | Why |
|-------|-----------|-----|
| **Frontend** | React (web form upload) or FastAPI + Jinja template | Simple, fast to build |
| **API** | FastAPI (Python) | Fast, async, integrates with LangGraph |
| **Agent** | LangGraph (StateGraph) | Orchestrates multi-step flows, stateful |
| **LLM** | Claude | Good at reasoning + understanding docs |
| **Document Processing** | PyPDF2 or pdfplumber | Extract text from PDFs |
| **Rules Engine** | Python + Pydantic models | Versioned, testable, deterministic |
| **Database** | SQLite (dev) or PostgreSQL (prod) | Store applications, audit logs, rules |
| **Deployment** | Render (free tier) or local FastAPI + SQLite | Simple, no Docker overhead for demo |

### **Folder Structure**
```
completeness-agent/
├── app.py                 # FastAPI app
├── agent.py              # LangGraph agent definition
├── rules.py              # Rules engine
├── models.py             # Pydantic models (input/output)
├── database.py           # SQLite/PostgreSQL connections
├── templates/
│   └── index.html        # Web UI for upload + approval
├── static/
│   └── style.css
├── requirements.txt
└── README.md
```

---

## 11. Demo Script (Sunday night)

**Setup:** 3 sample applications (1 NIBO, 1 READY, 1 edge case)

**Flow:**
1. **Upload:** "Here's a life insurance app with docs."
2. **Analyze:** Click [Analyze] → Agent runs → shows proposal in ~5 seconds
3. **Review:** "Agent found missing beneficiary + age mismatch. Here's the evidence."
4. **Approve:** Underwriter clicks [Approve] → status updates, letter generated
5. **Audit:** Show audit log: "Who did what, when, why?"

**Time:** ~3 minutes (clear, tight)

---

## 12. Success Criteria

✅ Agent autonomously analyzes documents  
✅ Proposes NIBO or READY with evidence  
✅ Underwriter approves in <2 minutes  
✅ System updates + generates letter  
✅ Full audit trail visible  
✅ Rules engine is deterministic (no surprise decisions)  
✅ Code is clean + ready for presales demo

---

## Next Steps

**Day 1 (Today, Wed Oct 21):** ✅ Design complete (NIBO / READY naming locked)  
**Day 2 (Thu Oct 22):** Build core (LangGraph agent + rules + state)  
**Day 3 (Fri Oct 23):** Wire human approval + audit + test + demo  
**Sunday 11:59pm (Oct 25):** Submit (video + GitHub + Google Doc)
