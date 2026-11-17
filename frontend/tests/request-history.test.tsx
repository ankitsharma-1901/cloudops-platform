import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import RequestHistory, { RequestHistoryDetail } from "../components/request-history";
import ApprovalQueue from "../components/approval-queue";
import type { ApprovalRequest, ProvisioningJob, SavedRequest } from "../lib/api";

const user = { id: "issuer|engineer", roles: ["engineer"] };
const request: SavedRequest = {
  id: "request-1", name: "persisted-server", environment: "development", region: "ca-central-1",
  instance_type: "t3.micro", install_nginx: true, install_docker: true,
  status: "queued", created_at: "2026-11-16T12:00:00Z",
};
const job: ProvisioningJob = {
  id: "job-1", server_request_id: request.id, plan_mode: "local", status: "plan_ready",
  plan_revision: "a".repeat(64), error_message: null,
  plan_summary: { plan_mode: "local", plan_revision: "a".repeat(64), resource: "terraform_data.server",
    resource_changes: [{ address: "terraform_data.server", actions: ["create"] }], to_add: 1, to_change: 0, to_destroy: 0 },
};
const approval: ApprovalRequest = {
  id: "approval-1", server_request_id: request.id, provisioning_job_id: job.id,
  plan_revision: "a".repeat(64), status: "approved", requested_by: user.id,
  requested_at: "2026-11-16T12:00:00Z", expires_at: "2026-11-16T12:30:00Z",
  decided_by: "issuer|approver", decided_at: "2026-11-16T12:05:00Z", decision_reason: null,
};
const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
const page = { items: [{ request, latest_job: job, approval }], has_more: false };
const detail = { request, items: [{ job, approval, is_current_plan: true, can_request_approval: true }], has_more: false };

describe("persistent request history", () => {
  it("loads persisted requests, opens sanitized plans and approval audit events", async () => {
    const fetch = vi.fn()
      .mockResolvedValueOnce(response(page))
      .mockResolvedValueOnce(response(detail))
      .mockResolvedValueOnce(response([{ id: "audit-1", approval_request_id: approval.id,
        event_type: "approved", actor_subject: "issuer|approver", occurred_at: approval.decided_at }]));
    vi.stubGlobal("fetch", fetch);
    const view = render(<RequestHistory currentUser={user} />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading requests");
    expect(await screen.findByRole("link", { name: request.name })).toHaveAttribute("href", "/requests/request-1");
    view.unmount();
    render(<RequestHistoryDetail requestId={request.id} />);
    const details = screen.getByRole("region", { name: "Request details" });
    expect(await within(details).findByText("terraform_data.server: create")).toBeVisible();
    expect(await within(details).findByText(/approved · issuer\|approver/)).toBeVisible();
    expect(within(details).getByText("Approval: approved")).toBeVisible();
    expect(fetch).toHaveBeenNthCalledWith(2, expect.stringContaining("history/requests/request-1?limit=20&offset=0"), expect.objectContaining({ cache: "no-store" }));
  });

  it("shows empty history and refreshes without creating or deleting requests", async () => {
    const fetch = vi.fn().mockResolvedValueOnce(response({ items: [], has_more: false })).mockResolvedValueOnce(response(page));
    vi.stubGlobal("fetch", fetch);
    render(<RequestHistory currentUser={user} />);
    expect(await screen.findByText("No saved requests on this page.")).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Refresh history" }));
    expect(await screen.findByRole("link", { name: request.name })).toBeVisible();
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it("shows session errors and retries through the refresh control", async () => {
    const fetch = vi.fn().mockResolvedValueOnce(response({}, 401)).mockResolvedValueOnce(response(page));
    vi.stubGlobal("fetch", fetch);
    render(<RequestHistory currentUser={user} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Your session expired. Sign in again.");
    await userEvent.click(screen.getByRole("button", { name: "Refresh history" }));
    expect(await screen.findByRole("link", { name: request.name })).toBeVisible();
  });

  it("uses bounded next and previous pages", async () => {
    const fetch = vi.fn().mockResolvedValueOnce(response({ ...page, has_more: true }))
      .mockResolvedValueOnce(response({ items: [], has_more: false })).mockResolvedValueOnce(response(page));
    vi.stubGlobal("fetch", fetch);
    render(<RequestHistory currentUser={user} />);
    await screen.findByRole("link", { name: request.name });
    await userEvent.click(screen.getByRole("button", { name: "Next page" }));
    expect(await screen.findByText("No saved requests on this page.")).toBeVisible();
    expect(fetch).toHaveBeenNthCalledWith(2, expect.stringContaining("offset=20"), expect.anything());
    await userEvent.click(screen.getByRole("button", { name: "Previous page" }));
    expect(await screen.findByRole("link", { name: request.name })).toBeVisible();
  });

  it("ignores a stale response after switching request details", async () => {
    let resolveOld: (value: Response) => void = () => {};
    const fetch = vi.fn().mockImplementationOnce(() => new Promise<Response>((resolve) => { resolveOld = resolve; }))
      .mockResolvedValueOnce(response({ request: { ...request, id: "request-2", name: "new-selection" }, items: [], has_more: false }));
    vi.stubGlobal("fetch", fetch);
    const { rerender } = render(<RequestHistoryDetail requestId="request-1" />);
    rerender(<RequestHistoryDetail requestId="request-2" />);
    expect(await screen.findByText("new-selection")).toBeVisible();
    resolveOld(response(detail));
    await waitFor(() => expect(screen.queryByText(request.name)).not.toBeInTheDocument());
  });

  it("shows permission and network failures without displaying plan data", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(response({}, 403)).mockRejectedValueOnce(new Error("network")));
    render(<RequestHistoryDetail requestId="forbidden" />);
    expect(await screen.findByRole("alert")).toHaveTextContent("You do not have permission");
    await userEvent.click(screen.getByRole("button", { name: "Refresh details" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not reach the history service");
    expect(screen.queryByText("terraform_data.server: create")).not.toBeInTheDocument();
  });

  it("allows an engineer to request approval of a saved completed plan", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(response({ ...detail, items: [{ job, approval: null, is_current_plan: true, can_request_approval: true }] }))
      .mockResolvedValueOnce(response(approval, 201)).mockResolvedValueOnce(response(detail)).mockResolvedValueOnce(response([])));
    render(<RequestHistoryDetail requestId={request.id} canEngineer />);
    await userEvent.click(await screen.findByRole("button", { name: "Request approval" }));
    expect(await screen.findByText("Approval: approved")).toBeVisible();
  });

  it("shows the approver's decision history without decision controls", async () => {
    const fetch = vi.fn().mockResolvedValueOnce(response([])).mockResolvedValueOnce(response([approval]));
    vi.stubGlobal("fetch", fetch);
    render(<ApprovalQueue currentSubject="issuer|approver" />);
    await screen.findByText("No pending approvals.");
    await userEvent.click(screen.getByRole("button", { name: "My decisions" }));
    expect(await screen.findByText(/Approval: approved/)).toBeVisible();
    expect(fetch).toHaveBeenNthCalledWith(2, expect.stringContaining("status=decided&mine=true&limit=20&offset=0"), expect.anything());
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "View request and plan history" })).toHaveAttribute("href", "/requests/request-1");
  });

  it("identifies self approvals in the queue and requires plan inspection", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response([{ ...approval, status: "pending", requested_by: "issuer|approver" }])));
    render(<ApprovalQueue currentSubject="issuer|approver" />);
    expect(await screen.findByText("Self-approval is prohibited.")).toBeVisible();
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
  });

  it("does not offer approval for a superseded, legacy or non-owned plan", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({ ...detail, items: [{ job, approval: null, is_current_plan: false, can_request_approval: false }] })));
    render(<RequestHistoryDetail requestId={request.id} canEngineer />);
    expect(await screen.findByText("Historical or incomplete plan; not eligible for a new approval.")).toBeVisible();
    expect(screen.queryByRole("button", { name: "Request approval" })).not.toBeInTheDocument();
  });

  it("records a reviewed decision through the existing API and reloads its history", async () => {
    const fetch = vi.fn().mockResolvedValueOnce(response({ ...detail, items: [{ job, approval: { ...approval, status: "pending" }, is_current_plan: true, can_request_approval: false }] }))
      .mockResolvedValueOnce(response([])).mockResolvedValueOnce(response(approval)).mockResolvedValueOnce(response(detail)).mockResolvedValueOnce(response([]));
    vi.stubGlobal("fetch", fetch);
    render(<RequestHistoryDetail requestId={request.id} reviewerSubject="issuer|approver" />);
    await userEvent.click(await screen.findByRole("button", { name: "Approve reviewed plan" }));
    expect(await screen.findByText("Approval: approved")).toBeVisible();
    expect(fetch).toHaveBeenCalledWith("/api/cloudops/api/v1/approvals/approval-1/decision", expect.objectContaining({ method: "POST", body: JSON.stringify({ decision: "approved" }) }));
    expect(screen.queryByRole("button", { name: "Approve reviewed plan" })).not.toBeInTheDocument();
  });

  it("disables self decisions in the detail view", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(response({ ...detail, items: [{ job, approval: { ...approval, status: "pending" }, is_current_plan: true, can_request_approval: true }] })).mockResolvedValueOnce(response([])));
    render(<RequestHistoryDetail requestId={request.id} reviewerSubject={user.id} />);
    expect(await screen.findByRole("button", { name: "Approve reviewed plan" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Reject reviewed plan" })).toBeDisabled();
  });
});
