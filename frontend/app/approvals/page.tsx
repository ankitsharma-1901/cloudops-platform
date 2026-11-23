import ApprovalQueue from "../../components/approval-queue";
import PortalShell, { PortalAccessNotice } from "../../components/portal-shell";
import { getPortalUser } from "../../lib/portal-user";
import EC2RequestList from "../../components/ec2-request-list";

export const dynamic = "force-dynamic";
export default async function Approvals() {
  const user = await getPortalUser();
  return <PortalShell title="Approvals">
    {user?.roles.includes("approver") ? <><EC2RequestList mode="approver" currentSubject={user.id} /><ApprovalQueue key={user.id} currentSubject={user.id} /></> : <PortalAccessNotice />}
  </PortalShell>;
}
