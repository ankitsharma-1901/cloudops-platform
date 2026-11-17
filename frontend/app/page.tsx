import Link from "next/link";
import PortalShell from "../components/portal-shell";
import { getPortalUser } from "../lib/portal-user";

export const dynamic = "force-dynamic";
export default async function Home() {
  const user = await getPortalUser();
  return <PortalShell title="Dashboard">
    <p className="my-5 text-slate-600">Manage infrastructure requests, inspect sanitized plans and review approval history.</p>
    {!user && <p>Sign in to access your workspace.</p>}
    {user && user.roles.length === 0 && <p>No CloudOps role is assigned. Ask your platform administrator for access.</p>}
    <div className="grid gap-4 sm:grid-cols-2">
      {user?.roles.includes("engineer") && <>
        <Link className="rounded-xl border bg-white p-6 text-teal-800" href="/requests/new">New Infrastructure Request</Link>
        <Link className="rounded-xl border bg-white p-6 text-teal-800" href="/requests">Requests / History</Link>
      </>}
      {user?.roles.includes("approver") && <Link className="rounded-xl border bg-white p-6 text-teal-800" href="/approvals">Approvals</Link>}
    </div>
  </PortalShell>;
}
