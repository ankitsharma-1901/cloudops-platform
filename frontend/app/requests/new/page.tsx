import CreateServerForm from "../../../components/create-server-form";
import PortalShell, { PortalAccessNotice } from "../../../components/portal-shell";
import { getPortalUser } from "../../../lib/portal-user";

export const dynamic = "force-dynamic";
export default async function NewRequest() {
  const user = await getPortalUser();
  return <PortalShell title="New Infrastructure Request">
    {user?.roles.includes("engineer") ? <CreateServerForm key={user.id} currentUser={user} /> : <PortalAccessNotice />}
  </PortalShell>;
}
