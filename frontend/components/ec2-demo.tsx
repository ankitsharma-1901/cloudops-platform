"use client";

import { useCallback, useEffect, useState } from "react";

type Demo = { enabled: boolean; instance: null | { id: string | null; status: string; message: string | null; region: string; aws_state?: string; instance_type?: string; ami?: string; subnet?: string; security_group?: string } };

export default function EC2Demo() {
  const [demo, setDemo] = useState<Demo | null>(null);
  const [error, setError] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [busy, setBusy] = useState(false);
  const refresh = useCallback(async () => {
    try {
      const response = await fetch("/api/cloudops/api/v1/ec2-demo", { cache: "no-store" });
      if (!response.ok) throw new Error("Demo status could not be loaded.");
      setDemo(await response.json());
    } catch { setError("Demo status could not be loaded."); }
  }, []);
  useEffect(() => { void refresh(); }, [refresh]);
  async function action(name: "create" | "terminate") {
    setBusy(true); setError("");
    try {
      const response = await fetch(`/api/cloudops/api/v1/ec2-demo/${name}`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ confirmation }),
      });
      if (!response.ok) { const body = await response.json(); throw new Error(body.detail || "Operation failed."); }
      setConfirmation("");
    } catch (failure) { setError(failure instanceof Error ? failure.message : "Operation failed."); }
    finally { await refresh(); setBusy(false); }
  }
  const instance = demo?.instance;
  const expected = instance?.id ? `TERMINATE ${instance.id}` : "CREATE ONE EC2 DEMO INSTANCE";
  return <section className="my-8 rounded-xl border border-amber-300 bg-amber-50 p-5" aria-label="AWS EC2 Demo Mode">
    <div className="flex justify-between gap-3"><div><h2 className="text-xl font-semibold">AWS EC2 Demo Mode</h2>
      <p className="mt-1 text-sm">Direct AWS API demo; separate from Terraform plans and approvals. Creating an instance incurs AWS charges.</p></div>
      <button className="rounded border bg-white px-3 py-2 text-sm" onClick={() => void refresh()}>Refresh</button></div>
    {!demo && !error && <p role="status">Loading demo status...</p>}
    {demo && !demo.enabled && <p className="mt-3">Demo mode is disabled by the operator.</p>}
    {instance && <div className="mt-3 text-sm"><p>Instance: {instance.id || "Not confirmed"}</p><p>Operation: {instance.status} · AWS state: {instance.aws_state || "not verified"}</p><p>{instance.region} · {instance.instance_type || "t3.micro requested"}</p>{instance.ami && <p>AMI: {instance.ami} · Subnet: {instance.subnet} · Security group: {instance.security_group}</p>}{instance.message && <p role="alert">{instance.message}</p>}</div>}
    {instance?.status === "needs_operator_review" && <p role="alert">An operator must reconcile the AWS result before any further operation.</p>}
    {demo?.enabled && (!instance || (instance.id && instance.status === "created")) && <div className="mt-4">
      <label className="block text-sm" htmlFor="demo-confirm">Type <strong>{expected}</strong> to confirm</label>
      <input id="demo-confirm" className="mt-2 w-full rounded border p-2" value={confirmation} onChange={(event) => setConfirmation(event.target.value)} />
      <button className="mt-2 rounded bg-slate-900 px-4 py-2 text-white disabled:opacity-40" disabled={busy || confirmation !== expected} onClick={() => void action(instance ? "terminate" : "create")}>{busy ? "Working..." : instance ? "Terminate this demo instance" : "Create one demo instance"}</button>
    </div>}
    {error && <p role="alert" className="mt-3 text-red-800">{error}</p>}
  </section>;
}
