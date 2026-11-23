import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import EC2Demo from "../components/ec2-demo";

it("keeps demo creation disabled until the exact confirmation is typed", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ enabled: true, instance: null }), { status: 200 })));
  render(<EC2Demo />);
  await userEvent.click(await screen.findByRole("button", { name: "Review EC2 configuration" }));
  expect(screen.getByText("Fixed legacy configuration")).toBeVisible();
  const button = await screen.findByRole("button", { name: "Create one demo instance" });
  expect(button).toBeDisabled();
  await userEvent.type(screen.getByLabelText(/Type/), "CREATE ONE EC2 DEMO INSTANCE");
  expect(button).toBeEnabled();
});

it("shows AWS instance details and requires exact termination confirmation", async () => {
  const demo = { enabled: true, instance: { id: "i-0123456789abcdef0", status: "created", message: null, region: "ca-central-1", aws_state: "running", name: "cloudops-ec2-demo", private_ip: "10.0.1.4", volumes: [{ device: "/dev/xvda", id: "vol-123" }] } };
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(demo), { status: 200 })));
  render(<EC2Demo />);
  expect(await screen.findByText(/AWS state: running/)).toBeVisible();
  expect(screen.getByText(/10.0.1.4/)).toBeVisible();
  expect(screen.getByText(/vol-123/)).toBeVisible();
  expect(screen.getByRole("button", { name: "Terminate this demo instance" })).toBeDisabled();
});

it("reviews the fixed configuration before creating and displays the returned instance", async () => {
  const empty = { enabled: true, instance: null };
  const created = { enabled: true, instance: { id: "i-0123456789abcdef0", status: "created", message: null, region: "ca-central-1", aws_state: "pending" } };
  const fetch = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify(empty), { status: 200 }))
    .mockResolvedValueOnce(new Response(JSON.stringify({ id: created.instance.id }), { status: 200 }))
    .mockResolvedValueOnce(new Response(JSON.stringify(created), { status: 200 }));
  vi.stubGlobal("fetch", fetch);
  const user = userEvent.setup();
  render(<EC2Demo />);
  await user.click(await screen.findByRole("button", { name: "Review EC2 configuration" }));
  await user.type(screen.getByLabelText(/Type/), "CREATE ONE EC2 DEMO INSTANCE");
  await user.click(screen.getByRole("button", { name: "Create one demo instance" }));
  expect(await screen.findByText(/AWS state: pending/)).toBeVisible();
  expect(fetch.mock.calls[1][0]).toBe("/api/cloudops/api/v1/ec2-demo/create");
  expect(JSON.parse(fetch.mock.calls[1][1].body)).toEqual({ confirmation: "CREATE ONE EC2 DEMO INSTANCE" });
});

it("submits exact termination confirmation and shows reconciliation state", async () => {
  const instance = { id: "i-0123456789abcdef0", status: "created", message: null, region: "ca-central-1", aws_state: "running" };
  const fetch = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ enabled: true, instance }), { status: 200 }))
    .mockResolvedValueOnce(new Response("{}", { status: 200 }))
    .mockResolvedValueOnce(new Response(JSON.stringify({ enabled: true, instance: { ...instance, status: "terminating", aws_state: "shutting-down" } }), { status: 200 }));
  vi.stubGlobal("fetch", fetch);
  const user = userEvent.setup();
  render(<EC2Demo />);
  await user.type(await screen.findByLabelText(/Type/), "TERMINATE i-0123456789abcdef0");
  await user.click(screen.getByRole("button", { name: "Terminate this demo instance" }));
  expect(await screen.findByText(/Waiting for AWS to confirm termination/)).toBeVisible();
  expect(JSON.parse(fetch.mock.calls[1][1].body)).toEqual({ confirmation: "TERMINATE i-0123456789abcdef0" });
  expect(screen.queryByRole("button", { name: "Terminate this demo instance" })).not.toBeInTheDocument();
});

it("shows disabled mode without a mutation control", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ enabled: false, instance: null }), { status: 200 })));
  render(<EC2Demo />);
  expect(await screen.findByText(/disabled by the operator/)).toBeVisible();
  expect(screen.queryByRole("button", { name: /create one demo instance/i })).not.toBeInTheDocument();
});

it("completes a verified lifecycle and displays it separately in history", async () => {
  const previous = { id: "i-0123456789abcdef0", status: "terminated", message: null, region: "ca-central-1" };
  const history = [{ id: previous.id, status: "completed", created_at: "2026-11-22T19:00:00Z", completed_at: "2026-11-22T20:00:00Z" }];
  const fetch = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ enabled: true, instance: previous, history: [] }), { status: 200 }))
    .mockResolvedValueOnce(new Response("{}", { status: 200 }))
    .mockResolvedValueOnce(new Response(JSON.stringify({ enabled: true, instance: null, history }), { status: 200 }));
  vi.stubGlobal("fetch", fetch);
  const user = userEvent.setup();
  render(<EC2Demo />);
  await user.click(await screen.findByRole("button", { name: "Verify and complete lifecycle" }));
  expect(await screen.findByText("Completed demo history")).toBeVisible();
  expect(screen.getByText(previous.id)).toBeVisible();
  expect(screen.getByRole("button", { name: "Review EC2 configuration" })).toBeVisible();
  expect(JSON.parse(fetch.mock.calls[1][1].body)).toEqual({ confirmation: `COMPLETE ${previous.id}` });
});

it("explains an expired session, hides stale termination controls and recovers on refresh", async () => {
  const demo = { enabled: true, instance: { id: "i-0123456789abcdef0", status: "created", message: null, region: "ca-central-1" } };
  const fetch = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify(demo), { status: 200 }))
    .mockResolvedValueOnce(new Response("{}", { status: 401 }))
    .mockResolvedValueOnce(new Response("{}", { status: 401 }))
    .mockResolvedValueOnce(new Response(JSON.stringify(demo), { status: 200 }));
  vi.stubGlobal("fetch", fetch);
  const user = userEvent.setup();
  render(<EC2Demo />);
  await user.type(await screen.findByLabelText(/Type/), "TERMINATE i-0123456789abcdef0");
  await user.click(screen.getByRole("button", { name: "Terminate this demo instance" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Sign in again");
  expect(screen.queryByRole("button", { name: "Terminate this demo instance" })).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Refresh EC2 demo" }));
  expect(await screen.findByText("Instance: i-0123456789abcdef0")).toBeVisible();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});
