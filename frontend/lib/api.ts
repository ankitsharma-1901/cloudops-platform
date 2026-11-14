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
export type TerraformPlanSummary = {
  resource: "terraform_data.server_configuration";
  to_add: number;
  to_change: number;
  to_destroy: number;
};
export type ProvisioningJob = {
  id: string;
  server_request_id: string;
  status: ProvisioningJobStatus;
  plan_summary: TerraformPlanSummary | null;
  error_message: string | null;
};
export type FieldErrors = Partial<Record<keyof ServerConfiguration, string>>;

const apiBaseUrl = (process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000").replace(
  /\/$/,
  "",
);

export class ApiError extends Error {
  constructor(
    message: string,
    public fields: FieldErrors = {},
  ) {
    super(message);
  }
}

export async function createServer(
  configuration: ServerConfiguration,
): Promise<SavedRequest> {
  let response: Response;
  try {
    response = await fetch(
      `${apiBaseUrl}/api/v1/resources`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(configuration),
        signal: AbortSignal.timeout(15000),
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
  if (
    typeof job.id !== "string" ||
    typeof job.server_request_id !== "string" ||
    typeof job.status !== "string" ||
    !validStatuses.includes(job.status) ||
    !(job.error_message === null || typeof job.error_message === "string")
  ) {
    return false;
  }
  if (job.plan_summary === null) return true;
  if (typeof job.plan_summary !== "object") return false;
  const summary = job.plan_summary as Record<string, unknown>;
  return (
    summary.resource === "terraform_data.server_configuration" &&
    Number.isInteger(summary.to_add) &&
    Number.isInteger(summary.to_change) &&
    Number.isInteger(summary.to_destroy)
  );
}

async function readJobResponse(response: Response): Promise<ProvisioningJob> {
  const body: unknown = await response.json().catch(() => null);
  if (!response.ok || !isProvisioningJob(body)) {
    throw new ApiError("The Terraform planning job could not be confirmed.");
  }
  return body;
}

export async function enqueueTerraformPlan(
  resourceId: string,
): Promise<ProvisioningJob> {
  let response: Response;
  try {
    response = await fetch(
      `${apiBaseUrl}/api/v1/resources/${encodeURIComponent(resourceId)}/plan`,
      {
        method: "POST",
        signal: AbortSignal.timeout(15000),
      },
    );
  } catch {
    throw new ApiError(
      "Could not reach the local API to request a Terraform plan.",
    );
  }
  return readJobResponse(response);
}

export async function getProvisioningJob(
  jobId: string,
): Promise<ProvisioningJob> {
  let response: Response;
  try {
    response = await fetch(
      `${apiBaseUrl}/api/v1/jobs/${encodeURIComponent(jobId)}`,
      { signal: AbortSignal.timeout(15000) },
    );
  } catch {
    throw new ApiError("Could not reach the local API to check the plan status.");
  }
  return readJobResponse(response);
}
