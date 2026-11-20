import Link from "next/link";

export default function PortalNavigation({ roles }: { roles: string[] }) {
  return <nav aria-label="CloudOps navigation" className="mt-8 flex flex-col gap-4 text-sm">
    <Link href="/">Dashboard</Link>
    {roles.includes("engineer") && <>
      <Link href="/infrastructure">Infrastructure</Link>
      <Link href="/requests/new">New Infrastructure Request</Link>
      <Link href="/requests">Requests / History</Link>
    </>}
    {roles.includes("approver") && <Link href="/approvals">Approvals</Link>}
  </nav>;
}
