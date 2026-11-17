import ApprovalQueue from "../../components/approval-queue";
import PortalShell, { PortalAccessNotice } from "../../components/portal-shell";
import { getPortalUser } from "../../lib/portal-user";

export const dynamic = "force-dynamic";
export default async function Approvals() {
  const user = await getPortalUser();
  return <PortalShell title="Approvals">
    {user?.roles.includes("approver") ? <ApprovalQueue key={user.id} currentSubject={user.id} /> : <PortalAccessNotice />}
  </PortalShell>;
}
