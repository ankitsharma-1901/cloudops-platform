"use server";

import { randomUUID } from "node:crypto";
import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { signOut } from "../../auth";
import { keycloakLogoutUrl, logoutConfiguration } from "../../lib/oidc-logout";

export async function logout() {
  // Next.js Server Actions validate the request origin and accept POST only.
  // Clear Auth.js (including chunked session cookies) even if OIDC is misconfigured.
  await signOut({ redirect: false, redirectTo: "/" });
  let config: ReturnType<typeof logoutConfiguration>;
  try { config = logoutConfiguration(); }
  catch { redirect("/signed-out?provider=unavailable"); }
  const state = randomUUID();
  const jar = await cookies();
  jar.set("cloudops.logout-state", state, {
    httpOnly: true, sameSite: "lax", secure: config.secure,
    path: "/auth/logout/callback", maxAge: 300,
  });
  redirect(keycloakLogoutUrl(config, state));
}
