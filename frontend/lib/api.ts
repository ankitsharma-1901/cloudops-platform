export const environments = ["development", "staging"] as const;
export const regions = ["ca-central-1", "us-east-1"] as const;
export const instanceTypes = ["t3.micro", "t3.small"] as const;

export type ServerConfiguration = {
  name: string;
  environment: (typeof environments)[number];
  region: (typeof regions)[number];
  instance_type: (typeof instanceTypes)[number];
  install_nginx: boolean;
  install_docker: boolean;
};
export type SavedRequest = ServerConfiguration & {
  id: string;
  status: "queued";
  created_at: string;
};
export type ProvisioningJobStatus =
  | "queued"
  | "planning"
  | "plan_ready"
  | "failed";
export type PlanMode = "local" | "aws_development";
export type TerraformResourceChange = {
  address: string;
  actions: ("create" | "read" | "update" | "delete" | "no-op")[];
};
export type TerraformPlanSummary = {
  plan_mode: PlanMode;
  plan_revision: string | null;
  resource: string | null;
  resource_changes: TerraformResourceChange[];
  to_add: number;
  to_change: number;
  to_destroy: number;
};
export type ProvisioningJob = {
  id: string;
  server_request_id: string;
  plan_mode: PlanMode;
  status: ProvisioningJobStatus;
  plan_summary: TerraformPlanSummary | null;
  plan_revision: string | null;
  error_message: string | null;
};
export type ApprovalStatus = "pending" | "approved" | "rejected" | "expired";
export type ApprovalRequest = {
  id: string;
  server_request_id: string;
  provisioning_job_id: string;
  plan_revision: string;
  status: ApprovalStatus;
  requested_by: string;
  requested_at: string;
  expires_at: string;
  decided_by: string | null;
  decided_at: string | null;
  decision_reason: string | null;
};
export type ApprovalAuditEvent = {
  id: string;
  approval_request_id: string;
  event_type: "requested" | "approved" | "rejected" | "expired";
  actor_subject: string;
  occurred_at: string;
};
export type CurrentUser = {
  id: string;
  roles: string[];
  name?: string | null;
};
export type FieldErrors = Partial<Record<keyof ServerConfiguration, string>>;

const apiBaseUrl = "/api/cloudops";

export class ApiError extends Error {
  constructor(
    message: string,
    public fields: FieldErrors = {},
    public status?: number,
  ) {
    super(message);
  }
}

export async function createServer(
  configuration: ServerConfiguration,
  signal?: AbortSignal,
): Promise<SavedRequest> {
  let response: Response;
  try {
    response = await fetch(
      `${apiBaseUrl}/api/v1/resources`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(configuration),
        signal: signal || AbortSignal.timeout(15000),
      },
    );
  } catch {
    throw new ApiError(
      "Could not reach the local API. Check that the backend is running and try again.",
    );
  }
  const body = await response.json().catch(() => null);
  if (response.status === 409)
    throw new ApiError(
      "That server name already exists in this environment. Choose another name or environment.",
      { name: "This name is already used in the selected environment." },
    );
  if (response.status === 422) {
    const fields: FieldErrors = {};
    if (Array.isArray(body?.detail)) {
      for (const error of body.detail) {
        const field = error.loc?.[1] as keyof ServerConfiguration;
        if (field in configuration && typeof error.msg === "string")
          fields[field] = error.msg;
      }
    }
    throw new ApiError(
      "Please check your configuration and correct the highlighted fields.",
      fields,
    );
  }
  if (!response.ok || !body?.id || body?.status !== "queued")
    throw new ApiError(
      "The request could not be confirmed. Please try again. If it was saved, retrying may report a duplicate name.",
    );
  return body;
}

function isProvisioningJob(value: unknown): value is ProvisioningJob {
  if (typeof value !== "object" || value === null) return false;
  const job = value as Record<string, unknown>;
  const validStatuses: readonly string[] = [
    "queued",
    "planning",
    "plan_ready",
    "failed",
  ];
  const validModes: readonly string[] = ["local", "aws_development"];
  if (
    typeof job.id !== "string" ||
    typeof job.server_request_id !== "string" ||
    typeof job.plan_mode !== "string" ||
    !validModes.includes(job.plan_mode) ||
    typeof job.status !== "string" ||
    !validStatuses.includes(job.status) ||
    !(job.plan_revision === null || typeof job.plan_revision === "string") ||
    !(job.error_message === null || typeof job.error_message === "string")
  ) {
    return false;
  }
  if (job.plan_summary === null) return true;
  if (typeof job.plan_summary !== "object") return false;
  const summary = job.plan_summary as Record<string, unknown>;
  const changes = summary.resource_changes;
  const validActions: readonly string[] = [
    "create",
    "read",
    "update",
    "delete",
    "no-op",
  ];
  return (
    summary.plan_mode === job.plan_mode &&
    (summary.plan_revision === null ||
      (typeof summary.plan_revision === "string" && /^[0-9a-f]{64}$/.test(summary.plan_revision))) &&
    summary.plan_revision === job.plan_revision &&
    (summary.resource === null || typeof summary.resource === "string") &&
    Array.isArray(changes) &&
    changes.every((change) => {
      if (typeof change !== "object" || change === null) return false;
      const item = change as Record<string, unknown>;
      return (
        typeof item.address === "string" &&
        Array.isArray(item.actions) &&
        item.actions.every(
          (action) => typeof action === "string" && validActions.includes(action),
        )
      );
    }) &&
    Number.isInteger(summary.to_add) &&
    (summary.to_add as number) >= 0 &&
    Number.isInteger(summary.to_change) &&
    (summary.to_change as number) >= 0 &&
    Number.isInteger(summary.to_destroy) &&
    (summary.to_destroy as number) >= 0
  );
}

async function readJobResponse(response: Response): Promise<ProvisioningJob> {
  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const messages: Record<number, string> = {
      401: "Your session expired or the API token was rejected. Sign in again to check the plan.",
      403: "You do not have permission to view this plan job.",
      404: "This plan job no longer exists in the configured API database.",
      503: "The API authentication service is unavailable or not configured.",
    };
    throw new ApiError(messages[response.status] || "The plan status service is unavailable. Retrying shortly.", {}, response.status);
  }
  if (!isProvisioningJob(body)) {
    throw new ApiError("The API returned an invalid plan status response. Refresh after checking the API version.", {}, 422);
  }
  return body;
}

export async function enqueueTerraformPlan(
  resourceId: string,
  planMode: PlanMode = "local",
  signal?: AbortSignal,
): Promise<ProvisioningJob> {
  let response: Response;
  try {
    response = await fetch(
      `${apiBaseUrl}/api/v1/resources/${encodeURIComponent(resourceId)}/plan`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ plan_mode: planMode }),
        signal: signal || AbortSignal.timeout(15000),
      },
    );
  } catch {
    throw new ApiError(
      "Could not reach the local API to request a Terraform plan.",
    );
  }
  const body: unknown = await response.json().catch(() => null);
  if (response.status === 409) {
    const detail =
      typeof body === "object" && body !== null &&
      typeof (body as Record<string, unknown>).detail === "string"
        ? (body as Record<string, string>).detail
        : "The requested Terraform plan conflicts with an active job.";
    throw new ApiError(detail);
  }
  if (!response.ok || !isProvisioningJob(body)) {
    throw new ApiError("The Terraform planning job could not be confirmed.");
  }
  return body;
}

export async function getProvisioningJob(
  jobId: string,
  signal?: AbortSignal,
): Promise<ProvisioningJob> {
  let response: Response;
  try {
    response = await fetch(
      `${apiBaseUrl}/api/v1/jobs/${encodeURIComponent(jobId)}`,
      { cache: "no-store", signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(15000)]) : AbortSignal.timeout(15000) },
    );
  } catch {
    throw new ApiError("Could not reach the local API to check the plan status.");
  }
  return readJobResponse(response);
}

function approvalError(body: unknown, fallback: string): ApiError {
  const detail =
    typeof body === "object" && body !== null &&
    typeof (body as Record<string, unknown>).detail === "string"
      ? (body as Record<string, string>).detail
      : fallback;
  return new ApiError(detail);
}

export async function requestPlanApproval(
  jobId: string,
  signal?: AbortSignal,
): Promise<ApprovalRequest> {
  let response: Response;
  try {
    response = await fetch(
      `${apiBaseUrl}/api/v1/jobs/${encodeURIComponent(jobId)}/approvals`,
      { method: "POST", signal: signal || AbortSignal.timeout(15000) },
    );
  } catch {
    throw new ApiError("Could not reach the approval service.");
  }
  const body = await response.json().catch(() => null);
  if (!response.ok) throw approvalError(body, "Approval could not be requested.");
  return body as ApprovalRequest;
}

export async function getPlanApproval(jobId: string): Promise<ApprovalRequest> {
  const response = await fetch(
    `${apiBaseUrl}/api/v1/jobs/${encodeURIComponent(jobId)}/approval`,
    { signal: AbortSignal.timeout(15000) },
  );
  const body = await response.json().catch(() => null);
  if (!response.ok) throw approvalError(body, "Approval status could not be loaded.");
  return body as ApprovalRequest;
}

export async function getApprovalAudit(
  approvalId: string,
  signal?: AbortSignal,
): Promise<ApprovalAuditEvent[]> {
  const response = await fetch(
    `${apiBaseUrl}/api/v1/approvals/${encodeURIComponent(approvalId)}/audit`,
    { signal: signal || AbortSignal.timeout(15000) },
  );
  const body = await response.json().catch(() => null);
  if (!response.ok) throw approvalError(body, "Approval audit history could not be loaded.");
  return body as ApprovalAuditEvent[];
}

export async function listPendingApprovals(): Promise<ApprovalRequest[]> {
  const response = await fetch(`${apiBaseUrl}/api/v1/approvals?status=pending`, {
    signal: AbortSignal.timeout(15000),
  });
  const body = await response.json().catch(() => null);
  if (!response.ok) throw approvalError(body, "Approval queue could not be loaded.");
  return body as ApprovalRequest[];
}

export async function decideApproval(
  approvalId: string,
  decision: "approved" | "rejected",
): Promise<ApprovalRequest> {
  const response = await fetch(
    `${apiBaseUrl}/api/v1/approvals/${encodeURIComponent(approvalId)}/decision`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ decision }),
      signal: AbortSignal.timeout(15000),
    },
  );
  const body = await response.json().catch(() => null);
  if (!response.ok) throw approvalError(body, "Approval decision was rejected.");
  return body as ApprovalRequest;
}

export type RequestHistoryItem = {
  request: SavedRequest;
  latest_job: ProvisioningJob | null;
  approval: ApprovalRequest | null;
};
export type RequestHistoryPage = { items: RequestHistoryItem[]; has_more: boolean };
export type JobHistoryPage = {
  request: SavedRequest;
  items: { job: ProvisioningJob; approval: ApprovalRequest | null; is_current_plan: boolean; can_request_approval: boolean }[];
  has_more: boolean;
};

async function historyResponse<T>(path: string, signal?: AbortSignal): Promise<T> {
  try {
    const response = await fetch(`${apiBaseUrl}/api/v1/${path}`, {
      cache: "no-store",
      signal: signal || AbortSignal.timeout(15000),
    });
    const body = await response.json().catch(() => null);
    if (!response.ok) throw new ApiError(
      response.status === 401 ? "Your session expired. Sign in again." :
      response.status === 403 ? "You do not have permission to view this history." :
      "History could not be loaded. Please try again.",
    );
    if (body === null) throw new ApiError("History could not be loaded. Please try again.");
    return body;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    throw new ApiError("Could not reach the history service. Please try again.");
  }
}

export const historyPageSize = 20;
export function listRequestHistory(offset: number, signal?: AbortSignal) {
  return historyResponse<RequestHistoryPage>(
    `history/requests?limit=${historyPageSize}&offset=${offset}`, signal,
  );
}
export function getRequestHistory(id: string, offset: number, signal?: AbortSignal) {
  return historyResponse<JobHistoryPage>(
    `history/requests/${encodeURIComponent(id)}?limit=${historyPageSize}&offset=${offset}`, signal,
  );
}
export function listReviewHistory(mode: "pending" | "decided", offset: number, signal?: AbortSignal) {
  return historyResponse<ApprovalRequest[]>(
    `approvals?status=${mode}&mine=${mode === "decided"}&limit=${historyPageSize}&offset=${offset}`, signal,
  );
}
