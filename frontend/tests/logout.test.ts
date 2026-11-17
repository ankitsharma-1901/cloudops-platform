// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { keycloakLogoutUrl, logoutConfiguration } from "../lib/oidc-logout";
import { GET } from "../app/auth/logout/callback/route";
import { logout } from "../app/actions/logout";

const mocks = vi.hoisted(() => ({ signOut: vi.fn(), setCookie: vi.fn(), redirect: vi.fn((url: string) => { throw new Error(`redirect:${url}`); }) }));
vi.mock("../auth", () => ({ signOut: mocks.signOut }));
vi.mock("next/headers", () => ({ cookies: async () => ({ set: mocks.setCookie }) }));
vi.mock("next/navigation", () => ({ redirect: mocks.redirect }));
afterEach(() => { vi.unstubAllEnvs(); vi.clearAllMocks(); });

function configure() {
  vi.stubEnv("AUTH_URL", "http://localhost:3000");
  vi.stubEnv("AUTH_ISSUER", "http://127.0.0.1:8080/realms/cloudops");
  vi.stubEnv("AUTH_CLIENT_ID", "cloudops-web");
}

describe("RP-Initiated Logout", () => {
  it("builds an exact registered redirect without browser tokens", () => {
    configure();
    const url = new URL(keycloakLogoutUrl(logoutConfiguration(), "opaque-state"));
    expect(url.origin).toBe("http://127.0.0.1:8080");
    expect(url.pathname).toBe("/realms/cloudops/protocol/openid-connect/logout");
    expect(Object.fromEntries(url.searchParams)).toEqual({ client_id: "cloudops-web", post_logout_redirect_uri: "http://localhost:3000/auth/logout/callback", state: "opaque-state" });
    expect(url.searchParams.has("id_token_hint")).toBe(false);
  });

  it.each(["https://user:password@example.com", "http://untrusted.example", "https://app.example/?returnTo=https://evil.example", "javascript:alert(1)", "https://app.example/path"])('rejects invalid app configuration %s', (url) => {
    configure(); vi.stubEnv("AUTH_URL", url);
    expect(() => logoutConfiguration()).toThrow();
  });

  it("clears Auth.js through its server API before redirecting to Keycloak", async () => {
    configure();
    await expect(logout()).rejects.toThrow("redirect:http://127.0.0.1:8080");
    expect(mocks.signOut).toHaveBeenCalledWith({ redirect: false, redirectTo: "/" });
    expect(mocks.setCookie).toHaveBeenCalledWith("cloudops.logout-state", expect.any(String), expect.objectContaining({ httpOnly: true, sameSite: "lax", maxAge: 300, path: "/auth/logout/callback" }));
    expect(mocks.signOut.mock.invocationCallOrder[0]).toBeLessThan(mocks.redirect.mock.invocationCallOrder[0]);
  });

  it("clears the app session even if provider configuration is unavailable", async () => {
    configure(); vi.stubEnv("AUTH_ISSUER", "");
    await expect(logout()).rejects.toThrow("redirect:/signed-out?provider=unavailable");
    expect(mocks.signOut).toHaveBeenCalled();
    expect(mocks.setCookie).not.toHaveBeenCalled();
  });

  it("verifies state and ignores browser-supplied return URLs", async () => {
    configure();
    const response = await GET(new NextRequest("http://localhost:3000/auth/logout/callback?state=expected&returnTo=https://evil.example", {
      headers: { Cookie: "cloudops.logout-state=expected" },
    }));
    expect(response.status).toBe(303);
    expect(response.headers.get("location")).toBe("http://localhost:3000/signed-out");
    expect(response.headers.get("set-cookie")).toContain("Max-Age=0");
    expect(response.headers.get("cache-control")).toBe("no-store");
  });

  it.each(["wrong", ""])('rejects missing or mismatched state (%s)', async (state) => {
    configure();
    const response = await GET(new NextRequest(`http://localhost:3000/auth/logout/callback?state=${state}`, { headers: { Cookie: "cloudops.logout-state=expected" } }));
    expect(response.status).toBe(400);
    expect(response.headers.has("location")).toBe(false);
  });
});
