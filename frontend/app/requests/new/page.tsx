import CreateServerForm from "../../../components/create-server-form";
import PortalShell, { PortalAccessNotice } from "../../../components/portal-shell";
import { getPortalUser } from "../../../lib/portal-user";
import Link from "next/link";
import EC2Configure from "../../../components/ec2-configure";

export const dynamic = "force-dynamic";
export default async function NewRequest() {
  const user = await getPortalUser();
  return <PortalShell title="Create Infrastructure">
    {user?.roles.includes("engineer") ? <><EC2Configure /><details className="my-5 rounded-xl border bg-white p-5"><summary className="cursor-pointer font-semibold">Terraform requests and planning · unfinished</summary><p className="mt-3 text-sm text-slate-600">This separate workflow records requests and generates plans. EC2-only provisioning remains blocked by its plan-policy checks.</p><CreateServerForm key={user.id} currentUser={user} /></details></> : <PortalAccessNotice />}
  </PortalShell>;
}
