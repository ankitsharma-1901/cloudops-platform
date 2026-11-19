import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import DeploymentPanel from "../components/deployment-panel";
import type { Deployment, DeploymentView } from "../lib/api";

const eligible: DeploymentView = {
  enabled: true, eligible: true, reason: null, artifact_digest: "a".repeat(64), plan_revision: "b".repeat(64), deployment: null, events: [],
};
const deployment: Deployment = {
  id: "deploy-1", request_id: "request-1", job_id: "job-1", approval_id: "approval-1",
  artifact_digest: "a".repeat(64), plan_revision: "b".repeat(64), requester: "issuer|owner", approver: "issuer|reviewer", initiator: "issuer|owner",
  status: "succeeded", created_at: "2026-11-17T12:00:00Z", started_at: "2026-11-17T12:00:01Z", apply_started_at: "2026-11-17T12:00:02Z",
  finished_at: "2026-11-17T12:00:03Z", result: { instance_id: "i-1234567890abcdef0" }, message: null,
};
const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });

describe("explicit AWS deployment", () => {
  it("keeps deployment unavailable when the server switch is disabled", async () => {
    const fetch = vi.fn().mockResolvedValue(response({ ...eligible, enabled: false, eligible: false, reason: "AWS deployment is disabled by the server safety switch." }));
    vi.stubGlobal("fetch", fetch);
    render(<DeploymentPanel jobId="job-1" />);
    expect(await screen.findByText(/disabled by the server safety switch/)).toBeVisible();
    expect(screen.queryByRole("button", { name: "Deploy Approved Plan" })).not.toBeInTheDocument();
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it.each(["The saved plan has expired.", "Only create-only plans are deployable.", "Only the engineer who owns this request may initiate deployment.", "The saved plan has been consumed."])("shows eligibility reason: %s", async (reason) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({ ...eligible, eligible: false, reason })));
    render(<DeploymentPanel jobId="job-1" />);
    expect(await screen.findByText(`Deployment unavailable: ${reason}`)).toBeVisible();
    expect(screen.queryByRole("button", { name: "Deploy Approved Plan" })).not.toBeInTheDocument();
  });

  it("requires explicit acknowledgement of real resources and charges before queuing the exact digest", async () => {
    const fetch = vi.fn().mockResolvedValueOnce(response(eligible)).mockResolvedValueOnce(response(deployment, 202))
      .mockResolvedValueOnce(response({ ...eligible, eligible: false, reason: "Attempt already exists.", deployment, events: [] }));
    vi.stubGlobal("fetch", fetch);
    render(<DeploymentPanel jobId="job-1" />);
    await userEvent.click(await screen.findByRole("button", { name: "Deploy Approved Plan" }));
    expect(screen.getByRole("group", { name: "Confirm AWS deployment" })).toHaveTextContent("ca-central-1");
    const confirm = screen.getByRole("button", { name: "Confirm deployment" });
    expect(confirm).toBeDisabled();
    expect(fetch).toHaveBeenCalledTimes(1);
    await userEvent.click(screen.getByRole("checkbox", { name: /real AWS resources and may incur charges/ }));
    await userEvent.click(confirm);
    expect(await screen.findByText("Deployment: succeeded")).toBeVisible();
    expect(fetch).toHaveBeenNthCalledWith(2, "/api/cloudops/api/v1/jobs/job-1/deploy", expect.objectContaining({
      method: "POST", body: JSON.stringify({ confirmation: "DEPLOY APPROVED AWS PLAN", artifact_digest: "a".repeat(64), plan_revision: "b".repeat(64) }),
    }));
    expect(screen.getByText("instance_id: i-1234567890abcdef0")).toBeVisible();
    expect(screen.queryByRole("button", { name: "Deploy Approved Plan" })).not.toBeInTheDocument();
  });

  it("cancels confirmation without sending a deployment request", async () => {
    const fetch = vi.fn().mockResolvedValue(response(eligible)); vi.stubGlobal("fetch", fetch);
    render(<DeploymentPanel jobId="job-1" />);
    await userEvent.click(await screen.findByRole("button", { name: "Deploy Approved Plan" }));
    await userEvent.click(screen.getByRole("button", { name: "Cancel deployment" }));
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("shows a server-side denial if the switch changes after confirmation", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(response(eligible)).mockResolvedValueOnce(response({ detail: "AWS deployment is disabled by the server safety switch." }, 503)));
    render(<DeploymentPanel jobId="job-1" />);
    await userEvent.click(await screen.findByRole("button", { name: "Deploy Approved Plan" }));
    await userEvent.click(screen.getByRole("checkbox"));
    await userEvent.click(screen.getByRole("button", { name: "Confirm deployment" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("disabled by the server safety switch");
  });

  it("displays uncertain outcomes and sanitized audit history without retrying execution", async () => {
    const fetch = vi.fn().mockResolvedValue(response({ ...eligible, eligible: false, reason: "Operator reconciliation required.",
      deployment: { ...deployment, status: "needs_operator_review", result: {}, message: "Terraform may have changed AWS. Automatic retry is blocked." },
      events: [{ id: "event-1", event_type: "needs_operator_review", actor_subject: "worker", occurred_at: deployment.finished_at }],
    }));
    vi.stubGlobal("fetch", fetch);
    render(<DeploymentPanel jobId="job-1" />);
    expect(await screen.findByText("Deployment: needs_operator_review")).toBeVisible();
    expect(screen.getByText(/Automatic retry is blocked/)).toBeVisible();
    expect(screen.getByText(/needs_operator_review · worker/)).toBeVisible();
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("polls a queued deployment until the existing attempt succeeds", async () => {
    const fetch = vi.fn().mockResolvedValueOnce(response({ ...eligible, eligible: false, deployment: { ...deployment, status: "queued" } }))
      .mockResolvedValueOnce(response({ ...eligible, eligible: false, deployment }));
    vi.stubGlobal("fetch", fetch);
    render(<DeploymentPanel jobId="job-1" />);
    expect(await screen.findByText("Deployment: queued")).toBeVisible();
    expect(await screen.findByText("Deployment: succeeded", {}, { timeout: 4000 })).toBeVisible();
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
  });

  it("shows persisted safe phase and reason codes without an execution retry", async () => {
    const fetch = vi.fn().mockResolvedValue(response({ ...eligible, eligible: false,
      deployment: { ...deployment, status: "failed", phase: "backend_preflight", reason_code: "backend_preflight_denied", result: {} },
    }));
    vi.stubGlobal("fetch", fetch);
    render(<DeploymentPanel jobId="job-1" />);
    expect(await screen.findByText("Execution phase: backend_preflight")).toBeVisible();
    expect(screen.getByText("Operator reason: backend_preflight_denied")).toBeVisible();
    expect(screen.queryByRole("button", { name: "Deploy Approved Plan" })).not.toBeInTheDocument();
    expect(fetch).toHaveBeenCalledTimes(1);
  });
});
