import { RequestHistoryDetail } from "../../../components/request-history";
import PortalShell, { PortalAccessNotice } from "../../../components/portal-shell";
import { getPortalUser } from "../../../lib/portal-user";

export const dynamic = "force-dynamic";
export default async function RequestDetails({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const user = await getPortalUser();
  return <PortalShell title="Request details">
    {user && (user.roles.includes("engineer") || user.roles.includes("approver"))
      ? <RequestHistoryDetail key={`${user.id}-${id}`} requestId={id} canEngineer={user.roles.includes("engineer")}
          reviewerSubject={user.roles.includes("approver") ? user.id : undefined} />
      : <PortalAccessNotice />}
  </PortalShell>;
}
