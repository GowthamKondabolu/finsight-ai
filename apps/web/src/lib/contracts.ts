export type WorkflowStatus = "pending_review" | "approved" | "rejected";

export interface AnswerClaim {
  statement: string;
  citation_ids: string[];
}

export interface NumericalValidation {
  statement: string;
  operation: string;
  fact_ids: string[];
  reported_value: string;
  expected_value: string | null;
  reported_unit: string;
  expected_unit: string | null;
  passed: boolean;
  message: string;
}

export interface AnswerSource {
  source_id: string;
  source_type: "filing_passage" | "financial_fact";
  label: string;
  source_url: string;
  accession_number: string;
  form_type: string;
  filing_date: string | null;
  section_name: string | null;
  chunk_index: number | null;
  content_hash: string | null;
  fact_concept: string | null;
  fact_value: string | null;
  fact_unit: string | null;
  fact_end_date: string | null;
}

export interface InvestigationAnswer {
  question: string;
  status: "grounded" | "insufficient_evidence" | "needs_review";
  answer: string;
  claims: AnswerClaim[];
  numerical_validations: NumericalValidation[];
  sources: AnswerSource[];
  limitations: string[];
  model_name: string | null;
  requires_human_review: boolean;
  review_reasons: string[];
}

export interface ReviewDecision {
  decision: "approve" | "reject";
  reviewer_id: string;
  notes: string | null;
  decided_at: string;
}

export interface InvestigationWorkflow {
  thread_id: string;
  status: WorkflowStatus;
  release_authorized: boolean;
  answer: InvestigationAnswer;
  review_request: {
    question: string;
    answer_status: InvestigationAnswer["status"];
    answer: string;
    source_ids: string[];
    limitations: string[];
    review_reasons: string[];
    proposed_action: "release_answer";
  } | null;
  review_decision: ReviewDecision | null;
}

export interface InvestigationRequest {
  thread_id: string;
  question: string;
  cik?: string;
  form_types: string[];
  filed_from?: string;
  filed_to?: string;
  section_names: string[];
  fact_concepts: string[];
  top_k: number;
  candidate_k: number;
  fact_limit: number;
}

export interface FeedbackRequest {
  feedback_key: string;
  rating: "helpful" | "not_helpful";
  evidence_quality: number;
  tags: Array<
    "citation_gap" | "numerical_issue" | "missing_context" | "clear_and_complete"
  >;
  comment?: string;
}

export interface FeedbackResponse extends FeedbackRequest {
  feedback_id: string;
  thread_id: string;
  recorded_at: string;
  duplicate: boolean;
}

export interface ApiErrorShape {
  detail?: string;
  error?: string;
}
