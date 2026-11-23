import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import EC2RequestList from "../components/ec2-request-list";

it("shows the exact configuration and submits an independent approval", async () => {
  const request = { id: "request-1", requester: "engineer", configuration: { instance_name: "web-demo", region: "ca-central-1" }, digest: "abc", status: "pending", execution_status: "not_started", expires_at: "later" };
  const fetch = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify([request]), { status: 200 }))
    .mockResolvedValueOnce(new Response("{}", { status: 200 }))
    .mockResolvedValueOnce(new Response(JSON.stringify([{ ...request, status: "approved" }]), { status: 200 }));
  vi.stubGlobal("fetch", fetch);
  render(<EC2RequestList mode="approver" currentSubject="approver" />);
  expect(await screen.findByText("web-demo")).toBeVisible();
  await userEvent.click(screen.getByRole("button", { name: "Approve" }));
  expect(await screen.findByText(/Status: approved/)).toBeVisible();
  expect(JSON.parse(fetch.mock.calls[1][1].body)).toEqual({ decision: "approved" });
});

it("does not show a self-approval button", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify([{ id: "one", requester: "same", configuration: {}, digest: "abc", status: "pending", execution_status: "not_started", expires_at: "later" }]), { status: 200 })));
  render(<EC2RequestList mode="approver" currentSubject="same" />);
  expect(await screen.findByText("Self-approval is prohibited.")).toBeVisible();
  expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
});
