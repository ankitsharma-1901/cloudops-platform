// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { encode } from "next-auth/jwt";
import { GET } from "../app/api/cloudops/[...path]/route";
import { ApiError, getProvisioningJob } from "../lib/api";

vi.mock("../auth", () => ({ authConfigured: true }));
afterEach(() => { vi.unstubAllEnvs(); });
const secret = "isolated-test-secret-with-at-least-32-characters";
const job = { id: "job-1", server_request_id: "request-1", status: "queued", plan_mode: "local",
  plan_summary: null, plan_revision: null, error_message: null };
const context = { params: Promise.resolve({ path: ["api", "v1", "jobs", "job-1"] }) };

async function request(expired = false, chunked = false) {
  vi.stubEnv("AUTH_SECRET", secret); vi.stubEnv("AUTH_URL", "http://localhost:3000");
  const cookie = await encode({ secret, salt: "authjs.session-token", token: {
    accessToken: "isolated-test-api-token", accessTokenExpiresAt: Math.floor(Date.now() / 1000) + (expired ? -1 : 300),
  } });
  const midpoint = Math.floor(cookie.length / 2);
  const header = chunked ? `authjs.session-token.0=${cookie.slice(0, midpoint)}; authjs.session-token.1=${cookie.slice(midpoint)}` : `authjs.session-token=${cookie}`;
  return new NextRequest("http://localhost:3000/api/cloudops/api/v1/jobs/job-1", { headers: { Cookie: header } });
}

describe("authenticated status BFF", () => {
  it.each([false, true])("forwards the server token from encrypted cookies (chunked=%s)", async (chunked) => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify(job)));
    vi.stubGlobal("fetch", fetch);
    const response = await GET(await request(false, chunked), context);
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual(job);
    expect(fetch).toHaveBeenCalledWith("http://127.0.0.1:8000/api/v1/jobs/job-1", expect.objectContaining({
      headers: { Authorization: "Bearer isolated-test-api-token" }, cache: "no-store", redirect: "manual",
    }));
    expect(response.headers.get("cache-control")).toBe("no-store");
  });

  it("rejects expired or missing sessions without calling FastAPI", async () => {
    const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
    expect((await GET(await request(true), context)).status).toBe(401);
    expect((await GET(new NextRequest("http://localhost:3000/api/cloudops/api/v1/jobs/job-1"), context)).status).toBe(401);
    expect(fetch).not.toHaveBeenCalled();
  });

  it("returns a safe gateway error when FastAPI is unreachable", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("secret-upstream-diagnostic")));
    const response = await GET(await request(), context);
    expect(response.status).toBe(502);
    expect(await response.text()).not.toContain("secret-upstream-diagnostic");
  });

  it.each([401, 403, 404, 500, 503])("preserves API status %s rather than misreporting a worker failure", async (status) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "safe error" }), { status })));
    expect((await GET(await request(), context)).status).toBe(status);
  });

  it.each([401, 403, 404, 500])("classifies frontend polling status %s", async (status) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("{}", { status })));
    const failure = await getProvisioningJob("job-1").catch((error: unknown) => error);
    expect(failure).toBeInstanceOf(ApiError);
    if (failure instanceof ApiError) expect(failure.status).toBe(status);
  });

  it("reads the normalized legacy response without fabricating a plan revision", async () => {
    const legacy = { ...job, status: "plan_ready", plan_summary: {
      plan_mode: "local", plan_revision: null, resource: "terraform_data.server_configuration",
      resource_changes: [], to_add: 1, to_change: 0, to_destroy: 0,
    } };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(legacy))));
    expect(await getProvisioningJob("job-1")).toEqual(legacy);
  });
});
