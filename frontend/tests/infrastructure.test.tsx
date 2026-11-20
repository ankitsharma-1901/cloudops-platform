import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import Infrastructure from "../components/infrastructure";

it("shows AWS inventory and a partial deployment without destructive controls", async () => {
  vi.stubGlobal("fetch", vi.fn().mockImplementation(() => Promise.resolve(new Response(JSON.stringify({
    aws_status: "available", state_status: "available", instances: [{ id: "i-12345678", name: "dev",
      state: "running", region: "ca-central-1", instance_type: "t3.micro", subnet_id: "subnet-12345678",
      security_groups: ["sg-12345678"], ownership: "managed", stack: "aws_ec2_only", latest_deployment: null }],
    recent_deployments: [{ id: "deployment-1", status: "needs_operator_review", stack: "aws_ec2_only", created_at: "2026-11-19" }],
  }), { status: 200 }))));
  render(<Infrastructure />);
  expect(await screen.findByText(/Managed by EC2-only state/)).toBeVisible();
  expect(screen.getByRole("alert")).toHaveTextContent("operator review");
  expect(screen.queryByRole("button", { name: /destroy|delete|terminate/i })).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Refresh" }));
  expect(await screen.findByText("dev", { exact: false })).toBeVisible();
});

it("distinguishes unavailable AWS and unknown state", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
    aws_status: "unavailable", state_status: "unknown", instances: [], recent_deployments: [],
  }), { status: 200 })));
  render(<Infrastructure />);
  expect(await screen.findByRole("alert")).toHaveTextContent("AWS inventory is unavailable");
  expect(screen.getByText(/Ownership is unknown/)).toBeVisible();
});
