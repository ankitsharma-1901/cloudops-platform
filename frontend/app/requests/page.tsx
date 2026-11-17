import RequestHistory from "../../components/request-history";
import PortalShell, { PortalAccessNotice } from "../../components/portal-shell";
import { getPortalUser } from "../../lib/portal-user";

export const dynamic = "force-dynamic";
export default async function Requests() {
  const user = await getPortalUser();
  return <PortalShell title="Requests / History">
    {user?.roles.includes("engineer") ? <RequestHistory key={user.id} currentUser={user} /> : <PortalAccessNotice />}
  </PortalShell>;
}
