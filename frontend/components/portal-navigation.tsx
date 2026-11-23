import Link from "next/link";

export default function PortalNavigation({ roles }: { roles: string[] }) {
  return <nav aria-label="CloudOps navigation" className="mt-6 flex flex-wrap gap-x-5 gap-y-3 text-sm lg:mt-8 lg:flex-col lg:gap-4">
    <Link href="/">Dashboard</Link>
    {roles.includes("engineer") && <>
      <Link href="/requests/new">Create Infrastructure</Link>
      <Link href="/infrastructure">Infrastructure</Link>
      <Link href="/requests">Activity</Link>
    </>}
    {roles.includes("approver") && <Link href="/approvals">Approvals</Link>}
  </nav>;
}
