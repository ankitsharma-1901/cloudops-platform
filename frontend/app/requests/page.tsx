import RequestHistory from "../../components/request-history";
import PortalShell, { PortalAccessNotice } from "../../components/portal-shell";
import { getPortalUser } from "../../lib/portal-user";
import Link from "next/link";
import EC2Activity from "../../components/ec2-activity";
import EC2RequestList from "../../components/ec2-request-list";

export const dynamic = "force-dynamic";
export default async function Requests() {
  const user = await getPortalUser();
  return <PortalShell title="Activity">
    {user?.roles.includes("engineer") ? <><EC2RequestList mode="engineer" currentSubject={user.id} /><EC2Activity /><p className="my-4 text-sm text-slate-600">The history below contains infrastructure requests and plans. The direct AWS EC2 demo is managed on <Link className="text-teal-800 underline" href="/infrastructure#ec2-demo">Infrastructure</Link>.</p><RequestHistory key={user.id} currentUser={user} /></> : <PortalAccessNotice />}
  </PortalShell>;
}
