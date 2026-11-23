import { render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import EC2Activity from "../components/ec2-activity";

it("shows names for active and completed demo records", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
    instance: { id: "i-current", name: "web-demo", status: "created" },
    history: [{ id: "i-old", name: "previous-demo", status: "completed", completed_at: "2026-11-22" }],
  }), { status: 200 })));
  render(<EC2Activity />);
  expect(await screen.findByText(/Current: web-demo/)).toBeVisible();
  expect(screen.getByText(/previous-demo/)).toBeVisible();
});
