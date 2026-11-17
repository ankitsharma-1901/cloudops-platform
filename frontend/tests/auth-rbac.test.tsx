import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import AuthControls from "../components/auth-controls";
import ApprovalQueue from "../components/approval-queue";
import { signIn } from "next-auth/react";
import { logout } from "../app/actions/logout";

vi.mock("../app/actions/logout", () => ({ logout: vi.fn() }));

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
    expect(logout).toHaveBeenCalled();
  });

  it("loads the approver queue with a link to inspect the plan before deciding", async () => {
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
    expect(screen.getByRole("link", { name: "View request and plan history" })).toHaveAttribute("href", "/requests/resource-1");
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
  });
});
