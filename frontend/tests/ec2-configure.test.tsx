import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import EC2Configure from "../components/ec2-configure";

const defaults = { instance_name: "cloudops-ec2-demo", region: "ca-central-1", operating_system: "amazon-linux-2023", ami: "ami-009d185a5ea80b390", instance_type: "t3.micro", storage_gib: 8, storage_type: "gp3", encrypted: true, vpc: "vpc-approved", subnet: "subnet-approved", security_group: "sg-approved" };

it("loads the catalog and submits the reviewed configuration for approval", async () => {
  const fetch = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ defaults, security: {} }), { status: 200 }))
    .mockResolvedValueOnce(new Response(JSON.stringify({ review_id: "review-1", configuration: { ...defaults, instance_name: "web-demo" } }), { status: 200 }))
    .mockResolvedValueOnce(new Response("{}", { status: 200 }));
  vi.stubGlobal("fetch", fetch);
  const user = userEvent.setup();
  render(<EC2Configure />);
  const input = await screen.findByLabelText("Instance name");
  await user.clear(input); await user.type(input, "web-demo");
  expect(screen.getByLabelText("Region")).toBeDisabled();
  await user.click(screen.getByRole("button", { name: "Review configuration" }));
  expect(await screen.findByText(/Name: web-demo/)).toBeVisible();
  const create = screen.getByRole("button", { name: "Submit approval request" });
  expect(create).toBeDisabled();
  await user.type(screen.getByLabelText(/Type REQUEST EC2/), "REQUEST EC2 APPROVAL");
  await user.click(create);
  expect(JSON.parse(fetch.mock.calls[2][1].body)).toEqual({ review_id: "review-1" });
});

it("invalidates a review when the name changes", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ defaults, security: {} }), { status: 200 }))
    .mockResolvedValueOnce(new Response(JSON.stringify({ review_id: "review-1", configuration: defaults }), { status: 200 })));
  const user = userEvent.setup();
  render(<EC2Configure />);
  const input = await screen.findByLabelText("Instance name");
  await user.click(screen.getByRole("button", { name: "Review configuration" }));
  expect(await screen.findByRole("button", { name: "Submit approval request" })).toBeVisible();
  await user.type(input, "x");
  expect(screen.queryByRole("button", { name: "Submit approval request" })).not.toBeInTheDocument();
});

it("does not display an older review when the name changes while review is in flight", async () => {
  let resolveReview!: (response: Response) => void;
  const pending = new Promise<Response>((resolve) => { resolveReview = resolve; });
  vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ defaults, security: {} }), { status: 200 })).mockReturnValueOnce(pending));
  const user = userEvent.setup();
  render(<EC2Configure />);
  const input = await screen.findByLabelText("Instance name");
  await user.click(screen.getByRole("button", { name: "Review configuration" }));
  await user.type(input, "x");
  resolveReview(new Response(JSON.stringify({ review_id: "old", configuration: defaults }), { status: 200 }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Review configuration" })).toBeEnabled());
  expect(screen.queryByRole("button", { name: "Submit approval request" })).not.toBeInTheDocument();
});
