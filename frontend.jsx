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
  if (!response.ok)
    throw new Error(
      typeof data.detail === "string"
        ? data.detail
        : JSON.stringify(data.detail || data),
    );
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
      const result = await api("/analyze", {
        application_id: appId.trim(),
        policy_product: product,
        documents,
      });
      await load(result.application_id);
      setTab("review");
      setNotes("");
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy("");
    }
  }
  async function lookup() {
    if (!appId.trim()) return;
    setError("");
    setNotice("");
    setRecord(null);
    setAudit([]);
    setBusy("Loading application");
    try {
      await load(appId.trim());
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
        `Decision recorded. Application is ${result.status}. ${result.execution.letter_generated ? "The applicant letter is stored below." : "No applicant letter was generated."}`,
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
                <h2>Application packet</h2>
                <p>Upload a new packet or open an existing review.</p>
              </div>
            </div>
            <form onSubmit={analyze}>
              <div className="form-row">
                <label>
                  Application ID
                  <input
                    required
                    value={appId}
                    onChange={(e) => setAppId(e.target.value)}
                    placeholder="e.g. APP_1001"
                    disabled={!!busy}
                  />
                </label>
                <label>
                  Policy product
                  <select
                    value={product}
                    onChange={(e) => setProduct(e.target.value)}
                    disabled={!!busy}
                  >
                    <option value="TermLife_10yr">Term Life · 10 year</option>
                    <option value="TermLife_20yr">Term Life · 20 year</option>
                    <option value="UniversalLife">Universal Life</option>
                  </select>
                </label>
                <button
                  type="button"
                  className="secondary lookup"
                  onClick={lookup}
                  disabled={!!busy || !appId.trim()}
                >
                  Open review ↗
                </button>
              </div>
              {tab === "review" && (
                <>
                  <label className="upload">
                    <span className="upload-icon">↑</span>
                    <strong>Choose PDF documents</strong>
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
                      onChange={(e) => setFiles(Array.from(e.target.files))}
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
                  <div className="intake-footer">
                    <p>
                      {files.length
                        ? `${files.length} document${files.length === 1 ? "" : "s"} selected`
                        : "Documents are processed locally."}
                    </p>
                    <button
                      className="primary"
                      disabled={!!busy || !files.length || !appId.trim()}
                    >
                      Analyze packet <span>→</span>
                    </button>
                  </div>
                </>
              )}
            </form>
          </section>
          {!proposal ? (
            <section className="empty">
              <div className="empty-icon">▤</div>
              <h2>Your next review starts here.</h2>
              <p>
                Add an application packet to see the completeness assessment and
                its supporting evidence.
              </p>
            </section>
          ) : tab === "audit" ? (
            <section className="card audit">
              <div className="section-title">
                <span className="step">02</span>
                <div>
                  <h2>Audit trail · {record.application_id}</h2>
                  <p>
                    {audit.length} recorded events · Original proposals and
                    decisions are preserved.
                  </p>
                </div>
              </div>
              <ol className="timeline">
                {audit.map((entry) => (
                  <li key={entry.id}>
                    <div className="timeline-dot" />
                    <div>
                      <div className="event-title">
                        <h3>
                          {entry.event_type === "agent_proposal"
                            ? "Agent proposal"
                            : entry.event_type === "underwriter_decision"
                              ? "Underwriter decision"
                              : "System execution"}
                        </h3>
                        <time>{stamp(entry.timestamp)}</time>
                      </div>
                      {entry.agent_proposal && (
                        <p>
                          Proposed {entry.agent_proposal.proposed_status} ·{" "}
                          {entry.agent_proposal.issues_detail.length} issue(s)
                        </p>
                      )}
                      {entry.underwriter_decision && (
                        <p>
                          {entry.underwriter_decision.decided_by} ·{" "}
                          {entry.underwriter_decision.action} · Final status{" "}
                          {entry.underwriter_decision.final_status}
                          <br />
                          {entry.underwriter_decision.notes}
                        </p>
                      )}
                      {entry.system_execution && (
                        <p>
                          Status updated:{" "}
                          {entry.system_execution.status_updated ? "Yes" : "No"}{" "}
                          · Letter stored:{" "}
                          {entry.system_execution.letter_generated
                            ? "Yes"
                            : "No"}
                        </p>
                      )}
                      <details>
                        <summary>View complete record</summary>
                        <pre>{JSON.stringify(entry, null, 2)}</pre>
                      </details>
                    </div>
                  </li>
                ))}
              </ol>
            </section>
          ) : (
            <>
              <section className="assessment">
                <div>
                  <div className="eyebrow">
                    {record.application_id} ·{" "}
                    {pending ? "AWAITING YOUR DECISION" : "REVIEW COMPLETE"}
                  </div>
                  <h2>
                    {pending
                      ? `Proposed ${proposal.proposed_status}`
                      : `Final status: ${record.status}`}
                  </h2>
                  <p>
                    {pending
                      ? proposal.issues_summary
                      : `Underwriter review complete · ${(record.final_issues || proposal.issues_detail).length} recorded requirement(s).`}
                  </p>
                  {proposal.alternative_status && (
                    <p className="alternative">
                      Underwriter judgment required · Alternative:{" "}
                      {proposal.alternative_status}
                    </p>
                  )}
                </div>
                <div className="confidence">
                  <strong>
                    {Math.round(proposal.confidence_score * 100)}
                    <small>%</small>
                  </strong>
                  <span>Agent verdict confidence</span>
                  <Badge tone={pending ? "amber" : ""}>{record.status}</Badge>
                </div>
              </section>
              <div className="review-grid">
                <section className="card evidence">
                  <div className="section-title">
                    <span className="step">02</span>
                    <div>
                      <h2>
                        {editing
                          ? "Edit outstanding requirements"
                          : "Evidence & requirements"}
                      </h2>
                      <p>
                        {editing
                          ? "Your edits will be saved with your explicit approval."
                          : `${(record.final_issues || proposal.issues_detail).length} issue(s) to review`}
                      </p>
                    </div>
                  </div>
                  {(editing
                    ? issues
                    : record.final_issues || proposal.issues_detail
                  ).map((issue, index) => (
                    <article className="issue" key={index}>
                      <div className="issue-top">
                        <Badge
                          tone={
                            issue.severity === "critical"
                              ? "red"
                              : issue.severity === "major"
                                ? "amber"
                                : ""
                          }
                        >
                          {issue.severity}
                        </Badge>
                        <span>
                          {index + 1 < 10 ? "0" : ""}
                          {index + 1}
                        </span>
                      </div>
                      {editing ? (
                        <>
                          <label>
                            Requirement
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
                              <option>critical</option>
                              <option>major</option>
                              <option>minor</option>
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
                            Remove requirement
                          </button>
                        </>
                      ) : (
                        <>
                          <h3>{issue.issue}</h3>
                          <blockquote>
                            {issue.evidence || "No source quote available."}
                          </blockquote>
                          <p className="location">
                            ↳ {issue.location || "Location unavailable"}
                          </p>
                          {issue.expected && (
                            <p className="expected">
                              Expected: {issue.expected}{" "}
                              {issue.actual && `· Found: ${issue.actual}`}
                            </p>
                          )}
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
                      + Add requirement
                    </button>
                  )}
                  {!editing &&
                    !(record.final_issues || proposal.issues_detail).length && (
                      <div className="clear">
                        <span>✓</span>
                        <h3>No outstanding requirements</h3>
                        <p>
                          No outstanding requirements remain in this review.
                        </p>
                      </div>
                    )}
                  <details className="reasoning">
                    <summary>Assessment reasoning & next steps</summary>
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
                          ? "Review, then explicitly approve."
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
                              <option>NIBO</option>
                              <option>READY</option>
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
                            Save changes & approve {finalStatus}
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
                            Approve {proposal.proposed_status} <span>✓</span>
                          </button>
                          <button
                            className="secondary full"
                            disabled={!!busy || !name.trim()}
                            onClick={() => decide("reject")}
                          >
                            Reject proposal · Mark READY
                          </button>
                          <button
                            className="text-button full"
                            disabled={!!busy}
                            onClick={() => setEditing(true)}
                          >
                            Modify requirements
                          </button>
                        </>
                      )}
                      <p className="decision-note">
                        Your decision and supporting notes will be recorded in
                        the audit trail. NIBO letters are stored for review.
                      </p>
                    </>
                  ) : (
                    <>
                      <Badge>{record.status}</Badge>
                      <p className="decision-note">
                        The status and execution record have been saved.
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
                        ? "Applicant letter preview"
                        : "Stored applicant letter"}
                    </h2>
                    <p>
                      {pending
                        ? "Prepared for your review. Stored only after approval."
                        : "Available here for review and download."}
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
                      ? "The letter will be regenerated from your edited requirements when you approve NIBO."
                      : proposal.recommended_letter_text ||
                        "No letter required for a READY proposal."
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
