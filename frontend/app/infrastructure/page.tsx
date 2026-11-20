import Infrastructure from "../../components/infrastructure";
import EC2Demo from "../../components/ec2-demo";
import PortalShell, { PortalAccessNotice } from "../../components/portal-shell";
import { getPortalUser } from "../../lib/portal-user";

export const dynamic = "force-dynamic";
export default async function InfrastructurePage() {
  const user = await getPortalUser();
  return <PortalShell title="Infrastructure">
    {user?.roles.includes("engineer") ? <><EC2Demo /><Infrastructure /></> : <PortalAccessNotice />}
  </PortalShell>;
}
