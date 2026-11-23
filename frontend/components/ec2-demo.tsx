"use client";

import { useCallback, useEffect, useRef, useState } from "react";

type Demo = { enabled: boolean; history?: { id: string; name?: string; status: string; created_at: string; completed_at: string }[]; instance: null | { id: string | null; status: string; message: string | null; region: string; aws_state?: string; instance_type?: string; ami?: string; subnet?: string; security_group?: string; name?: string | null; vpc?: string; private_ip?: string; volumes?: { device: string | null; id: string }[] } };

export default function EC2Demo() {
  const [demo, setDemo] = useState<Demo | null>(null);
  const [error, setError] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [busy, setBusy] = useState(false);
  const [review, setReview] = useState(false);
  const [notice, setNotice] = useState("");
  const lastStatus = useRef("");
  const refresh = useCallback(async (clearError = true) => {
    try {
      const response = await fetch("/api/cloudops/api/v1/ec2-demo", { cache: "no-store" });
      if (response.status === 401) {
        setDemo(null);
        setError("Your session expired or authentication is required. Sign in again to view the demo.");
        return;
      }
      if (!response.ok) throw new Error("Demo status could not be loaded.");
      const next: Demo = await response.json();
      const status = `${next.instance?.id || ""}:${next.instance?.status || ""}:${next.instance?.aws_state || ""}`;
      if (lastStatus.current && lastStatus.current !== status) window.dispatchEvent(new Event("cloudops-demo-changed"));
      lastStatus.current = status;
      setDemo(next);
      if (clearError) setError("");
    } catch { setError("Demo status could not be loaded."); }
  }, []);
  useEffect(() => { void refresh(); }, [refresh]);
  useEffect(() => {
    if (!demo?.instance || !["creating", "terminating", "termination_submitted"].includes(demo.instance.status)) return;
    const timer = window.setInterval(() => void refresh(false), 5000);
    return () => window.clearInterval(timer);
  }, [demo?.instance?.status, refresh]);
  async function action(name: "create" | "terminate" | "complete") {
    setBusy(true); setError(""); setNotice("");
    try {
      const response = await fetch(`/api/cloudops/api/v1/ec2-demo/${name}`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ confirmation: name === "complete" ? `COMPLETE ${demo?.instance?.id}` : confirmation }),
      });
      if (response.status === 401) { setDemo(null); throw new Error("Your session expired or authentication is required. Sign in again to view the demo."); }
      if (!response.ok) { const body = await response.json(); throw new Error(body.detail || "Operation failed."); }
      setConfirmation("");
      setReview(false);
      setNotice(name === "create" ? "Create request accepted. Refresh to check the AWS state." : name === "terminate" ? "Termination request accepted. Waiting for AWS to report termination." : "Lifecycle completed and saved in history. You can review a new instance.");
      window.dispatchEvent(new Event("cloudops-demo-changed"));
    } catch (failure) { setError(failure instanceof Error ? failure.message : "Operation failed."); }
    finally { await refresh(false); setBusy(false); }
  }
  const instance = demo?.instance;
  const expected = instance?.id ? `TERMINATE ${instance.id}` : "CREATE ONE EC2 DEMO INSTANCE";
  return <section id="ec2-demo" className="my-8 rounded-xl border border-teal-200 bg-white p-6 shadow-sm" aria-label="AWS EC2 Demo Mode">
    <div className="flex justify-between gap-3"><div><p className="text-xs font-semibold uppercase tracking-wide text-teal-700">Direct AWS API · single instance</p><h2 className="mt-1 text-xl font-semibold">EC2 demo workspace</h2>
      <p className="mt-1 text-sm text-slate-600">Review the fixed configuration, then create and manage one private EC2 instance. This is separate from Terraform plans and approvals. AWS charges apply.</p></div>
      <button aria-label="Refresh EC2 demo" className="rounded border bg-white px-3 py-2 text-sm" onClick={() => void refresh()}>Refresh</button></div>
    {!demo && !error && <p role="status">Loading demo status...</p>}
    {demo && !demo.enabled && <p className="mt-3">Demo mode is disabled by the operator.</p>}
    {demo?.enabled && !instance && <p className="mt-3 text-sm text-amber-800">Direct EC2 creation is blocked until the approval-controlled worker is implemented. You can submit an approval request from Create Infrastructure.</p>}
    {instance && <div className="mt-5 rounded-lg border bg-slate-50 p-4 text-sm"><h3 className="font-semibold">Instance details</h3><p className="mt-2">Instance: {instance.id || "Not confirmed"}</p><p>Name: {instance.name || "Unavailable"}</p><p>Operation: {instance.status} · AWS state: {instance.aws_state || (instance.status === "terminated" ? "terminated (last confirmed)" : "not verified")}</p><p>Region: {instance.region} · Type: {instance.instance_type || "not verified"}</p><p>AMI: {instance.ami || "Unavailable"}</p><p>VPC: {instance.vpc || "Unavailable"} · Subnet: {instance.subnet || "Unavailable"}</p><p>Security group: {instance.security_group || "Unavailable"} · Private IP: {instance.private_ip || "Unavailable"}</p><p>Attached storage: {instance.volumes?.length ? instance.volumes.map((volume) => `${volume.device || "device"}: ${volume.id}`).join(", ") : "Unavailable"}</p>{instance.message && <p role="alert">{instance.message}</p>}</div>}
    {instance?.status === "needs_operator_review" && <p role="alert">An operator must reconcile the AWS result before any further operation.</p>}
    {demo?.enabled && !instance && !review && <button className="mt-5 rounded bg-teal-800 px-4 py-2 text-white" onClick={() => setReview(true)}>Review EC2 configuration</button>}
    {demo?.enabled && !instance && review && <div className="mt-5 rounded-lg border border-teal-200 bg-teal-50 p-4"><h3 className="font-semibold">Fixed legacy configuration</h3><p className="mt-1 text-sm">This compatibility path uses the existing fixed settings. For the current catalog and a custom name, use <a className="text-teal-800 underline" href="/requests/new">Create Infrastructure</a>. No software installation is included.</p></div>}
    {(instance?.status === "terminating" || instance?.status === "termination_submitted") && <p className="mt-4 text-sm" role="status">Waiting for AWS to confirm termination. Status refreshes automatically.</p>}
    {instance?.status === "terminated" && <p className="mt-4 text-sm" role="status">AWS termination was confirmed. Complete this lifecycle to retain it in history and allow a new request.</p>}
    {demo?.enabled && instance?.id && (instance.status === "terminated" || (instance.status === "created" && instance.aws_state === "terminated")) && <button disabled={busy} className="mt-3 rounded border border-teal-700 px-4 py-2 text-teal-800 disabled:opacity-40" onClick={() => void action("complete")}>Verify and complete lifecycle</button>}
    {notice && <p className="mt-4 text-sm text-teal-800" role="status">{notice}</p>}
    {demo?.enabled && ((!instance && review) || (instance?.id && instance.status === "created" && instance.aws_state !== "terminated")) && <div className="mt-4">
      {instance && <p className="mb-2 text-sm">Termination permanently removes this exact instance. Verify its ID before confirming.</p>}
      <label className="block text-sm" htmlFor="demo-confirm">Type <strong>{expected}</strong> to confirm</label>
      <input id="demo-confirm" className="mt-2 w-full rounded border p-2" value={confirmation} onChange={(event) => setConfirmation(event.target.value)} />
      <button className="mt-2 rounded bg-slate-900 px-4 py-2 text-white disabled:opacity-40" disabled={busy || confirmation !== expected} onClick={() => void action(instance ? "terminate" : "create")}>{busy ? "Working..." : instance ? "Terminate this demo instance" : "Create one demo instance"}</button>
    </div>}
    {error && <p role="alert" className="mt-3 text-red-800">{error}</p>}
    {!!demo?.history?.length && <div className="mt-6 border-t pt-4"><h3 className="font-semibold">Completed demo history</h3><ul className="mt-2 space-y-2 text-sm">{demo.history.map((item) => <li key={`${item.id}-${item.completed_at}`} className="rounded border bg-slate-50 p-3">{item.name && <span>{item.name} · </span>}<span className="font-mono">{item.id}</span> · {item.status}<br />Created: {item.created_at} · Completed: {item.completed_at}</li>)}</ul></div>}
  </section>;
}
