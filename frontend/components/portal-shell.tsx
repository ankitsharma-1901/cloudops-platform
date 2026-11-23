import Link from "next/link";
import { authConfigured } from "../auth";
import { getPortalUser } from "../lib/portal-user";
import AuthControls from "./auth-controls";
import PortalNavigation from "./portal-navigation";

export default async function PortalShell({ title, children }: {
  title: string; children: React.ReactNode;
}) {
  const user = await getPortalUser();
  return <div className="min-h-screen lg:grid lg:grid-cols-[230px_1fr]">
    <aside className="bg-slate-950 px-7 py-7 text-white">
      <Link className="text-xl font-bold" href="/">CloudOps<span className="text-teal-400">.</span></Link>
      <PortalNavigation roles={user?.roles ?? []} />
    </aside>
    <div className="min-w-0">
      <header className="flex flex-wrap items-center justify-between gap-4 border-b bg-white px-5 py-5 sm:px-6">
        <p className="text-sm text-slate-600">Workspace / {title}</p>
        <AuthControls configured={authConfigured} user={user} />
      </header>
      <main className="mx-auto max-w-6xl px-5 py-9 sm:px-8">
        <h1 className="text-3xl font-semibold">{title}</h1>
        {children}
        <footer className="mt-8 border-t pt-5 text-xs text-slate-500">CloudOps · Plan and review infrastructure safely. Approval decisions do not execute Terraform.</footer>
      </main>
    </div>
  </div>;
}

export function PortalAccessNotice() {
  return <p role="alert" className="my-6">Sign in with the required CloudOps role to access this screen.</p>;
}
