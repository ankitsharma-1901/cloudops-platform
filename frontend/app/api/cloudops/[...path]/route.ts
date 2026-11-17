import { getToken } from "next-auth/jwt";
import type { NextRequest } from "next/server";
import { authConfigured } from "../../../../auth";

const backendUrl = (process.env.CLOUDOPS_API_URL || "http://127.0.0.1:8000").replace(
  /\/$/,
  "",
);

async function proxy(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> },
) {
  if (!authConfigured || !process.env.AUTH_SECRET) {
    return Response.json(
      { detail: "OIDC authentication is not configured." },
      { status: 503, headers: { "Cache-Control": "no-store" } },
    );
  }
  const token = await getToken({
    req: request,
    secret: process.env.AUTH_SECRET,
    secureCookie: new URL(process.env.AUTH_URL || request.nextUrl.origin).protocol === "https:",
  });
  if (
    !token ||
    typeof token.accessToken !== "string" ||
    typeof token.accessTokenExpiresAt !== "number" ||
    token.accessTokenExpiresAt <= Math.floor(Date.now() / 1000)
  ) {
    return Response.json(
      { detail: "Authentication required or session expired." },
      { status: 401, headers: { "Cache-Control": "no-store" } },
    );
  }
  const { path } = await context.params;
  if (
    path[0] !== "api" ||
    path[1] !== "v1" ||
    path.some((segment) => !/^[A-Za-z0-9_-]+$/.test(segment))
  ) {
    return Response.json({ detail: "Unsupported API path." }, { status: 404, headers: { "Cache-Control": "no-store" } });
  }
  const target = `${backendUrl}/${path.map(encodeURIComponent).join("/")}${request.nextUrl.search}`;
  const body = request.method === "GET" ? undefined : await request.arrayBuffer();
  let response: Response;
  try {
    response = await fetch(target, {
      method: request.method,
      headers: {
        Authorization: `Bearer ${token.accessToken}`,
        ...(request.headers.get("content-type")
          ? { "Content-Type": request.headers.get("content-type")! }
          : {}),
      },
      body,
      cache: "no-store",
      redirect: "manual",
      signal: AbortSignal.timeout(20000),
    });
  } catch {
    return Response.json({ detail: "The CloudOps API is unavailable." }, {
      status: 502, headers: { "Cache-Control": "no-store" },
    });
  }
  return new Response(response.body, {
    status: response.status,
    headers: {
      "Content-Type": response.headers.get("content-type") || "application/json",
      "Cache-Control": "no-store",
      ...(response.headers.has("www-authenticate") ? { "WWW-Authenticate": response.headers.get("www-authenticate")! } : {}),
    },
  });
}

export const GET = proxy;
export const POST = proxy;
