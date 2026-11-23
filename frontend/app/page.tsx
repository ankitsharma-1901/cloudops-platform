import Link from "next/link";
import PortalShell from "../components/portal-shell";
import { getPortalUser } from "../lib/portal-user";

export const dynamic = "force-dynamic";
export default async function Home() {
  const user = await getPortalUser();
  return <PortalShell title="Dashboard">
    <p className="my-5 text-slate-600">Create infrastructure, monitor the EC2 demo and review activity.</p>
    {!user && <p>Sign in to access your workspace.</p>}
    {user && user.roles.length === 0 && <p>No CloudOps role is assigned. Ask your platform administrator for access.</p>}
    <div className="grid gap-4 sm:grid-cols-2">
      {user?.roles.includes("engineer") && <>
        <Link className="rounded-xl border border-teal-200 bg-white p-6 text-teal-800" href="/requests/new">Create Infrastructure <span className="mt-2 block text-sm text-slate-600">Review the supported EC2 demo or explore Terraform planning.</span></Link>
        <Link className="rounded-xl border bg-white p-6 text-teal-800" href="/infrastructure">Infrastructure <span className="mt-2 block text-sm text-slate-600">Manage the direct API demo and view EC2 inventory.</span></Link>
        <Link className="rounded-xl border bg-white p-6 text-teal-800" href="/requests">Activity <span className="mt-2 block text-sm text-slate-600">Review saved requests and plan history.</span></Link>
      </>}
      {user?.roles.includes("approver") && <Link className="rounded-xl border bg-white p-6 text-teal-800" href="/approvals">Approvals</Link>}
    </div>
  </PortalShell>;
}
