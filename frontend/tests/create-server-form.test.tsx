import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import CreateServerForm from "../components/create-server-form";

const saved = {
  id: "a60332dc-a9ca-4bae-9711-97500a9da3a5",
  status: "queued",
  created_at: "2026-11-12T00:00:00Z",
};
const queuedPlanJob = {
  id: "d3fcf4f4-fbf5-4db4-b3bc-bae704c188f5",
  server_request_id: saved.id,
  status: "queued",
  plan_summary: null,
  error_message: null,
};
const readyPlanJob = {
  ...queuedPlanJob,
  status: "plan_ready",
  plan_summary: {
    resource: "terraform_data.server_configuration",
    to_add: 1,
    to_change: 0,
    to_destroy: 0,
  },
};
function mockResponse(status: number, body: unknown) {
  return vi
    .fn()
    .mockResolvedValue(new Response(JSON.stringify(body), { status }));
}
async function fillAndSubmit() {
  const user = userEvent.setup();
  await user.type(screen.getByLabelText(/Server name/), "web-dev-01");
  await user.click(
    screen.getByRole("button", { name: /Create server request/ }),
  );
  return user;
}

describe("Create server form", () => {
  it("offers exactly the backend-approved choices", () => {
    render(<CreateServerForm />);
    for (const [label, expected] of [
      ["Environment", ["development", "staging"]],
      ["AWS region", ["ca-central-1", "us-east-1"]],
      ["Instance type", ["t3.micro", "t3.small"]],
    ] as const) {
      const select = screen.getByLabelText(label) as HTMLSelectElement;
      expect(Array.from(select.options).map((option) => option.value)).toEqual(
        expected,
      );
    }
  });

  it("submits six fields, honours selections and shows the returned UUID and queued status", async () => {
    const fetch = mockResponse(202, saved);
    vi.stubGlobal("fetch", fetch);
    render(<CreateServerForm />);
    const user = userEvent.setup();
    await user.selectOptions(screen.getByLabelText("Environment"), "staging");
    await user.selectOptions(screen.getByLabelText("AWS region"), "us-east-1");
    await user.selectOptions(
      screen.getByLabelText("Instance type"),
      "t3.small",
    );
    await user.click(screen.getByLabelText(/Install Docker/));
    await fillAndSubmit();
    expect(await screen.findByText(saved.id)).toBeVisible();
    expect(screen.getByRole("status")).toHaveTextContent("queued");
    expect(fetch).toHaveBeenCalledOnce();
    const [url, options] = fetch.mock.calls[0];
    expect(url).toBe("http://127.0.0.1:8000/api/v1/resources");
    expect(options.method).toBe("POST");
    expect(JSON.parse(options.body)).toEqual({
      name: "web-dev-01",
      environment: "staging",
      region: "us-east-1",
      instance_type: "t3.small",
      install_nginx: true,
      install_docker: false,
    });
  });

  it.each(["", "ab", "Web", "1web", "web-"])(
    "rejects invalid name %s before fetching",
    async (name) => {
      const fetch = vi.fn();
      vi.stubGlobal("fetch", fetch);
      render(<CreateServerForm />);
      const user = userEvent.setup();
      if (name) await user.type(screen.getByLabelText(/Server name/), name);
      await user.click(
        screen.getByRole("button", { name: /Create server request/ }),
      );
      expect(screen.getByLabelText(/Server name/)).toHaveAttribute(
        "aria-invalid",
        "true",
      );
      expect(fetch).not.toHaveBeenCalled();
    },
  );

  it("shows duplicate-name conflicts and retains the entered values", async () => {
    vi.stubGlobal("fetch", mockResponse(409, { detail: "Duplicate" }));
    render(<CreateServerForm />);
    await fillAndSubmit();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "already exists",
    );
    expect(screen.getByLabelText(/Server name/)).toHaveValue("web-dev-01");
    expect(screen.getByLabelText(/Server name/)).toHaveAttribute(
      "aria-invalid",
      "true",
    );
  });

  it("shows backend field validation errors", async () => {
    vi.stubGlobal(
      "fetch",
      mockResponse(422, {
        detail: [{ loc: ["body", "region"], msg: "Region is not permitted" }],
      }),
    );
    render(<CreateServerForm />);
    await fillAndSubmit();
    expect(await screen.findByText("Region is not permitted")).toBeVisible();
    expect(screen.getByLabelText("AWS region")).toHaveAttribute(
      "aria-invalid",
      "true",
    );
  });

  it("disables submission while pending and avoids a second request", async () => {
    let resolve!: (value: Response) => void;
    const fetch = vi.fn(
      () =>
        new Promise<Response>((done) => {
          resolve = done;
        }),
    );
    vi.stubGlobal("fetch", fetch);
    render(<CreateServerForm />);
    const user = await fillAndSubmit();
    const button = screen.getByRole("button", { name: /Saving request/ });
    expect(button).toBeDisabled();
    await user.click(button);
    expect(fetch).toHaveBeenCalledOnce();
    resolve(new Response(JSON.stringify(saved), { status: 202 }));
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: /Create server request/ }),
      ).toBeEnabled(),
    );
  });

  it("shows connection failures and allows retry", async () => {
    const fetch = vi
      .fn()
      .mockRejectedValueOnce(new TypeError("offline"))
      .mockResolvedValueOnce(
        new Response(JSON.stringify(saved), { status: 202 }),
      );
    vi.stubGlobal("fetch", fetch);
    render(<CreateServerForm />);
    const user = await fillAndSubmit();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Could not reach",
    );
    await user.click(
      screen.getByRole("button", { name: /Create server request/ }),
    );
    expect(await screen.findByText(saved.id)).toBeVisible();
  });

  it("handles non-JSON server failures", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("unavailable", { status: 500 })),
    );
    render(<CreateServerForm />);
    await fillAndSubmit();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "could not be confirmed",
    );
  });

  it("requests a plan, shows its summary, and keeps apply and destroy disabled", async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(saved), { status: 202 }))
      .mockResolvedValueOnce(
        new Response(JSON.stringify(queuedPlanJob), { status: 202 }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify(readyPlanJob), { status: 200 }),
      );
    vi.stubGlobal("fetch", fetch);
    render(<CreateServerForm />);
    const user = await fillAndSubmit();
    await user.click(
      await screen.findByRole("button", { name: "Generate Terraform plan" }),
    );

    expect(await screen.findByText("plan_ready", {}, { timeout: 4000 })).toBeVisible();
    expect(
      screen.getByText(/terraform_data\.server_configuration/),
    ).toBeVisible();
    expect(screen.getByText("Add").parentElement).toHaveTextContent("1");
    expect(
      screen.getByRole("button", { name: "Apply disabled" }),
    ).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "Destroy disabled" }),
    ).toBeDisabled();
    expect(fetch).toHaveBeenNthCalledWith(
      2,
      `http://127.0.0.1:8000/api/v1/resources/${saved.id}/plan`,
      expect.objectContaining({ method: "POST" }),
    );
    expect(fetch).toHaveBeenNthCalledWith(
      3,
      `http://127.0.0.1:8000/api/v1/jobs/${queuedPlanJob.id}`,
      expect.any(Object),
    );
  });
});
