import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import ActualCreateServerForm from "../components/create-server-form";

const engineer = {
  id: "http://127.0.0.1:8080/realms/cloudops|engineer-id",
  roles: ["engineer"],
  name: "Engineer",
};

function CreateServerForm() {
  return <ActualCreateServerForm currentUser={engineer} />;
}

const saved = {
  id: "a60332dc-a9ca-4bae-9711-97500a9da3a5",
  status: "queued",
  created_at: "2026-11-12T00:00:00Z",
};
const queuedPlanJob = {
  id: "d3fcf4f4-fbf5-4db4-b3bc-bae704c188f5",
  server_request_id: saved.id,
  plan_mode: "local",
  status: "queued",
  plan_summary: null,
  plan_revision: null,
  error_message: null,
};
const readyPlanJob = {
  ...queuedPlanJob,
  status: "plan_ready",
  plan_revision: "a".repeat(64),
  plan_summary: {
    plan_mode: "local",
    plan_revision: "a".repeat(64),
    resource: "terraform_data.server_configuration",
    resource_changes: [
      {
        address: "terraform_data.server_configuration",
        actions: ["create"],
      },
    ],
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
  it("stops polling when API authorization expires instead of retrying forever", async () => {
    const fetch = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(saved), { status: 202 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(queuedPlanJob), { status: 202 }))
      .mockResolvedValue(new Response("{}", { status: 401 }));
    vi.stubGlobal("fetch", fetch);
    render(<CreateServerForm />);
    const user = await fillAndSubmit();
    await user.click(await screen.findByRole("button", { name: "Generate local plan" }));
    expect(await screen.findByText(/Your session expired or the API token was rejected/, {}, { timeout: 4000 })).toBeVisible();
    await new Promise((resolve) => setTimeout(resolve, 3200));
    expect(fetch).toHaveBeenCalledTimes(3);
  }, 10000);
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
    expect(url).toBe("/api/cloudops/api/v1/resources");
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
      await screen.findByRole("button", { name: "Generate local plan" }),
    );

    expect(await screen.findByText("plan_ready", {}, { timeout: 4000 })).toBeVisible();
    expect(
      screen.getAllByText(/terraform_data\.server_configuration/).length,
    ).toBeGreaterThan(0);
    expect(screen.getByText("Add").parentElement).toHaveTextContent("1");
    expect(
      screen.getByRole("button", { name: "Apply disabled" }),
    ).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "Destroy disabled" }),
    ).toBeDisabled();
    expect(fetch).toHaveBeenNthCalledWith(
      2,
      `/api/cloudops/api/v1/resources/${saved.id}/plan`,
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ plan_mode: "local" }),
      }),
    );
    expect(fetch).toHaveBeenNthCalledWith(
      3,
      `/api/cloudops/api/v1/jobs/${queuedPlanJob.id}`,
      expect.any(Object),
    );
  });

  it("clearly requests and displays the fixed AWS development plan mode", async () => {
    const awsQueued = { ...queuedPlanJob, plan_mode: "aws_development" };
    const awsReady = {
      ...awsQueued,
      status: "plan_ready",
      plan_revision: "b".repeat(64),
      plan_summary: {
        plan_mode: "aws_development",
        plan_revision: "b".repeat(64),
        resource: null,
        resource_changes: [
          { address: "aws_vpc.main", actions: ["create"] },
        ],
        to_add: 1,
        to_change: 0,
        to_destroy: 0,
      },
    };
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(saved), { status: 202 }))
      .mockResolvedValueOnce(
        new Response(JSON.stringify(awsQueued), { status: 202 }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify(awsReady), { status: 200 }),
      );
    vi.stubGlobal("fetch", fetch);
    render(<CreateServerForm />);
    const user = await fillAndSubmit();
    await user.click(
      await screen.findByRole("button", {
        name: "Generate legacy AWS network and EC2 plan",
      }),
    );

    expect(
      await screen.findByText("aws_vpc.main", {}, { timeout: 4000 }),
    ).toBeVisible();
    expect(screen.getByText("AWS development")).toBeVisible();
    expect(screen.getByText(/raw plan output are not exposed/)).toBeVisible();
    expect(fetch).toHaveBeenNthCalledWith(
      2,
      `/api/cloudops/api/v1/resources/${saved.id}/plan`,
      expect.objectContaining({
        body: JSON.stringify({ plan_mode: "aws_development" }),
      }),
    );
  });

  it("requests the isolated EC2-only plan and displays its stack", async () => {
    const queued = { ...queuedPlanJob, plan_mode: "aws_ec2_only" };
    const ready = { ...queued, status: "plan_ready", plan_revision: "c".repeat(64), plan_summary: {
      plan_mode: "aws_ec2_only", plan_revision: "c".repeat(64), resource: null,
      resource_changes: [{ address: "aws_instance.dev", actions: ["create"] }],
      to_add: 1, to_change: 0, to_destroy: 0,
    } };
    const fetch = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(saved), { status: 202 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(queued), { status: 202 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(ready), { status: 200 }));
    vi.stubGlobal("fetch", fetch);
    render(<CreateServerForm />);
    const user = await fillAndSubmit();
    await user.click(await screen.findByRole("button", { name: "Generate AWS — EC2 Only plan" }));
    expect(await screen.findByText("aws_instance.dev", {}, { timeout: 4000 })).toBeVisible();
    expect(screen.getByText("AWS — EC2 Only")).toBeVisible();
    expect(fetch).toHaveBeenNthCalledWith(2, `/api/cloudops/api/v1/resources/${saved.id}/plan`,
      expect.objectContaining({ body: JSON.stringify({ plan_mode: "aws_ec2_only" }) }));
  });

  it("shows plan-bound approval status and immutable audit history", async () => {
    const approval = {
      id: "approval-1",
      server_request_id: saved.id,
      provisioning_job_id: readyPlanJob.id,
      plan_revision: "a".repeat(64),
      status: "pending",
      requested_by: "user:requester",
      requested_at: "2026-11-15T12:00:00Z",
      expires_at: "2026-11-15T12:30:00Z",
      decided_by: null,
      decided_at: null,
      decision_reason: null,
    };
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(saved), { status: 202 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(queuedPlanJob), { status: 202 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(readyPlanJob), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(approval), { status: 201 }))
      .mockResolvedValueOnce(new Response(JSON.stringify([
        {
          id: "event-1",
          approval_request_id: approval.id,
          event_type: "requested",
          actor_subject: "user:requester",
          occurred_at: approval.requested_at,
        },
      ]), { status: 200 }));
    vi.stubGlobal("fetch", fetch);
    render(<CreateServerForm />);
    const user = await fillAndSubmit();
    await user.click(await screen.findByRole("button", { name: "Generate local plan" }));
    await user.click(await screen.findByRole("button", { name: "Request approval" }, { timeout: 4000 }));

    expect(await screen.findByText("pending")).toBeVisible();
    expect(screen.getByLabelText("Approval audit history")).toHaveTextContent(
      "requested by user:requester",
    );
    expect(fetch.mock.calls[3][1]).not.toHaveProperty("body");
  });

  it("explains that approval authentication is disabled", async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(saved), { status: 202 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(queuedPlanJob), { status: 202 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(readyPlanJob), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        detail: "Approval authentication is not configured.",
      }), { status: 503 }));
    vi.stubGlobal("fetch", fetch);
    render(<CreateServerForm />);
    const user = await fillAndSubmit();
    await user.click(await screen.findByRole("button", { name: "Generate local plan" }));
    await user.click(await screen.findByRole("button", { name: "Request approval" }, { timeout: 4000 }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Approval authentication is not configured.",
    );
  });

  it("resets after success and permits a second request without reloading", async () => {
    const secondSaved = { ...saved, id: "second-request-id" };
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(saved), { status: 202 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(secondSaved), { status: 202 }));
    vi.stubGlobal("fetch", fetch);
    render(<CreateServerForm />);
    const user = await fillAndSubmit();
    expect(await screen.findByText(saved.id)).toBeVisible();

    await user.click(screen.getByRole("button", { name: "New Request / Reset" }));
    expect(screen.queryByText(saved.id)).not.toBeInTheDocument();
    expect(screen.getByLabelText(/Server name/)).toHaveValue("");
    await user.type(screen.getByLabelText(/Server name/), "web-dev-02");
    await user.click(screen.getByRole("button", { name: /Create server request/ }));
    expect(await screen.findByText(secondSaved.id)).toBeVisible();
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it("reset stops stale polling results from restoring the previous plan", async () => {
    let resolvePoll!: (response: Response) => void;
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(saved), { status: 202 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(queuedPlanJob), { status: 202 }))
      .mockImplementationOnce(
        () => new Promise<Response>((resolve) => { resolvePoll = resolve; }),
      );
    vi.stubGlobal("fetch", fetch);
    render(<CreateServerForm />);
    const user = await fillAndSubmit();
    await user.click(await screen.findByRole("button", { name: "Generate local plan" }));
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(3), { timeout: 4000 });

    await user.click(screen.getByRole("button", { name: "New Request / Reset" }));
    expect(screen.getByText(/previously queued or running plan job was not cancelled/)).toBeVisible();
    resolvePoll(new Response(JSON.stringify(readyPlanJob), { status: 200 }));
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(screen.queryByText("plan_ready")).not.toBeInTheDocument();
    expect(screen.getByLabelText(/Server name/)).toHaveValue("");
  });

  it("reset clears failure feedback", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("unavailable", { status: 500 })),
    );
    render(<CreateServerForm />);
    const user = await fillAndSubmit();
    expect(await screen.findByRole("alert")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "New Request / Reset" }));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByText(/Ready for a new request/)).toBeVisible();
  });

  it("disables request creation when the user lacks the engineer role", () => {
    render(<ActualCreateServerForm currentUser={{ id: "approver", roles: ["approver"] }} />);
    expect(screen.getByRole("button", { name: /Create server request/ })).toBeDisabled();
    expect(screen.getByText(/engineer role/)).toBeVisible();
  });
});
