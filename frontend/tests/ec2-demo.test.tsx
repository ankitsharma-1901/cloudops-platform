import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import EC2Demo from "../components/ec2-demo";

it("keeps demo creation disabled until the exact confirmation is typed", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ enabled: true, instance: null }), { status: 200 })));
  render(<EC2Demo />);
  const button = await screen.findByRole("button", { name: "Create one demo instance" });
  expect(button).toBeDisabled();
  await userEvent.type(screen.getByLabelText(/Type/), "CREATE ONE EC2 DEMO INSTANCE");
  expect(button).toBeEnabled();
});

it("shows disabled mode without a mutation control", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ enabled: false, instance: null }), { status: 200 })));
  render(<EC2Demo />);
  expect(await screen.findByText(/disabled by the operator/)).toBeVisible();
  expect(screen.queryByRole("button", { name: /create one demo instance/i })).not.toBeInTheDocument();
});
