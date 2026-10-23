import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import "./style.css";

async function api(path, body) {
  const response = await fetch(
    path,
    body === undefined
      ? {}
      : {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        },
  );
  const data = await response.json();
  if (!response.ok) {
    const error = new Error(
      typeof data.detail === "string"
        ? data.detail
        : data.detail?.message || JSON.stringify(data.detail || data),
    );
    error.detail = data.detail;
    throw error;
  }
  return data;
}
function encode(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () =>
      resolve({
        filename: file.name,
        content_base64: reader.result.split(",")[1],
      });
    reader.onerror = () => reject(new Error(`Could not read ${file.name}`));
    reader.readAsDataURL(file);
  });
}
const stamp = (value) => new Date(value).toLocaleString();
function Badge({ children, tone = "" }) {
  return <span className={`badge ${tone}`}>{children}</span>;
}
// Business-language labels for the underwriter dashboard.
const CHECKS = {
  required_fields: "Required fields filled",
  signature_present: "Signed and dated",
  date_consistency: "Age matches medical records",
  medical_recency: "Medical records current",
  beneficiary_designated: "Beneficiary named",
  id_valid: "Valid government ID",
};
const DOCS = {
  application_form: "Application",
  medical_records: "Medical records",
  id_document: "ID document",
  beneficiary_form: "Beneficiary form",
};
const OUTCOME = {
  NIBO: "Incomplete: send back to applicant",
  READY: "Complete: ready for underwriting",
};
const RECOMMEND = {
  NIBO: "AI recommends: send back to applicant",
  READY: "AI recommends: ready for underwriting",
};
const FINAL = {
  NIBO: "Decision: additional information required",
  READY: "Decision: complete, ready for underwriting",
};
const PRODUCTS = {
  TermLife_10yr: "Term Life · 10 year",
  TermLife_20yr: "Term Life · 20 year",
  UniversalLife: "Universal Life",
};
const STATUS_LABEL = {
  PROPOSED: "Awaiting your decision",
  NIBO: "Incomplete",
  READY: "Complete",
};
const IMPACT = {
  critical: ["Blocks underwriting", "red"],
  major: ["Needs attention", "amber"],
  minor: ["Please verify", ""],
};
function resolutionsFor(proposal) {
  return Object.values(proposal?.gap_resolutions || {});
}
function followUp(proposal, rule) {
  const r = resolutionsFor(proposal).find((g) => g.original_issue?.rule === rule);
  if (!r) return null;
  if (r.resolved)
    return { tone: "", text: `Resolved by AI agent${r.simulated ? " (simulated data)" : ""}` };
  if (r.status === "unresolvable")
    return { tone: "red", text: "AI agent could not resolve this · needs your review" };
  const who =
    rule === "medical_recency"
      ? "Request to the doctor"
      : rule === "id_valid"
        ? "DMV could not confirm · request to applicant"
        : "Request to applicant";
  return { tone: "amber", text: `${who} drafted · pending` };
}
function applicantName(proposal) {
  const docs = Object.values(proposal?.evidence_snapshot?.extracted_documents || {});
  const app = docs.find((d) => d.doc_type === "application_form");
  return app?.fields?.applicant_name || null;
}
// ---------- Audit trail: where the case is in the process ----------
function since(value) {
  const mins = Math.max(0, Math.round((Date.now() - new Date(value)) / 60000));
  if (mins < 60) return `${mins} min`;
  const h = Math.floor(mins / 60);
  if (h < 48) return `${h} h ${mins % 60} min`;
  return `${Math.floor(h / 24)} days`;
}
const ACTION_TEXT = {
  approve: (d) =>
    d.final_status === "NIBO" ? "approved a request for additional information" : "approved it as complete",
  reject: () => "overruled the AI and marked the case complete",
  modify: (d) =>
    d.final_status === "NIBO"
      ? "edited and approved the missing-information request"
      : "edited the review and marked the case complete",
};
function caseSteps(record, audit) {
  const proposals = audit.filter((e) => e.event_type === "agent_proposal");
  const latest = proposals[proposals.length - 1];
  const p = latest?.agent_proposal || record.proposal;
  const after = latest ? audit.slice(audit.indexOf(latest) + 1) : [];
  const decision = after.find((e) => e.event_type === "underwriter_decision");
  const execution = after.find((e) => e.event_type === "system_execution");
  const docs = Object.values(p?.evidence_snapshot?.extracted_documents || {});
  const issues = decision?.underwriter_decision?.final_issues || p?.issues_detail || [];
  const checks = p?.evidence_snapshot?.rules_checked || [];
  const open = new Set((p?.issues_detail || []).map((i) => i.rule));
  const gaps = resolutionsFor(p);
  const t = latest?.timestamp || p?.agent_timestamp;
  const prev = proposals[proposals.length - 2]?.agent_proposal;
  const finalStatus = decision?.underwriter_decision?.final_status;

  const steps = [
    {
      key: "received", title: "Case received", state: "done", time: t,
      detail: [
        `${docs.length} document${docs.length === 1 ? "" : "s"}: ` +
          docs.map((d) => DOCS[d.doc_type] || d.filename).join(", "),
      ],
    },
    {
      key: "review", title: "AI document review", state: "done", time: t,
      detail: [
        docs.every((d) => d.readable)
          ? "Every document was read."
          : `${docs.filter((d) => !d.readable).length} document(s) could not be read: ` +
            docs.filter((d) => !d.readable).map((d) => d.filename).join(", "),
      ],
    },
    {
      key: "check", title: "Completeness check", state: "done", time: t,
      sub: `${checks.length - open.size} of ${checks.length} passed`,
      detail: checks.map((c) => `${open.has(c) ? "✗" : "✓"} ${CHECKS[c] || c}`),
    },
    {
      key: "followup", title: "AI follow-up", time: t,
      state: gaps.length ? "done" : "skipped",
      sub: gaps.length
        ? [
            gaps.filter((g) => g.resolved).length && `${gaps.filter((g) => g.resolved).length} resolved`,
            gaps.filter((g) => g.status === "pending").length && `${gaps.filter((g) => g.status === "pending").length} pending`,
            gaps.filter((g) => g.status === "unresolvable").length && `${gaps.filter((g) => g.status === "unresolvable").length} need you`,
          ].filter(Boolean).join(" · ")
        : "Nothing to chase",
      detail: gaps.length
        ? gaps.map((g) => `${CHECKS[g.original_issue.rule] || g.original_issue.message}: ${followUp(p, g.original_issue.rule)?.text}`)
        : ["Every check passed, so there was nothing to follow up."],
    },
    {
      key: "recommend", title: "AI recommendation", state: "done", time: t,
      sub: p?.proposed_status === "NIBO" ? "Incomplete" : "Complete",
      detail: [
        `${RECOMMEND[p?.proposed_status] || p?.proposed_status} · confidence ${Math.round((p?.confidence_score || 0) * 100)}%`,
        ...(p?.issues_detail || []).map(
          (i) => `✗ ${i.issue}${i.location ? ` (${i.location})` : ""}`,
        ),
        proposals.length > 1
          ? `Reviewed ${proposals.length} times. Previous review found ${prev?.issues_detail?.length ?? 0} missing item(s); this one found ${(p?.issues_detail || []).length}.`
          : null,
      ].filter(Boolean),
      issues: p?.issues_detail || [],
    },
    decision
      ? {
          key: "decision", title: "Underwriter decision", state: "done", time: decision.timestamp,
          sub: finalStatus === "NIBO" ? "Information requested" : "Marked complete",
          detail: [
            `${decision.underwriter_decision.decided_by} ${(ACTION_TEXT[decision.underwriter_decision.action] || (() => decision.underwriter_decision.action))(decision.underwriter_decision)}.`,
            decision.underwriter_decision.notes ? `Note: "${decision.underwriter_decision.notes}"` : null,
          ].filter(Boolean),
        }
      : {
          key: "decision", title: "Underwriter decision", state: "current", time: null,
          sub: "Waiting on underwriter",
          waiting: t ? `Waiting on the underwriter since ${stamp(t)} (${since(t)})` : "Waiting on the underwriter",
          detail: ["The AI recommendation is ready. No decision has been made yet."],
        },
  ];
  if (!decision) {
    steps.push({ key: "notified", title: "Applicant notified", state: "todo" });
    steps.push({ key: "ready", title: "Ready for underwriting", state: "todo" });
  } else if (finalStatus === "NIBO") {
    steps.push({
      key: "notified", title: "Applicant notification", state: "current", time: execution?.timestamp,
      waiting: "Waiting for staff to send the stored letter; no notification has been sent.",
      sub: execution?.system_execution?.letter_generated ? "Letter prepared" : "No letter",
      detail: [
        `Requested: ${issues.map((i) => i.issue).join("; ") || "missing information"}.`,
        "The letter is prepared and stored. Sending is not connected in this prototype.",
      ],
      letter: execution?.system_execution?.stored_letter_text || record.stored_letter_text,
    });
    steps.push({
      key: "ready", title: "Ready for underwriting", state: "todo",
      sub: "Missing information still required",
      detail: ["Once the applicant sends the missing information, the case is reviewed again."],
    });
  } else {
    steps.push({ key: "notified", title: "Applicant notified", state: "skipped", sub: "Not needed" });
    steps.push({
      key: "ready", title: "Ready for underwriting", state: "done", time: execution?.timestamp || decision.timestamp,
      sub: "Complete", detail: ["The file is complete and can move on to underwriting."],
    });
  }
  return steps;
}
function AuditView({ record, audit }) {
  const steps = caseSteps(record, audit);
  const current = steps.find((s) => s.state === "current");
  return (
    <section className="card audit">
      <div className="section-title">
        <span className="step">02</span>
        <div>
          <h2>Where this case is · {record.application_id}</h2>
          <p>
            {current
              ? current.waiting
              : "This case has finished every step."}
          </p>
        </div>
      </div>
      <ol className="stepper">
        {steps.map((st, i) => (
          <li key={st.key} className={`st ${st.state}`}>
            <span className="st-dot">
              {st.state === "done" ? "✓" : st.state === "current" ? "●" : st.state === "skipped" ? "–" : i + 1}
            </span>
            <strong>{st.title}</strong>
            {st.sub && <small>{st.sub}</small>}
            {st.state === "current" && <em className="stopped">Stopped here</em>}
          </li>
        ))}
      </ol>
      <ol className="step-details">
        {steps
          .filter((st) => st.state !== "todo")
          .map((st) => (
            <li key={st.key} className={`sd ${st.state}`}>
              <div className="sd-head">
                <h3>{st.title}</h3>
                <time>
                  {st.state === "current" ? "Now" : st.time ? stamp(st.time) : ""}
                </time>
              </div>
              {(st.detail || []).map((line, n) => (
                <p key={n} className={line.startsWith("✗") ? "bad" : line.startsWith("✓") ? "good" : ""}>
                  {line}
                </p>
              ))}
              {st.letter && (
                <details className="letter-peek">
                  <summary>Read the letter</summary>
                  <pre>{st.letter}</pre>
                </details>
              )}
            </li>
          ))}
      </ol>
    </section>
  );
}
function App() {
  const [health, setHealth] = useState(null),
    [appId, setAppId] = useState(""),
    [product, setProduct] = useState("TermLife_10yr");
  const [files, setFiles] = useState([]),
    [record, setRecord] = useState(null),
    [audit, setAudit] = useState([]),
    [tab, setTab] = useState("review");
  const [error, setError] = useState(""),
    [busy, setBusy] = useState(""),
    [seconds, setSeconds] = useState(0),
    [notice, setNotice] = useState("");
  const [name, setName] = useState(""),
    [notes, setNotes] = useState(""),
    [editing, setEditing] = useState(false),
    [issues, setIssues] = useState([]),
    [finalStatus, setFinalStatus] = useState("NIBO");
  const [lookupId, setLookupId] = useState(""),
    [needs, setNeeds] = useState([]);
  const proposal = record?.proposal,
    pending = record?.status === "PROPOSED";
  useEffect(() => {
    api("/health")
      .then(setHealth)
      .catch(() => setHealth({ agent_mode: "unavailable" }));
  }, []);
  useEffect(() => {
    if (!busy) return;
    setSeconds(0);
    const timer = setInterval(() => setSeconds((n) => n + 1), 1000);
    return () => clearInterval(timer);
  }, [busy]);
  async function load(id) {
    const [row, trail] = await Promise.all([
      api(`/applications/${encodeURIComponent(id)}`),
      api(`/applications/${encodeURIComponent(id)}/audit`),
    ]);
    setRecord(row);
    setAudit(trail.audit_log);
    setIssues(row.proposal?.issues_detail || []);
    setFinalStatus(row.proposal?.proposed_status || "NIBO");
    setEditing(false);
  }
  async function analyze(event) {
    event.preventDefault();
    setError("");
    setNotice("");
    setRecord(null);
    setAudit([]);
    setBusy("Analyzing documents");
    try {
      const documents = await Promise.all(files.map(encode));
      const body = { documents };
      if (needs.includes("application_id") && appId.trim())
        body.application_id = appId.trim();
      if (needs.includes("policy_product")) body.policy_product = product;
      const result = await api("/analyze", body);
      setNeeds([]);
      await load(result.application_id);
      setTab("review");
      setNotes("");
    } catch (e) {
      if (e.detail?.needs) {
        setNeeds(e.detail.needs);
        setError(`${e.message} Enter it below, then analyze again.`);
      } else setError(e.message);
    } finally {
      setBusy("");
    }
  }
  async function lookup() {
    if (!lookupId.trim()) return;
    setError("");
    setNotice("");
    setRecord(null);
    setAudit([]);
    setBusy("Loading application");
    try {
      await load(lookupId.trim());
      setTab("review");
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy("");
    }
  }
  async function decide(action) {
    if (!name.trim()) {
      setError(
        "Enter your underwriter name or ID before submitting a decision.",
      );
      return;
    }
    setError("");
    setNotice("");
    setBusy("Saving decision");
    try {
      const body = {
        action,
        decided_by: name.trim(),
        notes,
        expected_proposal_timestamp: proposal.agent_timestamp,
      };
      if (action === "modify") {
        body.modified_issues = issues;
        body.final_status = finalStatus;
      }
      const result = await api(
        `/applications/${encodeURIComponent(record.application_id)}/decision`,
        body,
      );
      await load(record.application_id);
      setNotice(
        `Decision recorded. Application is ${(STATUS_LABEL[result.status] || result.status).toLowerCase()}. ${result.execution.letter_generated ? "The applicant letter is stored below." : "No applicant letter was generated."}`,
      );
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy("");
    }
  }
  function changeIssue(index, key, value) {
    setIssues((items) =>
      items.map((item, n) => (n === index ? { ...item, [key]: value } : item)),
    );
  }
  function downloadLetter() {
    const url = URL.createObjectURL(
      new Blob([record.stored_letter_text], {
        type: "text/plain;charset=utf-8",
      }),
    );
    const a = document.createElement("a");
    a.href = url;
    a.download = `${record.application_id}-letter.txt`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  return (
    <div className="shell">
      <aside className="sidebar">
        <a className="brand" href="/" aria-label="NIBO home">
          <span className="brand-mark">n.</span>
          <span>
            NIBO<small>UNDERWRITER WORKSPACE</small>
          </span>
        </a>
        <div className="sidebar-label">WORKSPACE</div>
        <button
          className={tab === "review" ? "nav active" : "nav"}
          onClick={() => setTab("review")}
        >
          <span>▤</span> Application review
        </button>
        <button
          className={tab === "audit" ? "nav active" : "nav"}
          onClick={() => setTab("audit")}
        >
          <span>◷</span> Audit trail{" "}
          <span className="nav-count">{audit.length}</span>
        </button>
        <div className="sidebar-bottom">
          <span className="live-dot" /> Local workspace
          <strong>Human judgment, supported.</strong>
          <p>Every assessment is a proposal. You make the final decision.</p>
        </div>
      </aside>
      <main>
        <header className="topbar">
          <span>
            Underwriting /{" "}
            <b>{tab === "review" ? "Application review" : "Audit trail"}</b>
          </span>
          <Badge tone={health?.agent_mode === "mock" ? "amber" : ""}>
            {health?.agent_mode === "mock"
              ? "SAMPLE MODE"
              : health?.agent_mode === "ollama"
                ? "LOCAL MODEL"
                : health
                  ? "UNAVAILABLE"
                  : "CONNECTING"}
          </Badge>
        </header>
        <div className="content">
          <div className="page-heading">
            <div className="eyebrow">APPLICATION COMPLETENESS</div>
            <h1>
              {tab === "review"
                ? "A clearer path to a decision."
                : "The complete decision record."}
            </h1>
            <p>
              {tab === "review"
                ? "Review the evidence, resolve outstanding requirements, and move the application forward."
                : "Follow the proposal, the human decision, and the recorded outcome."}
            </p>
          </div>
          {error && (
            <div className="alert error" role="alert">
              {error}
            </div>
          )}
          {notice && (
            <div className="alert success" role="status">
              {notice}
            </div>
          )}
          {busy && (
            <div className="progress" role="status">
              <span className="spinner" />
              <div>
                <strong>
                  {busy} · {seconds}s
                </strong>
                <p>
                  {busy === "Analyzing documents"
                    ? "Reading your packet. Local analysis may take several minutes."
                    : "Please wait."}
                </p>
              </div>
            </div>
          )}
          <section className="card intake">
            <div className="section-title">
              <span className="step">01</span>
              <div>
                <h2>{tab === "review" ? "New case" : "Find a case"}</h2>
                <p>
                  {tab === "review"
                    ? "Add the case documents. The application number and product are read from them automatically."
                    : "Open the history of an existing case."}
                </p>
              </div>
            </div>
            {tab === "review" && (
              <form onSubmit={analyze}>
                <label className="upload">
                  <span className="upload-icon">↑</span>
                  <strong>Choose the case documents (PDF)</strong>
                  <span>
                    Application, medical records, government ID, and
                    beneficiary designation
                  </span>
                  <input
                    type="file"
                    multiple
                    accept=".pdf,application/pdf"
                    aria-label="PDF documents"
                    disabled={!!busy}
                    onChange={(e) => {
                      setFiles(Array.from(e.target.files));
                      setNeeds([]);
                    }}
                  />
                </label>
                {files.length > 0 && (
                  <ul className="file-list">
                    {files.map((file, index) => (
                      <li key={index}>
                        <span>PDF</span>
                        {file.name}
                        <small>{Math.ceil(file.size / 1024)} KB</small>
                      </li>
                    ))}
                  </ul>
                )}
                {needs.length > 0 && (
                  <div className="form-row needs">
                    {needs.includes("application_id") && (
                      <label>
                        Application number
                        <input
                          required
                          value={appId}
                          onChange={(e) => setAppId(e.target.value)}
                          placeholder="e.g. APP_1001"
                          disabled={!!busy}
                        />
                      </label>
                    )}
                    {needs.includes("policy_product") && (
                      <label>
                        Policy product
                        <select
                          value={product}
                          onChange={(e) => setProduct(e.target.value)}
                          disabled={!!busy}
                        >
                          {Object.entries(PRODUCTS).map(([value, label]) => (
                            <option key={value} value={value}>
                              {label}
                            </option>
                          ))}
                        </select>
                      </label>
                    )}
                  </div>
                )}
                <div className="intake-footer">
                  <p>
                    {files.length
                      ? `${files.length} document${files.length === 1 ? "" : "s"} selected`
                      : "Documents are processed locally."}
                  </p>
                  <button
                    className="primary"
                    disabled={
                      !!busy ||
                      !files.length ||
                      (needs.includes("application_id") && !appId.trim())
                    }
                  >
                    Analyze case <span>→</span>
                  </button>
                </div>
              </form>
            )}
            <div className="lookup-row">
              <label>
                {tab === "review" ? "Or open an existing case" : "Application number"}
                <input
                  value={lookupId}
                  onChange={(e) => setLookupId(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && lookup()}
                  placeholder="e.g. APP_1001"
                  disabled={!!busy}
                />
              </label>
              <button
                type="button"
                className="secondary lookup"
                onClick={lookup}
                disabled={!!busy || !lookupId.trim()}
              >
                Open case ↗
              </button>
            </div>
          </section>
          {!proposal ? (
            <section className="empty">
              <div className="empty-icon">▤</div>
              <h2>Your next review starts here.</h2>
              <p>
                Add a case's documents to see what's missing, with the evidence
                behind each item.
              </p>
            </section>
          ) : tab === "audit" ? (
            <AuditView record={record} audit={audit} />
          ) : (
            <>
              <section className="assessment">
                <div>
                  <div className="eyebrow">
                    {record.application_id}
                    {applicantName(proposal) ? ` · ${applicantName(proposal)}` : ""}
                    {record.policy_product ? ` · ${PRODUCTS[record.policy_product] || record.policy_product}` : ""} ·{" "}
                    {pending ? "AWAITING YOUR DECISION" : "DECISION RECORDED"}
                  </div>
                  <h2>
                    {pending
                      ? RECOMMEND[proposal.proposed_status] || proposal.proposed_status
                      : FINAL[record.status] || record.status}
                  </h2>
                  <p className="missing-count">
                    {(() => {
                      const n = (record.final_issues || proposal.issues_detail).length;
                      return n
                        ? `${n} item${n === 1 ? "" : "s"} missing before underwriting can continue`
                        : "Nothing missing. The file is complete.";
                    })()}
                  </p>
                  {proposal.alternative_status && (
                    <p className="alternative">
                      Your judgment needed: the AI also considered{" "}
                      {(OUTCOME[proposal.alternative_status] || proposal.alternative_status).toLowerCase()}
                    </p>
                  )}
                </div>
                <div className="confidence">
                  <strong>
                    {Math.round(proposal.confidence_score * 100)}
                    <small>%</small>
                  </strong>
                  <span>AI confidence</span>
                  <Badge tone={pending ? "amber" : ""}>{STATUS_LABEL[record.status] || record.status}</Badge>
                </div>
              </section>
              <div className="review-grid">
                <section className="card evidence">
                  <div className="section-title">
                    <span className="step">02</span>
                    <div>
                      <h2>
                        {editing ? "Edit missing items" : "What's missing"}
                      </h2>
                      <p>
                        {editing
                          ? "Your edits will be saved with your explicit approval."
                          : "Each item shows the exact text and where it was found."}
                      </p>
                    </div>
                  </div>
                  {(editing
                    ? issues
                    : record.final_issues || proposal.issues_detail
                  ).map((issue, index) => (
                    <article className="issue" key={index}>
                      <div className="issue-top">
                        <Badge tone={(IMPACT[issue.severity] || ["", ""])[1]}>
                          {(IMPACT[issue.severity] || [issue.severity])[0]}
                        </Badge>
                        <span>
                          {index + 1 < 10 ? "0" : ""}
                          {index + 1}
                        </span>
                      </div>
                      {editing ? (
                        <>
                          <label>
                            Missing item
                            <textarea
                              required
                              value={issue.issue}
                              onChange={(e) =>
                                changeIssue(index, "issue", e.target.value)
                              }
                            />
                          </label>
                          <label>
                            Severity
                            <select
                              value={issue.severity}
                              onChange={(e) =>
                                changeIssue(index, "severity", e.target.value)
                              }
                            >
                              <option value="critical">Blocks underwriting</option>
                              <option value="major">Needs attention</option>
                              <option value="minor">Please verify</option>
                            </select>
                          </label>
                          <label>
                            Evidence
                            <textarea
                              value={issue.evidence}
                              onChange={(e) =>
                                changeIssue(index, "evidence", e.target.value)
                              }
                            />
                          </label>
                          <label>
                            Location
                            <input
                              value={issue.location}
                              onChange={(e) =>
                                changeIssue(index, "location", e.target.value)
                              }
                            />
                          </label>
                          <button
                            className="text-button danger"
                            onClick={() =>
                              setIssues((items) =>
                                items.filter((_, n) => n !== index),
                              )
                            }
                          >
                            Remove item
                          </button>
                        </>
                      ) : (
                        <>
                          <h3 className="missing-title">
                            <span className="miss">✗</span> {issue.issue}
                          </h3>
                          <blockquote>
                            {issue.evidence || "No source quote available."}
                          </blockquote>
                          <p className="location">
                            Found in: {issue.location || "location unavailable"}
                          </p>
                          {issue.expected && (
                            <p className="expected">
                              Expected: {issue.expected}{" "}
                              {issue.actual && `· Found: ${issue.actual}`}
                            </p>
                          )}
                          {!editing &&
                            (() => {
                              const f = followUp(proposal, issue.rule);
                              return f ? (
                                <p className={`followup ${f.tone}`}>
                                  <b>AI follow-up:</b> {f.text}
                                </p>
                              ) : null;
                            })()}
                        </>
                      )}
                    </article>
                  ))}
                  {editing && (
                    <button
                      className="secondary add"
                      onClick={() =>
                        setIssues((items) => [
                          ...items,
                          {
                            issue: "",
                            rule: "underwriter_review",
                            severity: "major",
                            evidence: "",
                            location: "",
                          },
                        ])
                      }
                    >
                      + Add missing item
                    </button>
                  )}
                  {!editing &&
                    !(record.final_issues || proposal.issues_detail).length && (
                      <div className="clear">
                        <span>✓</span>
                        <h3>Nothing missing</h3>
                        <p>Every check passed. The file is complete.</p>
                      </div>
                    )}
                  {!editing &&
                    resolutionsFor(proposal).some((g) => g.resolved) && (
                      <div className="resolved-list">
                        <h3>Resolved by the AI agent before you opened this case</h3>
                        {resolutionsFor(proposal)
                          .filter((g) => g.resolved)
                          .map((g, i) => (
                            <p key={i}>
                              <span className="ok">✓</span>{" "}
                              {CHECKS[g.original_issue.rule] || g.original_issue.message}
                              {g.simulated ? " · simulated data" : ""}
                            </p>
                          ))}
                      </div>
                    )}
                  {!editing &&
                    (() => {
                      const open = new Set(
                        (record.final_issues || proposal.issues_detail).map((i) => i.rule),
                      );
                      const checks = proposal.evidence_snapshot?.rules_checked || [];
                      const passed = checks.filter((c) => !open.has(c)).length;
                      const docs = new Set(
                        Object.values(proposal.evidence_snapshot?.extracted_documents || {})
                          .filter((d) => d.readable)
                          .map((d) => d.doc_type),
                      );
                      return (
                        <div className="checklist">
                          <div className="checklist-head">
                            <h3>File checklist</h3>
                            <span>
                              {passed} of {checks.length} checks passed
                            </span>
                          </div>
                          <div className="check-group">
                            <small>Documents received</small>
                            {Object.entries(DOCS).map(([key, label]) => (
                              <span key={key} className={docs.has(key) ? "check ok" : "check no"}>
                                {docs.has(key) ? "✓" : "✗"} {label}
                              </span>
                            ))}
                          </div>
                          <div className="check-group">
                            <small>Checks</small>
                            {checks.map((c) => (
                              <span key={c} className={open.has(c) ? "check no" : "check ok"}>
                                {open.has(c) ? "✗" : "✓"} {CHECKS[c] || c}
                              </span>
                            ))}
                          </div>
                        </div>
                      );
                    })()}
                  <details className="reasoning">
                    <summary>Why the AI recommends this</summary>
                    <p>{proposal.reasoning}</p>
                    <ul>
                      {proposal.next_steps.map((step, index) => (
                        <li key={index}>{step}</li>
                      ))}
                    </ul>
                  </details>
                </section>
                <section className="card decision">
                  <div className="section-title">
                    <span className="step">03</span>
                    <div>
                      <h2>Your decision</h2>
                      <p>
                        {pending
                          ? "Check what's missing, then decide."
                          : "This review has been recorded."}
                      </p>
                    </div>
                  </div>
                  {pending ? (
                    <>
                      <label>
                        Underwriter name or ID
                        <input
                          value={name}
                          onChange={(e) => setName(e.target.value)}
                          placeholder="Your name or ID"
                          disabled={!!busy}
                        />
                      </label>
                      <label>
                        Decision notes
                        <textarea
                          value={notes}
                          onChange={(e) => setNotes(e.target.value)}
                          placeholder="Record your reasoning or any exceptions…"
                          disabled={!!busy}
                        />
                      </label>
                      {editing ? (
                        <>
                          <label>
                            Final status
                            <select
                              value={finalStatus}
                              onChange={(e) => setFinalStatus(e.target.value)}
                            >
                              <option value="NIBO">Incomplete: send back</option>
                              <option value="READY">Complete: ready for underwriting</option>
                            </select>
                          </label>
                          <button
                            className="primary full"
                            disabled={
                              !!busy ||
                              !name.trim() ||
                              issues.some((i) => !i.issue.trim()) ||
                              (finalStatus === "NIBO" && !issues.length)
                            }
                            onClick={() => decide("modify")}
                          >
                            {finalStatus === "NIBO"
                              ? "Save and send back to applicant"
                              : "Save and mark complete"}
                          </button>
                          <button
                            className="text-button"
                            onClick={() => {
                              setEditing(false);
                              setIssues(proposal.issues_detail);
                            }}
                          >
                            Cancel edits
                          </button>
                        </>
                      ) : (
                        <>
                          <button
                            className="primary full"
                            disabled={!!busy || !name.trim()}
                            onClick={() => decide("approve")}
                          >
                            {proposal.proposed_status === "NIBO"
                              ? "Send back to applicant"
                              : "Approve: ready for underwriting"}{" "}
                            <span>✓</span>
                          </button>
                          <button
                            className="secondary full"
                            disabled={!!busy || !name.trim()}
                            onClick={() => decide("reject")}
                          >
                            {proposal.proposed_status === "NIBO"
                              ? "Overrule AI: mark complete"
                              : "Reject recommendation"}
                          </button>
                          <button
                            className="text-button full"
                            disabled={!!busy}
                            onClick={() => setEditing(true)}
                          >
                            Edit missing items
                          </button>
                        </>
                      )}
                      <p className="decision-note">
                        Your decision and notes are recorded in the audit trail.
                      </p>
                    </>
                  ) : (
                    <>
                      <Badge>{STATUS_LABEL[record.status] || record.status}</Badge>
                      <p className="decision-note">
                        The case status and your decision have been saved.
                      </p>
                      <button
                        className="secondary full"
                        onClick={() => setTab("audit")}
                      >
                        View audit trail →
                      </button>
                    </>
                  )}
                </section>
              </div>
              <section className="card letter">
                <div className="section-title">
                  <span className="step">04</span>
                  <div>
                    <h2>
                      {pending
                        ? "Letter to applicant (preview)"
                        : "Letter to applicant"}
                    </h2>
                    <p>
                      {pending
                        ? "Stored only after approval; staff must send it separately."
                        : "Available here to review and download."}
                    </p>
                  </div>
                  {record.stored_letter_text && (
                    <button className="secondary" onClick={downloadLetter}>
                      Download .txt ↓
                    </button>
                  )}
                </div>
                <pre>
                  {pending
                    ? editing
                      ? "The letter will be rewritten from your edited list when you send the case back."
                      : proposal.recommended_letter_text ||
                        "No letter needed: the file is complete."
                    : record.stored_letter_text ||
                      "No applicant letter was generated for this decision."}
                </pre>
              </section>
            </>
          )}
          <footer>
            NIBO <span>Application completeness · Human approval required</span>
          </footer>
        </div>
      </main>
    </div>
  );
}
createRoot(document.getElementById("root")).render(<App />);
