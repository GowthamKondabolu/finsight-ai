"use client";

import { FormEvent, useMemo, useState } from "react";

import {
  getInvestigation,
  reviewInvestigation,
  startInvestigation,
  submitFeedback,
} from "@/lib/api";
import type {
  FeedbackRequest,
  InvestigationRequest,
  InvestigationWorkflow,
} from "@/lib/contracts";
import { demoWorkflow } from "@/lib/demo";
import {
  AlertIcon,
  CheckIcon,
  CompareIcon,
  ExternalIcon,
  FactIcon,
  SearchIcon,
  ShieldIcon,
} from "@/components/icons";

type Mode = "question" | "comparison" | "facts";

const MODES: Array<{ key: Mode; label: string; description: string }> = [
  { key: "question", label: "Filing Q&A", description: "Ask one evidence-bounded question" },
  { key: "comparison", label: "Risk comparison", description: "Compare disclosure emphasis over time" },
  { key: "facts", label: "Fact verification", description: "Recompute a claim from SEC XBRL facts" },
];

const DEFAULT_QUESTIONS: Record<Mode, string> = {
  question: "What material risks are emphasized in the latest annual filing?",
  comparison:
    "Compare material risk-factor changes across the selected filing periods. Distinguish new disclosures, removed disclosures, and changes in emphasis.",
  facts:
    "Verify the reported year-over-year revenue change using exact SEC company facts and show the deterministic calculation.",
};

function newThreadId(): string {
  return crypto.randomUUID();
}

function formatDate(value: string | null): string {
  if (!value) return "Period not supplied";
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    timeZone: "UTC",
  }).format(new Date(`${value}T00:00:00Z`));
}

function sourceMeta(source: InvestigationWorkflow["answer"]["sources"][number]): string {
  if (source.source_type === "financial_fact") {
    return `${source.fact_concept ?? "SEC fact"} · ${source.fact_value ?? "—"} ${source.fact_unit ?? ""}`;
  }
  return `${source.form_type} · ${formatDate(source.filing_date)} · ${source.section_name ?? "Filing passage"}`;
}

function safeSecSourceUrl(value: string): string | null {
  try {
    const url = new URL(value);
    const isSecHost = url.hostname === "sec.gov" || url.hostname.endsWith(".sec.gov");
    return url.protocol === "https:" && isSecHost ? url.toString() : null;
  } catch {
    return null;
  }
}

export function AnalystWorkspace() {
  const [mode, setMode] = useState<Mode>("question");
  const [cik, setCik] = useState("0000320193");
  const [formType, setFormType] = useState("10-K");
  const [filedFrom, setFiledFrom] = useState("2024-01-01");
  const [filedTo, setFiledTo] = useState("2026-12-31");
  const [question, setQuestion] = useState(DEFAULT_QUESTIONS.question);
  const [concepts, setConcepts] = useState(
    "RevenueFromContractWithCustomerExcludingAssessedTax",
  );
  const [workflow, setWorkflow] = useState<InvestigationWorkflow | null>(null);
  const [isFixture, setIsFixture] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [reviewerId, setReviewerId] = useState("");
  const [reviewNotes, setReviewNotes] = useState("");
  const [restoreId, setRestoreId] = useState("");
  const [feedbackSent, setFeedbackSent] = useState(false);

  const evidenceCount = workflow?.answer.sources.length ?? 0;
  const filingPeriods = useMemo(
    () =>
      new Set(
        workflow?.answer.sources
          .map((source) => source.filing_date ?? source.fact_end_date)
          .filter(Boolean),
      ).size,
    [workflow],
  );
  const sourceForms = useMemo(
    () =>
      [...new Set(workflow?.answer.sources.map((source) => source.form_type) ?? [])].join(
        ", ",
      ) || "Not reported",
    [workflow],
  );
  const evidencePeriod = useMemo(() => {
    const dates = (workflow?.answer.sources ?? [])
      .map((source) => source.filing_date ?? source.fact_end_date)
      .filter((value): value is string => Boolean(value))
      .sort();
    if (dates.length === 0) return "Not reported";
    const first = formatDate(dates[0]);
    const last = formatDate(dates[dates.length - 1]);
    return first === last ? first : `${first} — ${last}`;
  }, [workflow]);

  function selectMode(nextMode: Mode) {
    setMode(nextMode);
    setQuestion(DEFAULT_QUESTIONS[nextMode]);
    setMessage(null);
  }

  async function handleStart(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setMessage(null);
    setFeedbackSent(false);
    const request: InvestigationRequest = {
      thread_id: newThreadId(),
      question,
      cik: cik || undefined,
      form_types: [formType],
      filed_from: filedFrom || undefined,
      filed_to: filedTo || undefined,
      section_names: mode === "facts" ? [] : ["Risk Factors"],
      fact_concepts:
        mode === "facts"
          ? concepts.split(",").map((value) => value.trim()).filter(Boolean)
          : [],
      top_k: 8,
      candidate_k: 50,
      fact_limit: 30,
    };
    try {
      setWorkflow(await startInvestigation(request));
      setIsFixture(false);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Unable to start the investigation.");
    } finally {
      setBusy(false);
    }
  }

  async function handleRestore() {
    if (!restoreId.trim()) return;
    setBusy(true);
    setMessage(null);
    try {
      setWorkflow(await getInvestigation(restoreId.trim()));
      setIsFixture(false);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Unable to restore the investigation.");
    } finally {
      setBusy(false);
    }
  }

  async function handleReview(decision: "approve" | "reject") {
    if (!workflow || !reviewerId.trim()) {
      setMessage("Enter an attributable reviewer identifier before making a decision.");
      return;
    }
    setBusy(true);
    setMessage(null);
    if (isFixture) {
      setWorkflow({
        ...workflow,
        status: decision === "approve" ? "approved" : "rejected",
        release_authorized: decision === "approve",
        review_request: null,
        review_decision: {
          decision,
          reviewer_id: reviewerId.trim(),
          notes: reviewNotes.trim() || null,
          decided_at: new Date().toISOString(),
        },
      });
      setBusy(false);
      return;
    }
    try {
      setWorkflow(
        await reviewInvestigation(
          workflow.thread_id,
          decision,
          reviewerId.trim(),
          reviewNotes.trim(),
        ),
      );
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Unable to record the review.");
    } finally {
      setBusy(false);
    }
  }

  async function handleFeedback(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!workflow) return;
    const form = new FormData(event.currentTarget);
    const feedback: FeedbackRequest = {
      feedback_key: `web:${workflow.thread_id}`,
      rating: form.get("rating") === "not_helpful" ? "not_helpful" : "helpful",
      evidence_quality: Number(form.get("evidence_quality")),
      tags: [],
      comment: String(form.get("comment") ?? "").trim() || undefined,
    };
    setBusy(true);
    setMessage(null);
    try {
      if (!isFixture) await submitFeedback(workflow.thread_id, feedback);
      setFeedbackSent(true);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Unable to record feedback.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <a className="brand" href="#workspace" aria-label="FinSight AI home">
          <span className="brand-mark"><ShieldIcon /></span>
          <span><strong>FinSight</strong><small>Analyst intelligence</small></span>
        </a>
        <div className="topbar-meta">
          <span className="status-dot" />
          <span>Public SEC data</span>
          <span className="topbar-divider" />
          <span>Human approval required</span>
        </div>
      </header>

      <aside className="sidebar" aria-label="Workspace context">
        <div className="eyebrow">Investigation workspace</div>
        <nav className="nav-list" aria-label="Investigation stages">
          <a className="nav-item active" href="#investigate"><SearchIcon />Investigate</a>
          <a className="nav-item" href="#evidence"><FactIcon />Evidence</a>
          <a className="nav-item" href="#review"><ShieldIcon />Human review</a>
        </nav>
        <div className="sidebar-card">
          <span className="sidebar-label">Trust boundary</span>
          <strong>Decision support only</strong>
          <p>No trading, personalized recommendations, or autonomous financial action.</p>
        </div>
        <div className="sidebar-card subtle">
          <span className="sidebar-label">Restore a thread</span>
          <label className="sr-only" htmlFor="restore-thread">Workflow thread ID</label>
          <input
            id="restore-thread"
            onChange={(event) => setRestoreId(event.target.value)}
            placeholder="Workflow UUID"
            value={restoreId}
          />
          <button className="text-button" disabled={busy || !restoreId.trim()} onClick={handleRestore} type="button">
            Restore investigation →
          </button>
        </div>
      </aside>

      <main className="workspace" id="workspace">
        <section className="hero-row">
          <div>
            <span className="eyebrow">Evidence-first financial risk research</span>
            <h1>Investigate filings.<br /><span>Verify every claim.</span></h1>
            <p>Hybrid retrieval, exact SEC facts, deterministic checks, and an explicit human release gate in one analyst workflow.</p>
          </div>
          <button
            className="fixture-button"
            onClick={() => {
              setWorkflow(demoWorkflow);
              setIsFixture(true);
              setMessage(null);
              setFeedbackSent(false);
            }}
            type="button"
          >
            Explore interface fixture
            <small>No model or API call</small>
          </button>
        </section>

        {isFixture && (
          <div className="fixture-banner" role="status">
            <AlertIcon /> Illustrative interface fixture — not a live model result or performance claim.
          </div>
        )}
        {message && <div className="error-banner" role="alert">{message}</div>}

        <section className="investigation-grid" id="investigate">
          <form className="query-card" onSubmit={handleStart}>
            <div className="card-heading">
              <div><span className="step-number">01</span><h2>Frame the investigation</h2></div>
              <span className="bounded-pill">Bounded query</span>
            </div>

            <div className="mode-grid" role="tablist" aria-label="Investigation type">
              {MODES.map((item) => (
                <button
                  aria-selected={mode === item.key}
                  className={mode === item.key ? "mode-button selected" : "mode-button"}
                  key={item.key}
                  onClick={() => selectMode(item.key)}
                  role="tab"
                  type="button"
                >
                  {item.key === "question" ? <SearchIcon /> : item.key === "comparison" ? <CompareIcon /> : <FactIcon />}
                  <span><strong>{item.label}</strong><small>{item.description}</small></span>
                </button>
              ))}
            </div>

            <div className="field-grid">
              <label><span>Company CIK</span><input inputMode="numeric" maxLength={10} onChange={(e) => setCik(e.target.value)} pattern="[0-9]{1,10}" required value={cik} /></label>
              <label><span>SEC form</span><select onChange={(e) => setFormType(e.target.value)} value={formType}><option>10-K</option><option>10-Q</option><option>8-K</option></select></label>
              <label><span>Filed from</span><input onChange={(e) => setFiledFrom(e.target.value)} type="date" value={filedFrom} /></label>
              <label><span>Filed to</span><input onChange={(e) => setFiledTo(e.target.value)} type="date" value={filedTo} /></label>
            </div>

            {mode === "facts" && (
              <label className="full-field"><span>SEC fact concepts</span><input onChange={(e) => setConcepts(e.target.value)} value={concepts} /></label>
            )}

            <label className="full-field"><span>Investigation question</span><textarea maxLength={2000} onChange={(e) => setQuestion(e.target.value)} required rows={5} value={question} /></label>

            <div className="form-footer">
              <div><ShieldIcon /><span>Answers remain blocked until a reviewer approves release.</span></div>
              <button className="primary-button" disabled={busy} type="submit">{busy ? "Working…" : "Start investigation"}<span>→</span></button>
            </div>
          </form>

          <aside className="context-panel">
            <div className="context-label">Research contract</div>
            <div className="contract-item"><span>01</span><div><strong>Source boundary</strong><p>Public SEC filings and normalized company facts only.</p></div></div>
            <div className="contract-item"><span>02</span><div><strong>Verification</strong><p>Citations are validated and arithmetic is recomputed outside the model.</p></div></div>
            <div className="contract-item"><span>03</span><div><strong>Release control</strong><p>Consequential interpretation requires an attributable human decision.</p></div></div>
          </aside>
        </section>

        {workflow ? (
          <section className="result-section" id="evidence">
            <div className="result-header">
              <div><span className="step-number">02</span><h2>Evidence-backed report</h2></div>
              <div className={`workflow-status ${workflow.status}`}><span />{workflow.status.replace("_", " ")}</div>
            </div>

            <div className="metrics-row">
              <div><span>Evidence items</span><strong>{evidenceCount.toString().padStart(2, "0")}</strong></div>
              <div><span>Filing periods</span><strong>{filingPeriods.toString().padStart(2, "0")}</strong></div>
              <div><span>Model</span><strong className="model-value">{workflow.answer.model_name ?? "Not reported"}</strong></div>
              <div><span>Release</span><strong className="model-value">{workflow.release_authorized ? "Authorized" : "Blocked"}</strong></div>
            </div>

            <div className="report-grid">
              <article className="answer-card">
                <div className="answer-kicker">Analyst brief</div>
                <h3>{workflow.answer.question}</h3>
                <p className="answer-copy">{workflow.answer.answer}</p>
                <div className="claims-list">
                  {workflow.answer.claims.map((claim) => (
                    <div className="claim" key={claim.statement}>
                      <CheckIcon /><span>{claim.statement}</span><div>{claim.citation_ids.map((id) => <b key={id}>{id}</b>)}</div>
                    </div>
                  ))}
                </div>
              </article>

              <aside className="provenance-card">
                <span className="context-label">Provenance</span>
                <dl>
                  <div><dt>Source</dt><dd>SEC filing archive</dd></div>
                  <div><dt>Forms</dt><dd>{sourceForms}</dd></div>
                  <div><dt>Evidence period</dt><dd>{evidencePeriod}</dd></div>
                  <div><dt>Thread</dt><dd title={workflow.thread_id}>{workflow.thread_id.slice(0, 8)}…</dd></div>
                </dl>
                <p>Every source retains accession, filing date, section, and content identity.</p>
              </aside>
            </div>

            <div className="evidence-heading"><div><h3>Source evidence</h3><span>Open the original SEC record before approving.</span></div><span>{evidenceCount} attributable items</span></div>
            <div className="source-grid">
              {workflow.answer.sources.map((source) => {
                const sourceUrl = safeSecSourceUrl(source.source_url);
                return (
                <a className="source-card" href={sourceUrl ?? "#evidence"} key={source.source_id} rel={sourceUrl ? "noreferrer" : undefined} target={sourceUrl ? "_blank" : undefined}>
                  <div><b>{source.source_id}</b><span>{source.source_type === "financial_fact" ? "Structured fact" : "Filing passage"}</span><ExternalIcon /></div>
                  <h4>{source.label}</h4>
                  <p>{sourceMeta(source)}</p>
                  <small>Accession {source.accession_number}</small>
                </a>
                );
              })}
            </div>

            {workflow.answer.numerical_validations.length > 0 && (
              <div className="validation-panel">
                <div><span className="context-label">Deterministic checks</span><h3>Numerical verification</h3></div>
                {workflow.answer.numerical_validations.map((check) => (
                  <div className="validation-row" key={check.statement}>
                    <span className={check.passed ? "validation-icon pass" : "validation-icon fail"}>{check.passed ? <CheckIcon /> : <AlertIcon />}</span>
                    <div><strong>{check.statement}</strong><p>{check.message}</p></div>
                    <code>{check.reported_value} {check.reported_unit}</code>
                  </div>
                ))}
              </div>
            )}

            <div className="limitations-panel"><AlertIcon /><div><strong>Confidence and limitations</strong><p>This is evidence-constrained decision support, not a probability estimate. {workflow.answer.limitations.join(" ")}</p></div></div>

            <section className="review-panel" id="review">
              <div className="review-copy"><span className="step-number">03</span><div><h2>Human release gate</h2><p>Confirm that the cited evidence supports each material statement. Approval authorizes display; it does not convert the report into financial advice.</p></div></div>
              {workflow.status === "pending_review" ? (
                <div className="review-controls">
                  <label><span>Reviewer identifier</span><input onChange={(e) => setReviewerId(e.target.value)} placeholder="analyst@organization" value={reviewerId} /></label>
                  <label><span>Review notes</span><textarea onChange={(e) => setReviewNotes(e.target.value)} placeholder="Evidence checked against original filing…" rows={3} value={reviewNotes} /></label>
                  <div><button className="secondary-button reject" disabled={busy} onClick={() => handleReview("reject")} type="button">Reject report</button><button className="primary-button approve" disabled={busy} onClick={() => handleReview("approve")} type="button"><CheckIcon />Approve release</button></div>
                </div>
              ) : (
                <div className="decision-summary"><CheckIcon /><div><strong>Review recorded: {workflow.status}</strong><p>{workflow.review_decision?.reviewer_id} · {workflow.review_decision?.notes ?? "No notes supplied"}</p></div></div>
              )}
            </section>

            {workflow.status !== "pending_review" && (
              <form className="feedback-panel" onSubmit={handleFeedback}>
                <div><span className="context-label">Feedback loop</span><h3>Was this report useful?</h3><p>Capture product-quality feedback without storing personal data in the feedback record.</p></div>
                {feedbackSent ? <div className="feedback-success"><CheckIcon />Feedback recorded</div> : <div className="feedback-fields"><label><span>Assessment</span><select defaultValue="helpful" name="rating"><option value="helpful">Helpful</option><option value="not_helpful">Not helpful</option></select></label><label><span>Evidence quality</span><select defaultValue="4" name="evidence_quality"><option value="5">5 — Excellent</option><option value="4">4 — Strong</option><option value="3">3 — Adequate</option><option value="2">2 — Weak</option><option value="1">1 — Poor</option></select></label><label className="feedback-comment"><span>Optional comment</span><input maxLength={2000} name="comment" placeholder="What should improve?" /></label><button className="secondary-button" disabled={busy} type="submit">Submit feedback</button></div>}
              </form>
            )}
          </section>
        ) : (
          <section className="empty-state" id="evidence"><div className="empty-orbit"><ShieldIcon /></div><span className="eyebrow">Awaiting an investigation</span><h2>Evidence appears here—never hidden behind a score.</h2><p>Start a live workflow or explore the clearly labeled interface fixture.</p></section>
        )}
      </main>

      <footer className="footer"><span>FinSight AI · Independent engineering project</span><span>Public SEC data only · Not investment, legal, or accounting advice</span></footer>
    </div>
  );
}
