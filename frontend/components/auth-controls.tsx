"use client";

import { signIn } from "next-auth/react";
import { logout } from "../app/actions/logout";

type Props = {
  configured: boolean;
  user: { id: string; roles: string[]; name?: string | null } | null;
};

export default function AuthControls({ configured, user }: Props) {
  if (!configured) {
    return (
      <span className="rounded-full border border-amber-200 bg-amber-50 px-3 py-1 text-xs text-amber-900">
        Authentication not configured
      </span>
    );
  }
  if (!user) {
    return (
      <button
        type="button"
        onClick={() => signIn("keycloak", { redirectTo: "/" })}
        className="rounded-lg bg-teal-700 px-4 py-2 text-sm font-semibold text-white"
      >
        Sign in
      </button>
    );
  }
  return (
    <div className="flex items-center gap-3 text-xs">
      <div className="text-right">
        <p className="font-semibold text-slate-800">{user.name || user.id}</p>
        <p className="text-slate-500">{user.roles.join(", ") || "No CloudOps role"}</p>
      </div>
      <form action={logout}><button
        type="submit"
        className="rounded-lg border border-slate-300 px-3 py-2 font-semibold"
      >
        Sign out
      </button></form>
    </div>
  );
}
