import type {
  ApiErrorShape,
  FeedbackRequest,
  FeedbackResponse,
  InvestigationRequest,
  InvestigationWorkflow,
} from "@/lib/contracts";

export class FinSightApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "FinSightApiError";
  }
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api/finsight/${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...init?.headers,
    },
  });
  const payload = (await response.json()) as T | ApiErrorShape;
  if (!response.ok) {
    const error = payload as ApiErrorShape;
    throw new FinSightApiError(
      error.detail ?? error.error ?? "The FinSight API request failed.",
      response.status,
    );
  }
  return payload as T;
}

export function startInvestigation(
  request: InvestigationRequest,
): Promise<InvestigationWorkflow> {
  return requestJson<InvestigationWorkflow>("v1/investigations/runs", {
    method: "POST",
    body: JSON.stringify(request),
  });
}

export function getInvestigation(threadId: string): Promise<InvestigationWorkflow> {
  return requestJson<InvestigationWorkflow>(`v1/investigations/runs/${threadId}`);
}

export function reviewInvestigation(
  threadId: string,
  decision: "approve" | "reject",
  reviewerId: string,
  notes: string,
): Promise<InvestigationWorkflow> {
  return requestJson<InvestigationWorkflow>(
    `v1/investigations/runs/${threadId}/review`,
    {
      method: "POST",
      body: JSON.stringify({
        decision,
        reviewer_id: reviewerId,
        notes: notes || null,
      }),
    },
  );
}

export function submitFeedback(
  threadId: string,
  feedback: FeedbackRequest,
): Promise<FeedbackResponse> {
  return requestJson<FeedbackResponse>(
    `v1/investigations/runs/${threadId}/feedback`,
    {
      method: "POST",
      body: JSON.stringify(feedback),
    },
  );
}
