import type { InvestigationWorkflow } from "@/lib/contracts";

export const DEMO_THREAD_ID = "11111111-1111-4111-8111-111111111111";

export const demoWorkflow: InvestigationWorkflow = {
  thread_id: DEMO_THREAD_ID,
  status: "pending_review",
  release_authorized: false,
  answer: {
    question: "What material risk disclosures changed in the latest annual filing?",
    status: "grounded",
    answer:
      "The latest filing places greater emphasis on supplier concentration, geopolitical disruption, and the operational impact of product-component availability. The evidence supports a change in emphasis, not a prediction of financial loss. [E1] [E2]",
    claims: [
      {
        statement: "Supplier concentration receives greater emphasis in the latest filing.",
        citation_ids: ["E1"],
      },
      {
        statement: "The filing links component availability to operational disruption.",
        citation_ids: ["E2"],
      },
    ],
    numerical_validations: [
      {
        statement: "Reported year-over-year revenue change is arithmetically consistent.",
        operation: "percentage_change",
        fact_ids: ["F1", "F2"],
        reported_value: "2.0",
        expected_value: "2.0",
        reported_unit: "percent",
        expected_unit: "percent",
        passed: true,
        message: "The reported value matches deterministic recomputation.",
      },
    ],
    sources: [
      {
        source_id: "E1",
        source_type: "filing_passage",
        label: "Apple Inc. 2025 Form 10-K — Risk Factors",
        source_url: "https://www.sec.gov/Archives/edgar/data/320193/",
        accession_number: "0000320193-25-000079",
        form_type: "10-K",
        filing_date: "2025-10-31",
        section_name: "Risk Factors",
        chunk_index: 14,
        content_hash: "4f7f92b82c7ed6f69f6f94eb85f11bd78e5b3ad0a9c76de981278ab55a980006",
        fact_concept: null,
        fact_value: null,
        fact_unit: null,
        fact_end_date: null,
      },
      {
        source_id: "E2",
        source_type: "filing_passage",
        label: "Apple Inc. 2024 Form 10-K — Business Risks",
        source_url: "https://www.sec.gov/Archives/edgar/data/320193/",
        accession_number: "0000320193-24-000123",
        form_type: "10-K",
        filing_date: "2024-11-01",
        section_name: "Risk Factors",
        chunk_index: 11,
        content_hash: "7abed651e051117eea13b28dd8c689131d92bf1c99c26aaedf470f2a9a30fcde",
        fact_concept: null,
        fact_value: null,
        fact_unit: null,
        fact_end_date: null,
      },
      {
        source_id: "F1",
        source_type: "financial_fact",
        label: "Revenue — FY2025",
        source_url: "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json",
        accession_number: "0000320193-25-000079",
        form_type: "10-K",
        filing_date: "2025-10-31",
        section_name: null,
        chunk_index: null,
        content_hash: null,
        fact_concept: "RevenueFromContractWithCustomerExcludingAssessedTax",
        fact_value: "416161000000",
        fact_unit: "USD",
        fact_end_date: "2025-09-27",
      },
    ],
    limitations: [
      "The comparison reflects retrieved filing passages and may omit disclosures outside the selected forms and periods.",
      "A change in disclosure emphasis is not evidence that a risk will materialize.",
    ],
    model_name: "interface-fixture/no-model-call",
    requires_human_review: true,
    review_reasons: [
      "Consequential financial interpretation requires qualified human approval.",
    ],
  },
  review_request: {
    question: "What material risk disclosures changed in the latest annual filing?",
    answer_status: "grounded",
    answer:
      "The latest filing places greater emphasis on supplier concentration and disruption.",
    source_ids: ["E1", "E2", "F1"],
    limitations: ["This interface fixture is not a live model result."],
    review_reasons: ["Qualified human approval is required."],
    proposed_action: "release_answer",
  },
  review_decision: null,
};
