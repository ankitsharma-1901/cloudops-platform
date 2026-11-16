import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import AuthControls from "../components/auth-controls";
import ApprovalQueue from "../components/approval-queue";
import { signIn, signOut } from "next-auth/react";

vi.mock("next-auth/react", () => ({
  signIn: vi.fn(),
  signOut: vi.fn(),
}));

describe("authenticated role controls", () => {
  it("starts the OIDC login flow without collecting credentials", async () => {
    render(<AuthControls configured user={null} />);
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(signIn).toHaveBeenCalledWith("keycloak", { redirectTo: "/" });
  });

  it("shows server-provided roles and supports logout", async () => {
    render(
      <AuthControls
        configured
        user={{ id: "issuer|subject", name: "Approver", roles: ["approver"] }}
      />,
    );
    expect(screen.getByText("approver")).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Sign out" }));
    expect(signOut).toHaveBeenCalledWith({ redirectTo: "/" });
  });

  it("loads the approver queue and records a decision through the BFF", async () => {
    const approval = {
      id: "approval-1",
      server_request_id: "resource-1",
      provisioning_job_id: "job-1",
      plan_revision: "a".repeat(64),
      status: "pending",
      requested_by: "issuer|engineer",
      requested_at: "2026-11-15T12:00:00Z",
      expires_at: "2026-11-15T12:30:00Z",
      decided_by: null,
      decided_at: null,
      decision_reason: null,
    };
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(new Response(JSON.stringify([approval]), { status: 200 }))
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ ...approval, status: "approved" }), { status: 200 }),
      );
    vi.stubGlobal("fetch", fetch);
    render(<ApprovalQueue currentSubject="issuer|approver" />);
    expect(await screen.findByText("issuer|engineer")).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Approve" }));
    await waitFor(() =>
      expect(screen.queryByText("issuer|engineer")).not.toBeInTheDocument(),
    );
    expect(fetch).toHaveBeenNthCalledWith(
      2,
      "/api/cloudops/api/v1/approvals/approval-1/decision",
      expect.objectContaining({ body: JSON.stringify({ decision: "approved" }) }),
    );
  });
});
