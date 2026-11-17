import { NextRequest, NextResponse } from "next/server";
import { logoutConfiguration } from "../../../../lib/oidc-logout";

export async function GET(request: NextRequest) {
  let config: ReturnType<typeof logoutConfiguration>;
  try { config = logoutConfiguration(); }
  catch { return new Response("Logout configuration is unavailable.", { status: 503 }); }
  const state = request.nextUrl.searchParams.get("state");
  const expected = request.cookies.get("cloudops.logout-state")?.value;
  const valid = Boolean(state && expected && state === expected);
  const response = valid
    ? NextResponse.redirect(`${config.origin}/signed-out`, 303)
    : new NextResponse("The logout response could not be verified. The local session is signed out; provider logout is unconfirmed.", { status: 400 });
  response.cookies.set("cloudops.logout-state", "", {
    path: "/auth/logout/callback", maxAge: 0, httpOnly: true,
    sameSite: "lax", secure: config.secure,
  });
  response.headers.set("Cache-Control", "no-store");
  response.headers.set("Referrer-Policy", "no-referrer");
  return response;
}
